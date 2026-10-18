"""Closed-candle pattern entries with isolated linear-futures exit policies.

A direction at close[t] may first enter at open[t+1]. Zero means no new entry;
it does not close a position. Opposite pattern signals (and, by default, opposite
closed higher-timeframe trends) close at the following open without reversing
in the same bar. ATR and rolling exit levels use completed candles only.

Funding at an open belongs to the position held immediately before that open;
a position newly opened at that timestamp is not charged. Exact funding events
inside a candle use a fixed funding-before-exit order because OHLC cannot reveal
their order; this can overstate a funding credit if the true exit was earlier.
Intrabar exits receive the candle close timestamp and an explicit
precision label. Mark liquidation has conservative priority over other exits
when both could occur in one candle. This uses a fixed maintenance rate, not
Binance's tiered liquidation calculation.
"""
from dataclasses import dataclass, field
import math

import numpy as np
import pandas as pd


EXIT_MODES = (
    'fixed_rr', 'atr_trailing', 'three_bar_breakout',
    'three_bar_trailing', 'time_exit', 'fixed_three_bar_target',
    'swing_percent',
)


@dataclass
class _Position:
    direction: int
    quantity: float
    entry: float
    margin: float
    allocated: float
    entry_fee: float
    entry_time: int
    entry_bar: int
    signal_time: int
    pattern: str
    account_before: float
    initial_stop: float
    stop: float
    target: float
    extreme: float
    funding: float = 0.
    position_id: int = 0
    initial_quantity: float = 0.
    entry_atr: float = 0.
    partial_credit: float = 0.
    partial_gross: float = 0.
    partial_fees: float = 0.
    partial_funding: float = 0.
    partial_realized_pnl: float = 0.
    partial_exit_count: int = 0
    last_scale_time: int = 0
    pending_exit: str = ''
    signal_price: float = math.nan
    entry_raw_price: float = math.nan
    risk_sizing: dict = field(default_factory=dict)
    risk_actions: set = field(default_factory=set)
    entry_volatility: float = 0.
    entry_quality: float = 0.


def _validate(data, signals, exit_mode, parameters):
    """验证K线连续性、标记价格、信号方向和执行参数范围。

    Args:
        data: 当前函数契约下的时间顺序行情/特征表。
        signals: 与信号K线对齐的-1/0/1方向及可选形态表。
        exit_mode: 预定义退出方式名称。
        parameters: 经过命令/配置层组织的当前任务或执行参数字典。

    Returns:
        无返回值；不满足执行契约时抛出ValueError。
    """
    if exit_mode not in EXIT_MODES:
        raise ValueError('Unknown exit_mode: ' + str(exit_mode))
    if not all(math.isfinite(float(value)) for value in parameters.values()):
        raise ValueError('Nonfinite execution parameter')
    p = parameters
    if not 1 <= p['leverage'] <= 125 or not 0 < p['margin_fraction'] <= 1:
        raise ValueError('Invalid leverage or margin_fraction')
    if not 0 <= p['maintenance_rate'] < 1 / p['leverage']:
        raise ValueError('Invalid maintenance_rate')
    if min(p['fee'], p['slip'], p['liquidation_fee']) < 0 or p['slip'] >= 1 or p['leverage'] * p['fee'] >= 1:
        raise ValueError('Invalid execution costs')
    if min(p['atr_stop'], p['reward_risk'], p['trailing_atr']) <= 0:
        raise ValueError('ATR distances and reward_risk must be positive')
    if p['max_holding_bars'] < 1 or int(p['max_holding_bars']) != p['max_holding_bars']:
        raise ValueError('max_holding_bars must be a positive integer')
    required = ['open_time', 'close_time', 'open', 'high', 'low', 'close',
                'mark_open', 'mark_high', 'mark_low', 'mark_close', 'atr']
    if not set(required).issubset(data.columns) or data.empty:
        raise ValueError('Missing or empty OHLC/mark/ATR data')
    market = data[required[:-1]].to_numpy(dtype=float)
    if not np.isfinite(market).all():
        raise ValueError('Nonfinite OHLC data')
    for prefix in ('', 'mark_'):
        candles = data[[prefix + name for name in ('open', 'high', 'low', 'close')]]
        o, h, l, c = (candles.iloc[:, index].to_numpy() for index in range(4))
        if (candles <= 0).any().any() or (h < np.maximum(o, c)).any() or (l > np.minimum(o, c)).any() or (h < l).any():
            raise ValueError('Invalid OHLC bounds')
    opens = data.open_time.to_numpy(dtype=float)
    closes = data.close_time.to_numpy(dtype=float)
    if (opens != np.floor(opens)).any() or (closes != np.floor(closes)).any():
        raise ValueError('Times must be integer milliseconds')
    duration = closes - opens + 1
    if (duration <= 0).any() or not np.all(duration == duration[0]):
        raise ValueError('Candles must have a common positive duration')
    if len(data) > 1 and not np.all(opens[1:] == closes[:-1] + 1):
        raise ValueError('Candles must be contiguous and chronological')
    if len(signals) != len(data) or not np.isin(signals.direction, [-1, 0, 1]).all():
        raise ValueError('Invalid direction signals')
    if 'higher_trend' in data and not np.isin(data.higher_trend, [-1, 0, 1]).all():
        raise ValueError('Invalid higher_trend')
    atr = data.atr.to_numpy(dtype=float)
    # Warm-up ATR may be NaN, but never creates a position.
    if np.isinf(atr).any() or (atr[np.isfinite(atr)] < 0).any():
        raise ValueError('ATR must be nonnegative or NaN during warm-up')


def _funding_table(events):
    """规范化并校验独立资金费事件的时间、费率与可选标记价格。

    Args:
        events: 已保存的事件账本或待规范化的资金费记录。

    Returns:
        按真实结算时间排序的资金费事件表。
    """
    if events is None or len(events) == 0:
        return pd.DataFrame(columns=['funding_time', 'rate', 'mark_price'])
    events = pd.DataFrame(events).copy().rename(columns={
        'fundingTime': 'funding_time', 'time': 'funding_time',
        'fundingRate': 'rate', 'markPrice': 'mark_price',
    })
    if not {'funding_time', 'rate'}.issubset(events.columns):
        raise ValueError('Funding needs funding_time and rate')
    if 'mark_price' not in events:
        events['mark_price'] = np.nan
    events = events[['funding_time', 'rate', 'mark_price']].astype(float)
    if not np.isfinite(events[['funding_time', 'rate']]).all().all():
        raise ValueError('Nonfinite funding event')
    if (events.funding_time != np.floor(events.funding_time)).any() or events.funding_time.duplicated().any():
        raise ValueError('Invalid or duplicate funding timestamp')
    known = events.mark_price.dropna()
    if not np.isfinite(known).all() or (known <= 0).any():
        raise ValueError('Invalid funding mark_price')
    return events.sort_values('funding_time').reset_index(drop=True)


def _execution_alignment(source, signals, execution_data, delay_ms):
    """把已收盘信号和已知风控信息映射到延迟后的实际可观察成交K线。

    Args:
        source: 已知来源元数据，或对齐函数中的源K线表。
        signals: 与信号K线对齐的-1/0/1方向及可选形态表。
        execution_data: 细周期实际可观察成交K线；不允许凭空构造细粒度价格。
        delay_ms: 决策收盘后的响应延迟毫秒数。

    Returns:
        执行数据与信号/ATR/大周期/三根前高低点的对齐信息。

    Notes:
        原有细则保留如下：
        Align completed source information to observed execution opens/closes.

        A source decision is available at close+1+delay. It is emitted once rather
        than forward-filled as a repeated entry order. Price levels and trends use
        the latest source candle actually completed at an execution open; ATR for a
        trailing update uses only source candles completed at that execution close.
    """
    market = source.copy() if execution_data is None else execution_data.copy()
    market = market.reset_index(drop=True)
    if not {'open_time', 'close_time'}.issubset(market):
        raise ValueError('Execution data needs open_time and close_time')
    market = market.loc[(market.open_time >= source.open_time.iloc[0]) &
                        (market.close_time <= source.close_time.iloc[-1])].reset_index(drop=True)
    if market.empty or market.open_time.iloc[0] != source.open_time.iloc[0] or market.close_time.iloc[-1] != source.close_time.iloc[-1]:
        raise ValueError('Execution data must cover the complete source candle range')
    if market.close_time.iloc[0]-market.open_time.iloc[0] > source.close_time.iloc[0]-source.open_time.iloc[0]:
        raise ValueError('Execution candles cannot be coarser than source candles')
    source_info = pd.DataFrame(dict(
        available=source.close_time.to_numpy(dtype=np.int64)+1,
        source_index=np.arange(len(source)),
        source_time=source.close_time.to_numpy(dtype=np.int64),
        source_price=source.close.to_numpy(dtype=float),
        source_atr=source.atr.to_numpy(dtype=float),
        source_trend=source.higher_trend.to_numpy(dtype=int) if 'higher_trend' in source else np.zeros(len(source), dtype=int),
        prior_high=source.high.rolling(3, min_periods=3).max().to_numpy(),
        prior_low=source.low.rolling(3, min_periods=3).min().to_numpy(),
    ))
    at_open = pd.merge_asof(market[['open_time']], source_info,
                            left_on='open_time', right_on='available')
    delayed = source_info[['available', 'source_index', 'source_time', 'source_atr', 'source_price']].copy()
    delayed.available += int(delay_ms)
    decision = pd.merge_asof(market[['open_time']], delayed,
                             left_on='open_time', right_on='available')
    indices = decision.source_index.fillna(-1).to_numpy(dtype=int)
    fresh = (indices >= 0) & (indices != np.r_[-1, indices[:-1]])
    requested = np.zeros(len(market), dtype=int)
    requested[fresh] = signals.direction.to_numpy(dtype=int)[indices[fresh]]
    pattern = np.full(len(market), '', dtype=object)
    pattern[fresh] = signals.pattern.astype(str).to_numpy()[indices[fresh]]
    at_close = source_info[['available', 'source_atr']].copy()
    at_close.available -= 1
    known_close = pd.merge_asof(market[['close_time']], at_close,
                                left_on='close_time', right_on='available')
    market['atr'] = known_close.source_atr.to_numpy()
    # _validate checks the execution OHLC/mark and contiguous timestamps too.
    market['higher_trend'] = at_open.source_trend.fillna(0).to_numpy(dtype=int)
    return market, dict(requested=requested, patterns=pattern,
        signal_times=decision.source_time.fillna(-1).to_numpy(dtype=np.int64),
        signal_prices=decision.source_price.to_numpy(dtype=float),
        entry_atr=decision.source_atr.to_numpy(dtype=float),
        trends=market.higher_trend.to_numpy(dtype=int),
        highs=at_open.prior_high.to_numpy(dtype=float),
        lows=at_open.prior_low.to_numpy(dtype=float))


def simulate_patterns(data, signals=None, exit_mode='fixed_rr', leverage=5.,
                      margin_fraction=.1, fee=.0005, slip=.0002,
                      maintenance_rate=.01, liquidation_fee=.01,
                      atr_stop=1.5, reward_risk=2., trailing_atr=2.,
                      max_holding_bars=12, funding_events=None,
                      exit_on_trend_reversal=True, execution_data=None,
                      execution_delay_ms=0, min_holding_ms=0,
                      max_holding_ms=None, scale_out_after_ms=None,
                      scale_out_interval_ms=86_400_000,
                      scale_out_fraction=.25, scale_out_deviation_atr=3.,
                      price_target_pct=.05, price_stop_pct=.03,
                      min_profit_move_pct=0., risk_policy=None, risk_plan=None, risk_reductions=False):
    """逐根回放反转入场、多止盈、人工延迟、逐仓风控和分批减仓。

    Args:
        data: 当前函数契约下的时间顺序行情/特征表。
        signals: 与信号K线对齐的-1/0/1方向及可选形态表。
        exit_mode: 预定义退出方式名称。
        leverage: 逐仓名义金额与初始保证金之比；不是收益倍数保证。
        margin_fraction: 单笔初始保证金占开仓前账户权益的比例。
        fee: 单边成交手续费比例，例如0.0005表示0.05%。
        slip: 单边不利滑点比例。
        maintenance_rate: 研究用近似维持保证金比例，不是账户真实分档保证金。
        liquidation_fee: 近似强平时扣除的费用比例。
        atr_stop: 初始硬止损使用的ATR倍数。
        reward_risk: 固定目标相对初始风险距离的倍数。
        trailing_atr: ATR跟踪止损距离倍数。
        max_holding_bars: 按源周期计算的最大持仓根数。
        funding_events: 按真实结算毫秒时刻提供的独立资金费事件。
        exit_on_trend_reversal: 是否允许已收盘大周期趋势反转触发退出。
        execution_data: 细周期实际可观察成交K线；不允许凭空构造细粒度价格。
        execution_delay_ms: 决策收盘后模拟人工响应延迟，单位毫秒。
        min_holding_ms: 自愿止盈/反向退出的持仓下限毫秒数；风险止损不受限。
        max_holding_ms: 最长持仓毫秒数，到期在下一可观察开盘退出。
        scale_out_after_ms: 允许按时间/偏离触发减仓的最早持仓毫秒数。
        scale_out_interval_ms: 两次分批减仓之间至少间隔的毫秒数。
        scale_out_fraction: 每次减掉的初始持仓数量比例。
        scale_out_deviation_atr: 允许分批减仓所需的相对入场ATR绝对价格偏离。
        price_target_pct: 目标标的价格方向收益比例。
        price_stop_pct: 标的价格方向止损比例。
        min_profit_move_pct: 允许获利退出的最低标的价格变动比例，不限制强制风险退出。
        risk_policy: 可选RiskPolicy；None严格保留原固定仓位行为。
        risk_plan: 与源K线逐行对应的score/daily_volatility表，仅使用已收盘行。
        risk_reductions: 是否启用一次性分级利润/波动/评分恶化减仓。

    Returns:
        指标、权益路径、回撤路径、完整仓位账本和逐笔事件；不访问交易账户。

    Notes:
        原有细则保留如下：
        Return metrics, close-time equity/drawdown, a trade ledger, and events.

        ``data`` contains contiguous millisecond OHLC candles, ``mark_*`` OHLC and
        ``atr``. ``signals`` is a same-length frame with ``direction`` in -1/0/1 and
        optional ``pattern``; omitted signals are read from data. Price/ATR signals
        at the final close cannot create a position. Optional ``higher_trend`` is a
        fully closed, already aligned higher-timeframe direction supplied by caller.

        All policies retain an ATR hard stop. ``three_bar_breakout`` takes a net
        profitable long at the previous three highs (short at three lows), while
        ``three_bar_trailing`` takes a profitable long below three lows (short above
        three highs). Targets refresh only from preceding completed candles. The
        fixed variant freezes those three-candle levels before entry. A gap through
        a level fills at the open, with adverse slippage. The ATR trailing stop is
        advanced after each close, never using the current high before that close.

        Funding events use exact timestamps and rate; an optional mark_price is
        preferred. Missing event marks fall back to the candle mark open and are
        labeled in the event ledger. Account equity starts at 1. Returns include
        both fees, slippage and funding, with isolated losses capped at collateral.

        Optional duration limits are real milliseconds, independent of candle
        timeframe. Entries can wait for a manual reaction delay on finer observed
        ``execution_data``; absent that data the next observed source open is used.
        Profit, reversal and policy-time exits wait for ``min_holding_ms``; hard
        initial stops and mark liquidation remain active. ``max_holding_ms`` is an
        unconditional upper limit at the next observed open. FINAL_EXIT is a dataset
        cutoff and can occur earlier. After ``scale_out_after_ms``, an absolute
        displacement of ``scale_out_deviation_atr`` entry ATRs allows reducing the
        original quantity by ``scale_out_fraction`` at most once per interval.
        Both favorable and adverse displacements qualify; no quantity is added.
        One trade row represents the whole position, including any partial exits.

        ``swing_percent`` targets a configurable underlying-price move (2%--50%,
        5% by default), measured between slipped entry and exit fills. Its hard
        stop is the tighter of the ATR stop and a 3% price stop. An optional
        ``min_profit_move_pct`` raises fixed RR targets and disables three-candle
        profit levels below that price move. It also gates profitable ATR trailing
        levels. Risk stops, reversals, duration limits, dataset cutoffs and gradual
        position reductions remain possible below that profit threshold.
    """
    from quant_lab.risk.policy import RiskPolicy
    from quant_lab.risk.sizing import position_budget
    from quant_lab.risk.reductions import reduction_decision
    if isinstance(risk_policy, dict):risk_policy=RiskPolicy(**risk_policy)
    if risk_policy is not None and not isinstance(risk_policy,RiskPolicy):raise ValueError('Invalid risk_policy')
    risk_lookup={};risk_times=np.array([],dtype=np.int64)
    if risk_policy is not None:
        if not math.isclose(maintenance_rate,risk_policy.maintenance_rate_assumption):raise ValueError('Maintenance assumption differs from risk policy')
        if not isinstance(risk_plan,pd.DataFrame) or len(risk_plan)!=len(data) or not {'score','daily_volatility'}.issubset(risk_plan):raise ValueError('Risk plan must align with source candles')
        if 'close_time' in risk_plan and not np.array_equal(risk_plan.close_time,data.close_time):raise ValueError('Risk plan timestamp mismatch')
        risk_times=data.close_time.to_numpy(dtype=np.int64)
        risk_lookup=dict(zip(map(int,risk_times),risk_plan.to_dict('records')))
    elif risk_plan is not None or risk_reductions:raise ValueError('Risk overlay requires an explicit policy')
    data = data.reset_index(drop=True).copy()
    if signals is None:
        if 'direction' not in data:
            raise ValueError('Missing direction signals')
        signals = data[['direction'] + (['pattern'] if 'pattern' in data else [])]
    elif not isinstance(signals, pd.DataFrame):
        signals = pd.DataFrame({'direction': np.asarray(signals)})
    signals = signals.reset_index(drop=True).copy()
    if 'direction' not in signals:
        raise ValueError('Missing direction signals')
    if 'pattern' not in signals:
        signals['pattern'] = ''
    parameters = dict(leverage=leverage, margin_fraction=margin_fraction, fee=fee,
        slip=slip, maintenance_rate=maintenance_rate, liquidation_fee=liquidation_fee,
        atr_stop=atr_stop, reward_risk=reward_risk, trailing_atr=trailing_atr,
        max_holding_bars=max_holding_bars)
    _validate(data, signals, exit_mode, parameters)
    if (not all(math.isfinite(float(v)) for v in
                (price_target_pct, price_stop_pct, min_profit_move_pct))
            or not .02 <= price_target_pct <= .5
            or not 0 < price_stop_pct < 1
            or not 0 <= min_profit_move_pct <= .5):
        raise ValueError('Invalid percentage target, stop or minimum profit move')
    for name, value in dict(execution_delay_ms=execution_delay_ms,
                            min_holding_ms=min_holding_ms,
                            max_holding_ms=max_holding_ms,
                            scale_out_after_ms=scale_out_after_ms,
                            scale_out_interval_ms=scale_out_interval_ms).items():
        if value is not None and (not math.isfinite(value) or value < 0 or int(value) != value):
            raise ValueError(name + ' must be nonnegative integer milliseconds')
    if max_holding_ms is not None and (max_holding_ms <= 0 or max_holding_ms < min_holding_ms):
        raise ValueError('max_holding_ms must be positive and at least min_holding_ms')
    if scale_out_interval_ms <= 0 or not 0 < scale_out_fraction < 1 or not math.isfinite(scale_out_deviation_atr) or scale_out_deviation_atr <= 0:
        raise ValueError('Invalid gradual reduction parameters')
    duration_mode = (execution_data is not None or execution_delay_ms > 0 or min_holding_ms > 0
                     or max_holding_ms is not None or scale_out_after_ms is not None)
    source_duration = int(data.close_time.iloc[0]-data.open_time.iloc[0]+1)
    source_has_trend = 'higher_trend' in data
    aligned = None
    if duration_mode:
        data, aligned = _execution_alignment(data, signals, execution_data, execution_delay_ms)
        _validate(data, pd.DataFrame({'direction': aligned['requested']}), exit_mode, parameters)
    directions = signals.direction.to_numpy(dtype=int)
    patterns = signals.pattern.astype(str).to_numpy()
    atr_values = data.atr.to_numpy(dtype=float)
    trend_values = data.higher_trend.to_numpy(dtype=int) if 'higher_trend' in data else np.zeros(len(data), dtype=int)
    close_times = data.close_time.to_numpy(dtype=np.int64)
    prior_highs = aligned['highs'] if aligned else data.high.rolling(3, min_periods=3).max().shift(1).to_numpy()
    prior_lows = aligned['lows'] if aligned else data.low.rolling(3, min_periods=3).min().shift(1).to_numpy()
    funding = _funding_table(funding_events)
    funding_rows = list(funding.itertuples(index=False))
    funding_index = 0
    while funding_index < len(funding_rows) and funding_rows[funding_index].funding_time < data.open_time.iloc[0]:
        funding_index += 1

    free, position = 1., None
    ledger, events, path = [], [], []
    total_fees, total_funding = 0., 0.
    stress_peak, stress_drawdown = 1., 0.
    risk_points = [1.]
    entries, liquidations = 0, 0

    def nav(mark):
        """以标记价格估值可用资金和剩余逐仓仓位。

        Args:
            mark: 当前观察的标记价格。

        Returns:
            归一化账户权益，逐仓抵押品不计为负值。
        """
        return free if position is None else free + max(0., position.margin + position.quantity * (mark - position.entry))

    def observe(mark):
        """记录当前标记权益并更新压力峰值和最大回撤。

        Args:
            mark: 当前观察的标记价格。

        Returns:
            无返回值；只修改外层模拟状态。
        """
        nonlocal stress_peak, stress_drawdown
        value = nav(mark)
        risk_points.append(float(value))
        stress_peak = max(stress_peak, value)
        if stress_peak > 0:
            stress_drawdown = max(stress_drawdown, 1 - value / stress_peak)

    def net_if_closed(raw_price):
        """预估当前剩余仓位按该原始价格退出后的整笔净损益。

        Args:
            raw_price: 尚未计入本次退出滑点的原始行情价格。

        Returns:
            包含此前减仓、手续费及已结资金费的归一化净损益。
        """
        p = position
        fill = raw_price * (1 - p.direction * slip)
        available = max(0., p.margin + p.quantity*(fill-p.entry))
        charge = min(available, abs(p.quantity)*fill*fee)
        return p.partial_credit + available-charge-p.allocated

    def price_move(raw_price):
        """把预计滑点后的退出价换算为相对实际入场价的方向收益。

        Args:
            raw_price: 尚未计入本次退出滑点的原始行情价格。

        Returns:
            标的价格方向收益比例，不是杠杆或账户收益。
        """
        return position.direction*(raw_price*(1-position.direction*slip)/position.entry-1)

    def raw_profit_level(entry, direction, minimum):
        # The threshold concerns actual fills. Slippage is applied once by
        # finish(); account fees and funding are checked separately below.
        """反推满足最低价格收益的原始行情目标价，避免重复计入滑点。

        Args:
            entry: 实际含入场滑点的建仓价格。
            direction: 方向：1做多、-1做空、0不建立新仓位。
            minimum: 最低标的价格方向收益比例。

        Returns:
            满足实际成交价收益门槛的原始目标价。
        """
        return entry*(1+direction*minimum)/(1-direction*slip)

    def minimum_profit_met(raw_price):
        """检查拟成交价格是否满足最低标的价格收益条件。

        Args:
            raw_price: 尚未计入本次退出滑点的原始行情价格。

        Returns:
            布尔值；门槛为零时通过。
        """
        return min_profit_move_pct <= 0 or price_move(raw_price)+1e-12 >= min_profit_move_pct

    def finish(raw_price, timestamp, bar, reason, precision='bar_open', liquidation=False):
        """结清剩余仓位，并将所有分批退出归并为一笔完整持仓记录。

        Args:
            raw_price: 尚未计入本次退出滑点的原始行情价格。
            timestamp: 当前模拟事件的真实或近似发生时刻，单位毫秒。
            bar: 当前执行K线的整数位置索引。
            reason: 退出/风险事件原因标签。
            precision: 事件时间精度标签，不能把K线内近似当作逐笔真实时间。
            liquidation: 是否按强平费用和结算规则完整退出。

        Returns:
            无返回值；同时更新现金、费用、完整交易与退出事件账本。
        """
        nonlocal free, position, total_fees, liquidations
        p = position
        fill = raw_price if liquidation else raw_price * (1 - p.direction * slip)
        gross = p.quantity * (fill - p.entry)
        available = max(0., p.margin + gross)
        rate = liquidation_fee + slip if liquidation else fee
        charge = min(available, abs(p.quantity) * fill * rate)
        credit = max(0., available - charge)
        free += credit
        total_fees += charge
        pnl = credit + p.partial_credit - p.allocated
        if liquidation:
            liquidations += 1
        holding_ms = max(0, int(timestamp)-p.entry_time)
        record = dict(position_id=p.position_id, pattern=p.pattern, direction=p.direction,
            signal_time=p.signal_time, entry_time=p.entry_time, exit_time=int(timestamp),
            entry_bar=p.entry_bar, exit_bar=int(bar),
            holding_bars=int(bar-p.entry_bar+(precision != 'bar_open')),
            holding_ms=holding_ms, holding_hours=holding_ms/3_600_000,
            entry_price=p.entry, exit_price=float(fill), exit_reason=reason,
            signal_price=p.signal_price, entry_raw_price=p.entry_raw_price,
            exit_raw_price=float(raw_price),
            directional_price_return=float((gross+p.partial_gross)/(abs(p.initial_quantity)*p.entry)),
            exit_time_precision=precision, quantity=abs(p.initial_quantity),
            initial_quantity=abs(p.initial_quantity), remaining_quantity=0.,
            final_exit_quantity=abs(p.quantity),
            partial_exit_quantity=abs(p.initial_quantity)-abs(p.quantity),
            partial_exit_count=p.partial_exit_count, partial_exit_fee=p.partial_fees,
            partial_realized_pnl=p.partial_realized_pnl,
            allocated_margin=p.allocated, entry_notional=abs(p.initial_quantity)*p.entry,
            initial_stop=p.initial_stop, final_stop=p.stop,
            target_price=float(p.target) if math.isfinite(p.target) else None,
            gross_pnl=float(gross+p.partial_gross), entry_fee=p.entry_fee,
            exit_fee=float(charge+p.partial_fees), final_exit_fee=float(charge),
            total_fee=float(p.entry_fee+charge+p.partial_fees), funding_paid=p.funding,
            net_pnl=float(pnl), account_return=float(pnl/p.account_before),
            margin_return=float(pnl/p.allocated),
            account_equity_before=p.account_before, account_equity_after=float(free))
        if risk_policy is not None:
            record.update(entry_score=p.entry_quality,entry_daily_volatility=p.entry_volatility,
                leverage_at_entry=p.risk_sizing['leverage'],margin_fraction_at_entry=p.risk_sizing['margin_fraction'],
                nominal_exposure_at_entry=p.risk_sizing['nominal_exposure'],risk_budget_fraction=p.risk_sizing['risk_budget_fraction'],
                risk_actions='|'.join(sorted(p.risk_actions)),risk_policy_version=risk_policy.version)
        ledger.append(record)
        events.append(dict(time=int(timestamp), event=reason, position_id=p.position_id,
            direction=p.direction, quantity=abs(p.quantity), remaining_quantity=0.,
            price=float(fill), fee=float(charge), pattern=p.pattern,
            entry_fee=p.entry_fee*abs(p.quantity)/abs(p.initial_quantity),
            funding_paid=p.funding-p.partial_funding,
            gross_pnl=float(gross),
            holding_ms=holding_ms, holding_hours=holding_ms/3_600_000,
            net_pnl=float(pnl-p.partial_realized_pnl),
            cash_after=float(free), equity_after=float(free),
            timestamp_precision=precision, bar=int(bar)))
        position = None

    def reduce_position(raw_price, timestamp, bar, initial_fraction=None, reduction_reason=None):
        """按初始仓位比例减掉部分持仓并按数量分摊保证金和成本。

        Args:
            raw_price: 尚未计入本次退出滑点的原始行情价格。
            timestamp: 当前模拟事件的真实或近似发生时刻，单位毫秒。
            bar: 当前执行K线的整数位置索引。
            initial_fraction: 本次减掉初始数量的比例；None使用原时间减仓参数。
            reduction_reason: 可选风险动作标签；None保留原时间偏离标签。

        Returns:
            无返回值；只减仓、不加仓，余额过小时转为完整退出。
        """
        nonlocal free, total_fees
        p = position
        amount = min(abs(p.initial_quantity)*(scale_out_fraction if initial_fraction is None else initial_fraction), abs(p.quantity))
        favorable = p.direction*(raw_price-p.entry) >= 0
        suffix = 'FAVORABLE' if favorable else 'ADVERSE'
        if amount >= abs(p.quantity)-1e-14:
            finish(raw_price, timestamp, bar, reduction_reason+'_COMPLETE' if reduction_reason else 'TIME_SCALE_OUT_COMPLETE_'+suffix)
            return
        fraction = amount/abs(p.quantity)
        signed = p.direction*amount
        fill = raw_price*(1-p.direction*slip)
        margin = p.margin*fraction
        gross = signed*(fill-p.entry)
        available = max(0., margin+gross)
        charge = min(available, amount*fill*fee)
        credit = available-charge
        allocated = p.allocated*amount/abs(p.initial_quantity)
        pnl = credit-allocated
        funding_paid = (p.funding-p.partial_funding)*fraction
        entry_fee = p.entry_fee*amount/abs(p.initial_quantity)
        p.quantity -= signed
        p.margin -= margin
        p.partial_credit += credit
        p.partial_gross += gross
        p.partial_fees += charge
        p.partial_funding += funding_paid
        p.partial_realized_pnl += pnl
        p.partial_exit_count += 1
        p.last_scale_time = int(timestamp)
        free += credit
        total_fees += charge
        held = int(timestamp)-p.entry_time
        events.append(dict(time=int(timestamp), event='PARTIAL_EXIT',
            position_id=p.position_id, direction=p.direction, quantity=amount,
            remaining_quantity=abs(p.quantity), price=float(fill), fee=float(charge),
            gross_pnl=float(gross), net_pnl=float(pnl), pattern=p.pattern,
            funding_paid=float(funding_paid), entry_fee=float(entry_fee),
            reason=reduction_reason or 'TIME_DEVIATION_'+suffix, holding_ms=held,
            holding_hours=held/3_600_000, cash_after=float(free),
            equity_after=float(nav(raw_price)), timestamp_precision='bar_open', bar=int(bar)))

    def liquidate_if_needed(mark, timestamp, bar, opening=False, funding_event=False):
        """用标记价格检查近似维持保证金阈值并在必要时强平。

        Args:
            mark: 当前观察的标记价格。
            timestamp: 当前模拟事件的真实或近似发生时刻，单位毫秒。
            bar: 当前执行K线的整数位置索引。
            opening: 是否为开盘跳空风险检查。
            funding_event: 是否为资金费结算导致的风险检查。

        Returns:
            发生强平返回True；区分开盘跳空、K线内近似和资金费时刻。
        """
        p = position
        if p is None or p.margin + p.quantity * (mark-p.entry) > abs(p.quantity)*mark*maintenance_rate:
            return False
        amount = abs(p.quantity)
        threshold = ((amount*p.entry-p.margin)/(amount*(1-maintenance_rate)) if p.direction > 0
                     else (amount*p.entry+p.margin)/(amount*(1+maintenance_rate)))
        fill = (min(mark, threshold) if p.direction > 0 else max(mark, threshold)) if opening else threshold
        finish(max(0., fill), timestamp, bar,
            'LIQUIDATION_AFTER_FUNDING' if funding_event else 'LIQUIDATION_OPEN_GAP' if opening else 'LIQUIDATION_INTRABAR',
            'exchange_event' if funding_event else 'bar_open' if opening else 'bar_close_intrabar_approximation', True)
        return True

    def apply_funding(event, row, bar):
        """在真实结算时刻扣收资金费并检查结算后强平。

        Args:
            event: 带时间、费率及可选标记价格的资金费事件。
            row: 当前K线或候选结果记录。
            bar: 当前执行K线的整数位置索引。

        Returns:
            资金费后是否发生强平；优先用事件自带标记价格。
        """
        nonlocal total_funding
        if position is None:
            return False
        exact_mark = math.isfinite(event.mark_price)
        mark = float(event.mark_price if exact_mark else row.mark_open)
        cash = position.quantity * mark * event.rate
        position.margin -= cash
        position.funding += cash
        total_funding += cash
        events.append(dict(time=int(event.funding_time), event='FUNDING',
            position_id=position.position_id, direction=position.direction,
            quantity=abs(position.quantity), funding_cash=float(cash),
            rate=float(event.rate), price=mark,
            funding_price_source='event_mark' if exact_mark else 'bar_mark_open_approximation',
            timestamp_precision='exchange_event', bar=int(bar)))
        return liquidate_if_needed(mark, int(event.funding_time), bar, opening=True, funding_event=True)

    def targets(bar):
        """读取当前执行时刻之前已收盘的三根源K线高低目标。

        Args:
            bar: 当前执行K线的整数位置索引。

        Returns:
            前高和前低二元组，不包含尚未收盘的源K线。
        """
        return float(prior_highs[bar]), float(prior_lows[bar])

    def take_profit_target(bar):
        """按选定止盈模式取得当前可用的固定或三根K线目标。

        Args:
            bar: 当前执行K线的整数位置索引。

        Returns:
            目标价；当前模式没有价格目标时返回NaN。
        """
        if exit_mode in ('fixed_rr', 'fixed_three_bar_target', 'swing_percent'):
            return position.target
        high, low = targets(bar)
        if exit_mode == 'three_bar_breakout':
            return high if position.direction > 0 else low
        if exit_mode == 'three_bar_trailing':
            return low if position.direction > 0 else high
        return math.nan

    for bar, row in enumerate(data.itertuples(index=False)):
        closed_this_bar = False
        if position is not None:
            closed_this_bar = liquidate_if_needed(row.mark_open, row.open_time, bar, opening=True)
        observe(row.mark_open)

        # An open-time funding event belongs to the incumbent position. Events
        # in gaps do not occur because input candles are required contiguous.
        while funding_index < len(funding_rows) and funding_rows[funding_index].funding_time <= row.open_time:
            event = funding_rows[funding_index]
            if event.funding_time == row.open_time and position is not None:
                closed_this_bar = apply_funding(event, row, bar) or closed_this_bar
            funding_index += 1

        requested = int(aligned['requested'][bar]) if aligned else int(directions[bar-1]) if bar else 0
        trend = int(aligned['trends'][bar]) if aligned else int(trend_values[bar-1]) if bar else 0
        held_ms = int(row.open_time-position.entry_time) if position is not None else 0
        voluntary_exit_allowed = held_ms >= min_holding_ms
        if position is not None:
            p = position
            gap_stop = row.open <= p.stop if p.direction > 0 else row.open >= p.stop
            if gap_stop:
                finish(row.open, row.open_time, bar,
                    'ATR_TRAILING_STOP' if exit_mode == 'atr_trailing' and p.stop != p.initial_stop else 'STOP_LOSS_GAP')
                closed_this_bar = True
            else:
                target = take_profit_target(bar)
                trailing = exit_mode == 'three_bar_trailing'
                crossed = ((row.open <= target if p.direction > 0 else row.open >= target) if trailing else
                           (row.open >= target if p.direction > 0 else row.open <= target))
                if (voluntary_exit_allowed and math.isfinite(target) and crossed
                        and minimum_profit_met(target) and minimum_profit_met(row.open)
                        and net_if_closed(target) > 0 and net_if_closed(row.open) > 0):
                    finish(row.open, row.open_time, bar, 'TAKE_PROFIT_GAP')
                    closed_this_bar = True
        if position is not None:
            if requested == -position.direction:
                position.pending_exit = 'OPPOSITE_SIGNAL'
            elif exit_on_trend_reversal and trend == -position.direction and not position.pending_exit:
                position.pending_exit = 'HIGHER_TREND_REVERSAL'
            if max_holding_ms is not None and held_ms >= max_holding_ms:
                finish(row.open, row.open_time, bar, 'MAX_HOLDING_TIME')
                closed_this_bar = True
            elif voluntary_exit_allowed and position.pending_exit:
                finish(row.open, row.open_time, bar, position.pending_exit)
                closed_this_bar = True
            elif (voluntary_exit_allowed and exit_mode == 'time_exit' and
                  (held_ms >= max_holding_bars*source_duration if duration_mode else bar-position.entry_bar >= max_holding_bars)):
                finish(row.open, row.open_time, bar, 'TIME_EXIT')
                closed_this_bar = True
        if position is not None and risk_policy is not None and risk_reductions:
            p=position
            index=int(np.searchsorted(risk_times,int(row.open_time)-1,side='right'))-1
            latest=risk_lookup[int(risk_times[index])] if index>=0 else None
            if latest and all(math.isfinite(float(latest[k])) for k in ('score','daily_volatility')) and float(latest['daily_volatility'])>0:
                distance=max(abs(p.entry-p.initial_stop),np.finfo(float).tiny)
                current_r=p.direction*(row.open-p.entry)/distance
                peak_r=p.direction*(p.extreme-p.entry)/distance
                action=reduction_decision(current_r,peak_r,float(latest['score']),p.entry_quality,
                    float(latest['daily_volatility']),p.entry_volatility,p.risk_actions,risk_policy)
                if action:
                    p.risk_actions.add(action['key']);p.risk_actions.update(action.get('consume',[]))
                    fraction=action.get('fraction',action.get('remaining_fraction',0)*abs(p.quantity)/abs(p.initial_quantity))
                    reduce_position(row.open,row.open_time,bar,initial_fraction=fraction,reduction_reason='RISK_'+action['key'])
                    if position is not None and action.get('protect_stop'):
                        # Protection uses known costs and the current open, never a future high/low.
                        buffer=position.entry*2*(fee+slip)+max(0.,position.funding)/max(abs(position.quantity),1e-15)
                        candidate=position.entry+position.direction*buffer
                        if position.direction>0:position.stop=max(position.stop,min(row.open,candidate))
                        else:position.stop=min(position.stop,max(row.open,candidate))
                    closed_this_bar=position is None

        if (position is not None and scale_out_after_ms is not None and voluntary_exit_allowed
                and held_ms >= scale_out_after_ms
                and row.open_time-position.last_scale_time >= scale_out_interval_ms
                and abs(row.open-position.entry) >= scale_out_deviation_atr*position.entry_atr):
            reduce_position(row.open, row.open_time, bar)
            closed_this_bar = position is None

        if (aligned and requested and position is None and source_has_trend
                and trend != requested):
            events.append(dict(time=int(row.open_time), event='ENTRY_CANCELLED_TREND',
                direction=requested, signal_time=int(aligned['signal_times'][bar]),
                bar=int(bar), timestamp_precision='bar_open'))
            requested = 0
        enough_history_remaining = data.close_time.iloc[-1]-row.open_time+1 >= min_holding_ms
        if (position is None and not closed_this_bar and requested and free > 1e-12
                and enough_history_remaining):
            known_atr = float(aligned['entry_atr'][bar] if aligned else atr_values[bar-1])
            if math.isfinite(known_atr) and known_atr > 0:
                fill = row.open * (1+requested*slip)
                distance = known_atr*atr_stop
                if exit_mode == 'swing_percent':
                    distance=min(distance,fill*price_stop_pct)
                selected_leverage,selected_margin=leverage,margin_fraction
                sizing={};quality=0.;volatility=0.
                if risk_policy is not None:
                    decision_time=int(aligned['signal_times'][bar] if aligned else close_times[bar-1])
                    observed=risk_lookup.get(decision_time)
                    if observed is None or not all(math.isfinite(float(observed[k])) for k in ('score','daily_volatility')) or float(observed['daily_volatility'])<=0:
                        events.append(dict(time=int(row.open_time),event='ENTRY_SKIPPED_RISK',reason='RISK_WARMUP_OR_MISSING_DECISION',signal_time=decision_time))
                        observe(row.mark_close);path.append(dict(close_time=int(row.close_time),equity=float(nav(row.mark_close))))
                        continue
                    quality=float(observed['score']);volatility=float(observed['daily_volatility'])
                    sizing=position_budget(quality,volatility,distance/fill,requested,
                        max(0.,1-free/stress_peak) if stress_peak else 0.,fee,slip,risk_policy)
                    if not sizing['allowed']:
                        events.append(dict(time=int(row.open_time),event='ENTRY_SKIPPED_RISK',reason=';'.join(sizing['reasons']),signal_time=decision_time))
                        observe(row.mark_close);path.append(dict(close_time=int(row.close_time),equity=float(nav(row.mark_close))))
                        continue
                    selected_leverage=sizing['leverage'];selected_margin=sizing['margin_fraction']
                allocated = free * selected_margin
                quantity = requested*allocated*selected_leverage/fill
                charge = abs(quantity)*fill*fee
                stop = fill-requested*distance
                # A nonpositive long stop is outside the tradable price domain.
                stop = max(np.finfo(float).tiny, stop)
                high, low = targets(bar)
                target = (fill+requested*distance*reward_risk if exit_mode == 'fixed_rr'
                          else high if requested > 0 else low) if exit_mode in ('fixed_rr', 'fixed_three_bar_target') else math.nan
                if exit_mode == 'swing_percent':
                    target=raw_profit_level(fill,requested,max(price_target_pct,min_profit_move_pct))
                elif exit_mode == 'fixed_rr' and min_profit_move_pct>0:
                    minimum=raw_profit_level(fill,requested,min_profit_move_pct)
                    target=max(target,minimum) if requested>0 else min(target,minimum)
                position = _Position(requested, quantity, fill, allocated-charge,
                    allocated, charge, int(row.open_time), bar,
                    int(aligned['signal_times'][bar] if aligned else close_times[bar-1]),
                    str(aligned['patterns'][bar] if aligned else patterns[bar-1]),
                    free, stop, stop, target, fill)
                position.risk_sizing=sizing
                position.entry_volatility=volatility
                position.entry_quality=quality
                position.position_id = entries+1
                position.initial_quantity = quantity
                position.entry_atr = known_atr
                position.signal_price = float(aligned['signal_prices'][bar] if aligned else data.close.iloc[bar-1])
                position.entry_raw_price = float(row.open)
                position.last_scale_time = int(row.open_time)
                free -= allocated
                total_fees += charge
                entries += 1
                events.append(dict(time=int(row.open_time), event='ENTRY',
                    position_id=position.position_id, direction=requested,
                    price=float(fill), quantity=float(abs(quantity)), fee=float(charge),
                    raw_price=float(row.open), signal_price=position.signal_price,
                    pattern=position.pattern, signal_time=position.signal_time,
                    bar=int(bar), timestamp_precision='bar_open',
                    **(dict(entry_score=quality,leverage=selected_leverage,margin_fraction=selected_margin,risk_policy_version=risk_policy.version) if risk_policy else {})))
                # Trade and mark prices are distinct feeds. A position can be
                # undercollateralized immediately after an unusual basis gap.
                closed_this_bar = liquidate_if_needed(row.mark_open, row.open_time, bar, opening=True)

        # Event-time funding is precise; its order versus an unknown intrabar
        # stop/target is approximated as preceding that exit, for debits/credits.
        while funding_index < len(funding_rows) and funding_rows[funding_index].funding_time <= row.close_time:
            event = funding_rows[funding_index]
            if position is not None:
                closed_this_bar = apply_funding(event, row, bar) or closed_this_bar
            funding_index += 1

        if position is not None:
            p = position
            adverse = row.mark_low if p.direction > 0 else row.mark_high
            favorable = row.mark_high if p.direction > 0 else row.mark_low
            observe(favorable)
            if liquidate_if_needed(adverse, row.close_time, bar):
                closed_this_bar = True
            else:
                observe(adverse)
                stop_touched = row.low <= p.stop if p.direction > 0 else row.high >= p.stop
                if stop_touched:
                    stop_fill = min(row.open, p.stop) if p.direction > 0 else max(row.open, p.stop)
                    finish(stop_fill, row.close_time, bar,
                        'ATR_TRAILING_STOP' if exit_mode == 'atr_trailing' and p.stop != p.initial_stop else 'STOP_LOSS',
                        'bar_close_intrabar_approximation')
                    closed_this_bar = True
                else:
                    target = take_profit_target(bar)
                    trailing = exit_mode == 'three_bar_trailing'
                    touched = ((row.low <= target if p.direction > 0 else row.high >= target) if trailing else
                               (row.high >= target if p.direction > 0 else row.low <= target))
                    correct_side = not trailing or (row.open > target if p.direction > 0 else row.open < target)
                    if (row.open_time-p.entry_time >= min_holding_ms and
                            math.isfinite(target) and touched and correct_side
                            and minimum_profit_met(target) and row.low <= target <= row.high
                            and net_if_closed(target) > 0):
                        finish(target, row.close_time, bar, 'THREE_BAR_TRAILING_PROFIT' if trailing else 'TAKE_PROFIT',
                            'bar_close_intrabar_approximation')
                        closed_this_bar = True

        if (position is not None and exit_mode == 'atr_trailing'
                and row.close_time-position.entry_time >= min_holding_ms):
            # Advance for the NEXT bar only; no assumed high/low ordering.
            p = position
            known_atr = float(row.atr)
            p.extreme = max(p.extreme, row.high) if p.direction > 0 else min(p.extreme, row.low)
            if math.isfinite(known_atr) and known_atr > 0:
                candidate = p.extreme-p.direction*known_atr*trailing_atr
                # Do not classify a sub-threshold gain as a profit trail. A
                # tighter loss-side protective stop remains valid at any time.
                candidate_move=price_move(candidate)
                if min_profit_move_pct<=0 or candidate_move<=0 or candidate_move+1e-12>=min_profit_move_pct:
                    p.stop = max(p.stop, candidate) if p.direction > 0 else min(p.stop, candidate)
        if position is not None and risk_reductions:
            # This completed bar's favorable price may only influence the NEXT open.
            position.extreme=max(position.extreme,row.high) if position.direction>0 else min(position.extreme,row.low)
        if bar == len(data)-1 and position is not None:
            finish(row.close, row.close_time, bar, 'FINAL_EXIT', 'bar_close')
        observe(row.mark_close)
        path.append(dict(close_time=int(row.close_time), equity=float(nav(row.mark_close))))

    equity = pd.DataFrame(path)
    values = equity.equity.to_numpy()
    peaks = np.maximum.accumulate(np.r_[1., values])[1:]
    equity['drawdown'] = 1-values/peaks
    returns = values/np.r_[1., values[:-1]]-1
    returns = np.nan_to_num(returns)
    deviation = np.std(returns, ddof=1) if len(returns) > 1 else 0.
    annual_bars = 365.25*24*3600*1000/float(data.close_time.iloc[0]-data.open_time.iloc[0]+1)
    wins = sum(trade['net_pnl'] > 0 for trade in ledger)
    profits = sum(max(0., trade['net_pnl']) for trade in ledger)
    losses = -sum(min(0., trade['net_pnl']) for trade in ledger)
    metrics = dict(net_return=float(values[-1]-1), max_drawdown=float(equity.drawdown.max()),
        max_drawdown_stress=float(stress_drawdown), entries=int(entries), trades=len(ledger),
        liquidations=int(liquidations), win_rate=float(wins/len(ledger)) if ledger else 0.,
        profit_factor=float(profits/losses) if losses > 0 else None,
        fees_equity_fraction=float(total_fees), funding_equity_fraction=float(total_funding),
        sharpe_estimate=float(np.mean(returns)/deviation*np.sqrt(annual_bars)) if deviation > 1e-12 else 0.,
        leverage=float(leverage), margin_fraction=float(margin_fraction), exit_mode=exit_mode,
        bars=len(data), funding_events_consumed=int(funding_index),
        initial_nominal_exposure=float(leverage*margin_fraction))
    if risk_policy is not None:
        metrics.update(leverage=None,margin_fraction=None,initial_nominal_exposure=None,
            leverage_mode='SCORE_VOLATILITY',risk_policy=risk_policy.to_dict(),risk_reductions=bool(risk_reductions),
            actual_max_leverage=max((t['leverage_at_entry'] for t in ledger),default=0),
            actual_mean_leverage=float(np.mean([t['leverage_at_entry'] for t in ledger])) if ledger else 0.,
            actual_mean_margin_fraction=float(np.mean([t['margin_fraction_at_entry'] for t in ledger])) if ledger else 0.)
    durations = np.array([trade['holding_hours'] for trade in ledger], dtype=float)
    short = [trade for trade in ledger if trade['holding_ms']+1 < min_holding_ms]
    risk_reasons = ('STOP_LOSS', 'ATR_TRAILING_STOP', 'LIQUIDATION')
    metrics.update(
        holding_hours_mean=float(durations.mean()) if len(durations) else 0.,
        holding_hours_median=float(np.median(durations)) if len(durations) else 0.,
        holding_hours_min=float(durations.min()) if len(durations) else 0.,
        holding_hours_max=float(durations.max()) if len(durations) else 0.,
        holding_hours_p90=float(np.percentile(durations, 90)) if len(durations) else 0.,
        holding_hours_total=float(durations.sum()),
        time_in_market_fraction=float(durations.sum()*3_600_000/
            (data.close_time.iloc[-1]-data.open_time.iloc[0]+1)),
        partial_exit_count=sum(trade['partial_exit_count'] for trade in ledger),
        short_holding_trades=len(short),
        short_holding_risk_exits=sum(trade['exit_reason'].startswith(risk_reasons) for trade in short),
        short_holding_cutoff_exits=sum(trade['exit_reason'] == 'FINAL_EXIT' for trade in short),
        final_exit_count=sum(trade['exit_reason'] == 'FINAL_EXIT' for trade in ledger),
        holding_in_target_range=int(sum(
            trade['holding_ms']+1 >= min_holding_ms and
            (max_holding_ms is None or trade['holding_ms'] <= max_holding_ms)
            for trade in ledger)),
        execution_delay_ms=int(execution_delay_ms), min_holding_ms=int(min_holding_ms),
        max_holding_ms=int(max_holding_ms) if max_holding_ms is not None else None,
        scale_out_after_ms=int(scale_out_after_ms) if scale_out_after_ms is not None else None,
        scale_out_interval_ms=int(scale_out_interval_ms),
        scale_out_fraction=float(scale_out_fraction),
        scale_out_deviation_atr=float(scale_out_deviation_atr),
        price_target_pct=float(price_target_pct), price_stop_pct=float(price_stop_pct),
        min_profit_move_pct=float(min_profit_move_pct),
        source_interval_ms=source_duration,
        execution_interval_ms=int(data.close_time.iloc[0]-data.open_time.iloc[0]+1))
    trade_frame = pd.DataFrame(ledger)
    if trade_frame.empty:
        trade_frame = pd.DataFrame(columns=[
            'position_id', 'pattern', 'direction', 'signal_time', 'entry_time', 'exit_time',
            'entry_bar', 'exit_bar', 'holding_bars', 'entry_price', 'exit_price',
            'signal_price', 'entry_raw_price', 'exit_raw_price', 'directional_price_return',
            'holding_ms', 'holding_hours', 'initial_quantity', 'remaining_quantity',
            'final_exit_quantity', 'partial_exit_quantity', 'partial_exit_count',
            'partial_exit_fee', 'partial_realized_pnl', 'final_exit_fee',
            'exit_reason', 'exit_time_precision', 'quantity', 'allocated_margin',
            'entry_notional', 'initial_stop', 'final_stop', 'target_price',
            'gross_pnl', 'entry_fee', 'exit_fee', 'total_fee', 'funding_paid',
            'net_pnl', 'account_return', 'margin_return',
            'account_equity_before', 'account_equity_after',
        ])
    event_frame = pd.DataFrame(events) if events else pd.DataFrame(columns=['time', 'event', 'direction', 'price', 'fee'])
    return dict(metrics=metrics, equity=equity,
        drawdown=equity[['close_time', 'drawdown']].copy(),
        trades=trade_frame, events=event_frame, risk_points=risk_points)
