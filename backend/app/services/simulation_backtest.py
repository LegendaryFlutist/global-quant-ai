"""用本地真实合约行情运行隔离、版本化、多理论模拟回测。"""

import hashlib
import math
import os
import time
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from app.core.config import DATA_ROOT
from app.domains.models import Asset, SimulationAccount
from app.services.derivative_history import read_derivative_history
from app.services.long_history import read_archive, three_year_start, validate_archive_identity
from quant_lab.analysis import candlestick_patterns, market_structure
from quant_lab.analysis.candlestick_patterns import PATTERN_COLUMNS, reversal_patterns
from quant_lab.analysis.market_structure import _rsi_series, structure_features
from quant_lab.backtesting import pattern_execution
from quant_lab.backtesting.pattern_execution import simulate_patterns

INTERVAL_MS = {
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}
EXIT_MODES = {
    "three_bar_trailing": "three_bar_trailing",
    "atr": "atr_trailing",
    "fixed_rr": "fixed_rr",
    "time": "time_exit",
}
FEE_RATE = Decimal("0.0005")
SLIPPAGE_RATE = Decimal("0.0002")
INITIAL_STOP_ATR = 1.5
FORECAST_HORIZONS_HOURS = (72, 168, 720)


def _frame(records):
    """把归档十进制字符串K线转换为研究计算表。

    Args:
        records: 本地校验过的真实K线字典列表，时间字段为UTC毫秒。

    Returns:
        时间升序的OHLCV DataFrame；研究价格列转换为float仅用于信号计算。
    """
    frame = pd.DataFrame(records).copy()
    for name in ("open_time", "close_time"):
        frame[name] = pd.to_numeric(frame[name], errors="raise").astype("int64")
    for name in ("open", "high", "low", "close", "volume"):
        frame[name] = pd.to_numeric(frame[name], errors="raise").astype(float)
    return frame.sort_values("open_time").reset_index(drop=True)


def _divergence_votes(bars, active_bars=4):
    """从确认摆动点生成有限有效期的因果RSI背离方向序列。

    Args:
        bars: 已收盘并按时间升序排列的OHLCV表。
        active_bars: 背离确认后可参与分数的K线根数。

    Returns:
        与bars逐行对齐的-1/0/1背离方向数组，未来K线不改变已确认事件。
    """
    result = np.zeros(len(bars), dtype=np.int8)
    if len(bars) < 20:
        return result
    _, _, pivots, _ = structure_features(bars, 2, True)
    rsi = _rsi_series(bars.close.to_numpy(dtype=float), 14)
    previous = {"high": None, "low": None}
    pulses = {}
    for point in pivots:
        kind = point["kind"]
        prior = previous[kind]
        value = rsi[point["index"]]
        if prior is not None and np.isfinite(value) and np.isfinite(prior["rsi"]):
            if kind == "low" and point["price"] < prior["price"] and value > prior["rsi"]:
                pulses[point["confirmed_index"]] = 1
            elif kind == "high" and point["price"] > prior["price"] and value < prior["rsi"]:
                pulses[point["confirmed_index"]] = -1
        previous[kind] = {"price": point["price"], "rsi": value}
    active_direction = 0
    remaining = 0
    for index in range(len(result)):
        if index in pulses:
            active_direction = pulses[index]
            remaining = active_bars
        if remaining:
            result[index] = active_direction
            remaining -= 1
        else:
            active_direction = 0
    return result


def build_multi_theory_signals(
    bars_by_timeframe, weights, entry_threshold, timeframe_weights=None,
    decision_horizon_hours=168, horizon_consensus_weight=0.25,
    direction_mode="trend_following",
):
    """按多周期权重与持有期限生成因果方向信号及未校准价格情景带。

    Args:
        bars_by_timeframe: 15m至1d的原生闭合K线DataFrame映射，至少含最小周期。
        weights: 权重和为1的趋势、道氏、波浪、背离、反转、深度模型配置。
        entry_threshold: 0至1之间的绝对方向分数门槛，不代表胜率。
        timeframe_weights: 每个原生周期相对权重；值越大该周期贡献越高。
        decision_horizon_hours: 主要入场评分期限，仅支持72、168、720小时。
        horizon_consensus_weight: 其余两个期限对入场分数的相对贡献。
        direction_mode: 顺最高周期、理论确认逆势反弹或多周期多数方向。

    Returns:
        最小周期信号、分期限理论评分、价格情景上下边界及多周期趋势证据。

    Raises:
        ValueError: 周期缺失、阈值无效或请求尚无注册器支持的深度模型。

    Notes:
        周期特征通过已完成收盘时间向下对齐；高周期尚未收盘时沿用最近已知值。
        反转形态只使用最小周期已收盘K线。entry方向在该收盘后交给模拟器下根开盘。
        价格情景中心与波动带是透明启发式，尚未概率校准，不等同可保证的目标价。
    """
    if not bars_by_timeframe:
        raise ValueError("回测至少需要一个已归档周期")
    base_interval = min(bars_by_timeframe, key=lambda item: INTERVAL_MS[item])
    base = bars_by_timeframe[base_interval].copy().reset_index(drop=True)
    if len(base) < 32 or not 0 <= float(entry_threshold) <= 1:
        raise ValueError("回测K线数量不足或入场分数阈值无效")
    if float(weights.get("deep_model", 0)) > 0:
        raise ValueError("Web模型注册器尚未实现，不能回测未验证的深度模型版本")
    aligned_components = []
    timeframe_weights = timeframe_weights or {}
    highest_interval = max(bars_by_timeframe, key=lambda item: INTERVAL_MS[item])
    for interval, bars in bars_by_timeframe.items():
        features = structure_features(bars)
        patterns = reversal_patterns(bars)
        source = pd.DataFrame({
            "close_time": bars.close_time.to_numpy(dtype=np.int64),
            f"trend_{interval}": features.analysis_trend.to_numpy(dtype=np.int8),
            f"dow_{interval}": features.analysis_dow_state.to_numpy(dtype=np.int8),
            f"wave_{interval}": features.analysis_wave_bias.to_numpy(dtype=np.int8),
            f"divergence_{interval}": _divergence_votes(bars),
        })
        if interval == base_interval:
            source = pd.concat([
                source.reset_index(drop=True),
                patterns[list(PATTERN_COLUMNS)].reset_index(drop=True),
            ], axis=1)
        aligned = pd.merge_asof(
            base[["close_time"]].sort_values("close_time"),
            source.sort_values("close_time"),
            on="close_time",
            direction="backward",
        )
        age = base.close_time.to_numpy(dtype=np.int64) - aligned.close_time.to_numpy(dtype=np.int64)
        valid = age >= 0
        valid &= age <= INTERVAL_MS[interval] * 2
        for prefix in ("trend", "dow", "wave", "divergence"):
            column = f"{prefix}_{interval}"
            aligned[column] = aligned[column].fillna(0).where(valid, 0).astype(np.int8)
        if interval == base_interval:
            for column in PATTERN_COLUMNS:
                aligned[column] = aligned[column].fillna(0).where(valid, 0).astype(np.int8)
        aligned_components.append(aligned.reset_index(drop=True))
    components = pd.concat([item.drop(columns="close_time") for item in aligned_components], axis=1)
    active_timeframes = list(bars_by_timeframe)
    interval_relative_weights = {
        interval: float(timeframe_weights.get(interval, 1)) for interval in active_timeframes
    }
    if not any(value > 0 for value in interval_relative_weights.values()):
        raise ValueError("至少一个已选周期的相对权重大于0")

    def weighted_feature(prefix, intervals):
        """按配置权重汇总已闭合周期特征，返回无量纲方向分数。"""
        denominator = sum(interval_relative_weights[item] for item in intervals)
        if denominator <= 0:
            raise ValueError("参与评分的周期相对权重总和必须大于0")
        return sum(
            interval_relative_weights[item] * components[f"{prefix}_{item}"]
            for item in intervals
        ) / denominator

    components["trend_score"] = weighted_feature("trend", active_timeframes)
    components["dow123_score"] = weighted_feature("dow", active_timeframes)
    components["wave_score"] = weighted_feature("wave", active_timeframes)
    components["divergence_score"] = weighted_feature("divergence", active_timeframes)
    components["reversal_score"] = np.sign(components[list(PATTERN_COLUMNS)].sum(axis=1))
    timeframe_scores = {}
    nonpattern_weight = sum(float(weights.get(name, 0)) for name in ("trend", "dow123", "wave", "divergence"))
    for interval in active_timeframes:
        numerator = sum(
            float(weights.get(name, 0)) * components[f"{prefix}_{interval}"]
            for name, prefix in (("trend", "trend"), ("dow123", "dow"), ("wave", "wave"), ("divergence", "divergence"))
        )
        denominator = nonpattern_weight
        if interval == base_interval:
            numerator += float(weights.get("reversal_patterns", 0)) * components["reversal_score"]
            denominator += float(weights.get("reversal_patterns", 0))
        timeframe_scores[interval] = numerator / max(denominator, np.finfo(float).tiny)
        components[f"timeframe_score_{interval}"] = timeframe_scores[interval]
    horizon_scores = {}
    for horizon_hours in FORECAST_HORIZONS_HOURS:
        eligible = [interval for interval in active_timeframes
                    if INTERVAL_MS[interval] / 3_600_000 <= horizon_hours / 4]
        if not eligible:
            eligible = active_timeframes
        eligible = [interval for interval in eligible if interval_relative_weights[interval] > 0]
        if not eligible:
            eligible = [interval for interval in active_timeframes if interval_relative_weights[interval] > 0]
        horizon_weight = sum(interval_relative_weights[item] for item in eligible)
        horizon_scores[horizon_hours] = sum(
            interval_relative_weights[item] * timeframe_scores[item] for item in eligible
        ) / horizon_weight
        components[f"forecast_score_{horizon_hours}h"] = horizon_scores[horizon_hours]
    consensus = sum(horizon_scores.values()) / len(horizon_scores)
    horizon_consensus_weight = float(horizon_consensus_weight)
    if decision_horizon_hours not in FORECAST_HORIZONS_HOURS or not 0 <= horizon_consensus_weight <= 1:
        raise ValueError("主要预测期限或多期限共识权重无效")
    primary_score = horizon_scores[decision_horizon_hours]
    components["entry_score"] = primary_score * (1 - horizon_consensus_weight) + consensus * horizon_consensus_weight
    high_trend = components[f"trend_{highest_interval}"].to_numpy(dtype=np.int8)
    score = components.entry_score.to_numpy(dtype=float)
    direction = np.sign(score).astype(np.int8)
    threshold_pass = np.abs(score) >= float(entry_threshold)
    if direction_mode == "trend_following":
        mode_pass = (direction == high_trend) & (high_trend != 0)
    elif direction_mode == "countertrend_rebound":
        base_divergence = components[f"divergence_{base_interval}"].to_numpy(dtype=np.int8)
        base_dow = components[f"dow_{base_interval}"].to_numpy(dtype=np.int8)
        base_trend = components[f"trend_{base_interval}"].to_numpy(dtype=np.int8)
        reversal = components["reversal_score"].to_numpy(dtype=np.int8)
        additional_confirmation = ((base_divergence == direction) | (base_dow == direction) | (base_trend == direction))
        mode_pass = (high_trend != 0) & (direction == -high_trend) & (reversal == direction) & additional_confirmation
    elif direction_mode == "balanced":
        same_direction = np.column_stack([
            np.sign(timeframe_scores[interval].to_numpy(dtype=float)) == direction
            for interval in active_timeframes
        ]).sum(axis=1)
        mode_pass = same_direction >= math.ceil(len(active_timeframes) / 2)
    else:
        raise ValueError("未知的多周期方向规则")
    permitted = threshold_pass & mode_pass
    direction = np.where(permitted, direction, 0).astype(np.int8)
    signal_names = np.where(direction > 0, "MULTI_THEORY_LONG", np.where(direction < 0, "MULTI_THEORY_SHORT", ""))
    signals = pd.DataFrame({"direction": direction, "pattern": signal_names})
    components["highest_timeframe_trend"] = high_trend
    components["direction"] = direction
    components["close_time"] = base.close_time.to_numpy(dtype=np.int64)
    bars_per_day = 86_400_000 / INTERVAL_MS[base_interval]
    log_returns = np.log(base.close.astype(float)).diff()
    volatility_window = min(len(base), max(20, int(20 * bars_per_day)))
    min_volatility_observations = min(20, len(base))
    daily_volatility = log_returns.rolling(
        volatility_window, min_periods=min_volatility_observations,
    ).std(ddof=0) * math.sqrt(bars_per_day)
    for horizon_hours in FORECAST_HORIZONS_HOURS:
        horizon_days = horizon_hours / 24
        horizon_sigma = np.clip(
            daily_volatility.to_numpy(dtype=float) * math.sqrt(horizon_days), 0, 3,
        )
        log_shift = np.clip(
            horizon_scores[horizon_hours].to_numpy(dtype=float) * horizon_sigma * 0.1,
            -1.5,
            1.5,
        )
        center = base.close.to_numpy(dtype=float) * np.exp(
            log_shift
        )
        spread = np.clip(1.28 * horizon_sigma, 0, 3)
        components[f"scenario_{horizon_hours}h_center"] = center
        components[f"scenario_{horizon_hours}h_lower"] = center * np.exp(-spread)
        components[f"scenario_{horizon_hours}h_upper"] = center * np.exp(spread)
    return signals, components, base_interval, highest_interval


def _attach_forecast_outcomes(trades, components, bars):
    """把入场时生成的期限情景与未来实际收盘价作事后对照。

    Args:
        trades: 已完成/强制结清的模拟成交记录，含入场、信号和方向。
        components: 决策收盘时可用的多期限分数与价格情景。
        bars: 截至回测结束的真实本地基础周期K线。

    Returns:
        附带三天/七天/三十天情景及事后实现结果的逐笔表和校准摘要。

    Notes:
        未来价格只用于回测事后评价，不会进入过去的入场分数或情景计算。
    """
    if trades.empty:
        return trades, {}
    output = trades.copy()
    close_times = bars.close_time.to_numpy(dtype=np.int64)
    closes = bars.close.to_numpy(dtype=float)
    forecast_rows = components.set_index("close_time").to_dict(orient="index")
    evaluations = {}
    for horizon_hours in FORECAST_HORIZONS_HOURS:
        horizon = f"{horizon_hours}h"
        forecast_column_map = {
            f"forecast_{horizon}_score": f"forecast_score_{horizon}",
            f"scenario_{horizon}_center": f"scenario_{horizon}_center",
            f"scenario_{horizon}_lower": f"scenario_{horizon}_lower",
            f"scenario_{horizon}_upper": f"scenario_{horizon}_upper",
        }
        for output_column, source_column in forecast_column_map.items():
            output[output_column] = output.signal_time.map(
                lambda timestamp: forecast_rows.get(int(timestamp), {}).get(source_column)
            )
        actual_values = []
        return_values = []
        for trade in output.itertuples(index=False):
            target_time = int(trade.entry_time) + horizon_hours * 3_600_000
            position = int(np.searchsorted(close_times, target_time, side="left"))
            actual = closes[position] if position < len(closes) else np.nan
            entry_price = float(trade.entry_price)
            actual_values.append(actual)
            return_values.append((actual / entry_price - 1) * 100 if np.isfinite(actual) else np.nan)
        output[f"actual_{horizon}_close"] = actual_values
        output[f"actual_{horizon}_return_pct"] = return_values
        valid = np.isfinite(np.asarray(return_values, dtype=float))
        if valid.any():
            realized = np.asarray(return_values, dtype=float)[valid]
            directions = output.direction.to_numpy(dtype=int)[valid]
            evaluations[horizon] = {
                "observations": int(valid.sum()),
                "directional_accuracy_pct": float((np.sign(realized) == directions).mean() * 100),
                "mean_directional_return_pct": float((realized * directions).mean()),
            }
        else:
            evaluations[horizon] = {
                "observations": 0,
                "directional_accuracy_pct": None,
                "mean_directional_return_pct": None,
            }
    return output, evaluations


def _write_immutable_frame(directory, filename, frame):
    """将回测权益或成交表以不可覆盖的Parquet文件保存并返回SHA256。

    Args:
        directory: run UUID对应的专用本地持久目录。
        filename: 受限的固定文件名。
        frame: 已清理NaN和资金格式的报告DataFrame。

    Returns:
        数据集内相对文件路径和文件SHA256。

    Raises:
        FileExistsError: 同一run已有不同内容，防止覆盖旧报告。
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    temporary = directory / (filename + ".tmp")
    pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), temporary, compression="zstd")
    digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() == digest:
            temporary.unlink()
            return str(path.relative_to(DATA_ROOT)), digest
        temporary.unlink()
        raise FileExistsError("该回测任务已保存不同内容的不可变报告")
    os.replace(temporary, path)
    return str(path.relative_to(DATA_ROOT)), digest


def _algorithm_sha256():
    """对多理论决策与成交模拟核心源文件生成稳定内容摘要。

    Returns:
        按固定模块顺序连接文件字节计算的SHA256，不读取用户数据或凭据。
    """
    modules = (Path(__file__), market_structure, candlestick_patterns, pattern_execution)
    digest = hashlib.sha256()
    for module in modules:
        source_path = module if isinstance(module, Path) else Path(module.__file__)
        digest.update(source_path.name.encode("utf-8"))
        digest.update(source_path.read_bytes())
    return digest.hexdigest()


def _format_money_columns(frame, allocation):
    """将模拟器中的报价、数量和损益浮点字段转为精确十进制文本。

    Args:
        frame: 模拟器返回的成交记录表，权益基数为1的标准化子账户。
        allocation: 子账户分配的初始USDT资金，用于缩放数量与损益。

    Returns:
        用string保存价格/数量/手续费/损益的报告表，空值保持None。
    """
    output = frame.copy()
    capital_names = {
        "initial_quantity", "remaining_quantity", "final_exit_quantity", "partial_exit_quantity",
        "partial_exit_fee", "partial_realized_pnl", "final_exit_fee", "quantity",
        "allocated_margin", "entry_notional", "gross_pnl", "entry_fee", "exit_fee",
        "total_fee", "funding_paid", "net_pnl", "account_equity_before", "account_equity_after",
    }
    money_names = {
        "entry_price", "exit_price", "signal_price", "entry_raw_price", "exit_raw_price",
        "initial_stop", "final_stop", "target_price",
    }
    money_names.update(
        f"scenario_{horizon}_{bound}" for horizon in ("72h", "168h", "720h")
        for bound in ("center", "lower", "upper")
    )
    money_names.update(f"actual_{horizon}_close" for horizon in ("72h", "168h", "720h"))
    for column in (money_names | capital_names) & set(output.columns):
        def format_value(value):
            """格式化单个研究结果字段且将标准化子账户金额换算成USDT。"""
            if pd.isna(value):
                return None
            amount = Decimal(str(value))
            return format(amount * allocation if column in capital_names else amount, "f")
        output[column] = output[column].map(format_value)
    return output


def _metrics_summary(metrics, allocation):
    """把单标的标准化回测结果转换为按初始USDT分配额表达的报告数值。

    Args:
        metrics: 逐标的隔离模拟器指标，收益和费用相对初始权益。
        allocation: 该标的分配的初始USDT资金Decimal。

    Returns:
        含净值、回撤、盈亏、资金费、手续费、交易数和持仓时间的JSON字典。
    """
    return {
        "initial_equity": format(allocation, "f"),
        "final_equity": format(allocation * Decimal(str(1 + metrics["net_return"])), "f"),
        "net_return_pct": format(Decimal(str(metrics["net_return"] * 100)), "f"),
        "max_drawdown_pct": format(Decimal(str(metrics["max_drawdown"] * 100)), "f"),
        "trades": int(metrics["trades"]),
        "entries": int(metrics["entries"]),
        "wins": int(round(metrics["win_rate"] * metrics["trades"])),
        "fees_usdt": format(allocation * Decimal(str(metrics["fees_equity_fraction"])), "f"),
        "funding_usdt": format(allocation * Decimal(str(metrics["funding_equity_fraction"])), "f"),
        "holding_hours_mean": float(metrics["holding_hours_mean"]),
        "holding_hours_median": float(metrics["holding_hours_median"]),
        "holding_hours_max": float(metrics["holding_hours_max"]),
        "liquidations": int(metrics["liquidations"]),
        "exit_mode": metrics["exit_mode"],
    }


def _read_asset_sources(db, asset_id, intervals, end_ms):
    """读取一项模拟资产各原生周期已校验的本地交易价K线与数据集hash。

    Args:
        db: 回测后台任务使用的数据库会话。
        asset_id: 精确核验的Binance USDM永续ID。
        intervals: 账号绑定的周期白名单。
        end_ms: 回测右边界UTC毫秒。

    Returns:
        周期到OHLCV DataFrame的映射与对应manifest SHA256映射。

    Raises:
        ValueError: 标的身份不符、历史K线未缓存或数据集损坏。
    """
    bars_by_timeframe, hashes = {}, {}
    for interval in intervals:
        validate_archive_identity(db, asset_id, interval)
        archived = read_archive(asset_id, interval, limit=200_000, end_ms=end_ms)
        if archived is None or not archived["candles"]:
            raise ValueError(f"{asset_id}缺少{interval}本地交易K线归档")
        bars_by_timeframe[interval] = _frame(archived["candles"])
        hashes[interval] = archived["dataset_sha256"]
    return bars_by_timeframe, hashes


def _run_asset(db, asset_id, intervals, start_ms, end_ms, weights, threshold, account, allocation):
    """将一个标的的真实交易价、标记价与资金费率交给逐仓回测器。

    Args:
        db: 回测后台任务数据库会话。
        asset_id: 已核验的Binance USDM永续资产ID。
        intervals: 策略配置原生多周期。
        start_ms: 回测起点UTC毫秒。
        end_ms: 回测终点UTC毫秒。
        weights: 经持久账号配置验证的理论/模型权重。
        threshold: 入场综合方向分门槛。
        account: 已读取且版本哈希匹配的模拟配置账号。
        allocation: 初始USDT资金等额分配值。

    Returns:
        单标的指标、成交表、权益表、数据源hash和可比较的策略分数摘要。
    """
    bars_by_timeframe, market_hashes = _read_asset_sources(db, asset_id, intervals, end_ms)
    configuration = account.configuration
    signals, components, base_interval, high_interval = build_multi_theory_signals(
        bars_by_timeframe,
        weights,
        threshold,
        timeframe_weights=configuration.get("timeframe_weights", {}),
        decision_horizon_hours=int(configuration.get("decision_horizon_hours", 168)),
        horizon_consensus_weight=float(configuration.get("horizon_consensus_weight", 0.25)),
        direction_mode=configuration.get("direction_mode", "trend_following"),
    )
    base = bars_by_timeframe[base_interval]
    all_history = base.loc[base.close_time <= end_ms].reset_index(drop=True)
    signals = signals.loc[all_history.index].reset_index(drop=True)
    components = components.loc[all_history.index].reset_index(drop=True)
    prior_close = all_history.close.shift(1)
    true_range = pd.concat([
        all_history.high - all_history.low,
        (all_history.high - prior_close).abs(),
        (all_history.low - prior_close).abs(),
    ], axis=1).max(axis=1)
    all_history["atr"] = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    executable = all_history.loc[(all_history.close_time >= start_ms) & (all_history.close_time <= end_ms)].copy()
    if executable.empty:
        raise ValueError(f"{asset_id}在所选回测时间窗没有交易K线")
    first_bar_open = int(executable.open_time.iloc[0])
    mark_archive = read_derivative_history(asset_id, base_interval, first_bar_open, end_ms)
    if mark_archive["mark"] is None or mark_archive["funding"] is None:
        raise ValueError(f"{asset_id}缺少{base_interval}本地标记价K线或资金费率归档")
    mark = pd.DataFrame(mark_archive["mark"]["rows"])
    for name in ("open_time", "close_time"):
        mark[name] = pd.to_numeric(mark[name], errors="raise").astype("int64")
    for name in ("open", "high", "low", "close"):
        mark[name] = pd.to_numeric(mark[name], errors="raise").astype(float)
        mark = mark.rename(columns={name: "mark_" + name})
    executable = executable.merge(mark, on=["open_time", "close_time"], how="inner", validate="one_to_one")
    if len(executable) != int(((all_history.close_time >= start_ms) & (all_history.close_time <= end_ms)).sum()):
        raise ValueError(f"{asset_id}交易K线与标记价格K线时间戳不完整对齐")
    selected_indices = all_history.index[(all_history.close_time >= start_ms) & (all_history.close_time <= end_ms)]
    selected_signals = signals.loc[selected_indices].reset_index(drop=True)
    selected_components = components.loc[selected_indices].reset_index(drop=True)
    executable["atr"] = all_history.loc[selected_indices, "atr"].to_numpy(dtype=float)
    executable["higher_trend"] = selected_components[f"trend_{high_interval}"].to_numpy(dtype=np.int8)
    funding = mark_archive["funding"]["rows"]
    funding_events = [{
        "funding_time": int(item["funding_time"]),
        "rate": float(item["rate"]),
        "mark_price": float(item["mark_price"]),
    } for item in funding]
    max_holding_ms = int(account.configuration["max_holding_hours"]) * 3_600_000
    min_holding_ms = int(account.configuration.get("min_holding_hours", 24)) * 3_600_000
    max_holding_bars = max(1, math.ceil(max_holding_ms / INTERVAL_MS[base_interval]))
    exit_mode = EXIT_MODES[account.configuration["exit_mode"]]
    result = simulate_patterns(
        executable,
        selected_signals,
        exit_mode=exit_mode,
        leverage=float(account.configuration["max_leverage"]),
        margin_fraction=float(account.configuration["max_margin_fraction"]),
        fee=float(FEE_RATE),
        slip=float(SLIPPAGE_RATE),
        maintenance_rate=0.01,
        liquidation_fee=0.01,
        atr_stop=INITIAL_STOP_ATR,
        max_holding_bars=max_holding_bars,
        funding_events=funding_events,
        execution_delay_ms=0,
        min_holding_ms=min_holding_ms,
        max_holding_ms=max_holding_ms,
    )
    equity = result["equity"].copy()
    equity["asset_id"] = asset_id
    equity["allocated_equity"] = equity.equity.map(
        lambda value: format(allocation * Decimal(str(value)), "f")
    )
    trades = result["trades"].copy()
    trades["asset_id"] = asset_id
    trades, forecast_evaluation = _attach_forecast_outcomes(trades, selected_components, all_history)
    trades = _format_money_columns(trades, allocation)
    hashes = {
        "market": market_hashes,
        "mark_manifest_sha256": mark_archive["mark"]["manifest_sha256"],
        "mark_segment_sha256": mark_archive["mark"]["segment_sha256"],
        "funding_manifest_sha256": mark_archive["funding"]["manifest_sha256"],
        "funding_segment_sha256": mark_archive["funding"]["segment_sha256"],
    }
    return {
        "asset_id": asset_id,
        "base_interval": base_interval,
        "highest_interval": high_interval,
        "metrics": _metrics_summary(result["metrics"], allocation),
        "raw_metrics": result["metrics"],
        "equity": equity[["close_time", "asset_id", "allocated_equity"]],
        "trades": trades,
        "forecast_definition": {
            "status": "UNCALIBRATED_SCENARIO",
            "horizons_hours": list(FORECAST_HORIZONS_HOURS),
            "decision_horizon_hours": int(configuration.get("decision_horizon_hours", 168)),
            "note": "启发式中心与历史波动情景带，仅作可审计假设，不是概率预测或保证目标价",
        },
        "forecast_evaluation": forecast_evaluation,
        "dataset_hashes": hashes,
    }


def _portfolio_equity(asset_results, asset_ids, initial_equity, start_ms, end_ms):
    """以等额现金子账户合并各标的净值，不共用或超配保证金。

    Args:
        asset_results: 每个成功标的的逐收盘已实现/浮动权益曲线。
        asset_ids: 配置中所有目标标的；缺数据资产保持现金不变。
        initial_equity: 账号Decimal初始USDT资金。
        start_ms: 请求回测起始UTC毫秒。
        end_ms: 请求回测结束UTC毫秒。

    Returns:
        组合UTC毫秒净值和峰值回撤表；图表字段以十进制字符串保存。
    """
    allocation = initial_equity / Decimal(len(asset_ids))
    frames = [item["equity"][["close_time", "asset_id", "allocated_equity"]] for item in asset_results]
    if frames:
        timeline = sorted({int(value) for frame in frames for value in frame.close_time})
    else:
        timeline = []
    if not timeline or timeline[0] > start_ms:
        timeline.insert(0, start_ms)
    if timeline[-1] < end_ms:
        timeline.append(end_ms)
    columns = []
    for asset_id in asset_ids:
        frame = next((item for item in frames if item.asset_id.iloc[0] == asset_id), None)
        if frame is None:
            values = [allocation] * len(timeline)
        else:
            series = frame.set_index("close_time").allocated_equity.map(Decimal)
            aligned = series.reindex(timeline).ffill().fillna(allocation)
            values = [Decimal(value) for value in aligned]
        columns.append(values)
    portfolio = [sum((columns[column][index] for column in range(len(columns))), Decimal("0"))
                 for index in range(len(timeline))]
    peak = portfolio[0]
    drawdowns = []
    for value in portfolio:
        peak = max(peak, value)
        drawdowns.append(Decimal("0") if peak == 0 else Decimal("1") - value / peak)
    return pd.DataFrame({
        "close_time": timeline,
        "equity": [format(value, "f") for value in portfolio],
        "drawdown": [format(value, "f") for value in drawdowns],
    })


def run_simulation_backtest(db, task_id, parameters):
    """执行绑定配置修订的本地合约回测并保存可校验的逐笔/净值文件。

    Args:
        db: 后台RQ任务使用的数据库会话。
        task_id: 持久队列任务UUID，同时作为本次不可变报告目录名。
        parameters: owner/account/config hash/revision及UTC毫秒评估窗参数。

    Returns:
        按账号配置和数据hash绑定的多标的性能摘要、回撤、成交数及文件SHA256。

    Raises:
        ValueError: 配置修订已变更、缺行情、时间窗非法或模型版本不可用。
    """
    account = db.get(SimulationAccount, parameters["account_id"])
    if (account is None or account.owner_user_id != parameters["owner_user_id"]
            or account.revision != parameters["account_revision"]
            or account.configuration_sha256 != parameters["configuration_sha256"]):
        raise ValueError("模拟账号已删除或配置修订已变化；请为当前修订重新排队回测")
    start_ms, end_ms = int(parameters["start_ms"]), int(parameters["end_ms"])
    now_ms = int(time.time() * 1000)
    if end_ms <= start_ms:
        raise ValueError("回测结束时刻必须晚于开始时刻")
    if start_ms < three_year_start(now_ms) or end_ms > now_ms:
        raise ValueError("回测执行边界仅允许最近三年内且不晚于当前时刻")
    configuration = account.configuration
    weights = configuration["weights"]
    if Decimal(str(weights.get("deep_model", "0"))) > 0:
        raise ValueError("当前没有经验证的Web深度模型注册器和版本加载器")
    initial_equity = Decimal(account.initial_equity)
    asset_ids = list(configuration["asset_ids"])
    allocation = initial_equity / Decimal(len(asset_ids))
    results, skipped = [], []
    for asset_id in asset_ids:
        try:
            result = _run_asset(
                db, asset_id, configuration["intervals"], start_ms, end_ms,
                weights, float(configuration["entry_threshold"]), account, allocation,
            )
            results.append(result)
        except (ValueError, OSError) as exc:
            skipped.append({"asset_id": asset_id, "status": "SKIPPED", "reason": str(exc)[:240]})
    equity = _portfolio_equity(results, asset_ids, initial_equity, start_ms, end_ms)
    trades = pd.concat([item["trades"] for item in results], ignore_index=True) if results else pd.DataFrame()
    directory = (DATA_ROOT / "simulation_runs" / task_id).resolve()
    if not directory.is_relative_to(DATA_ROOT.resolve()):
        raise ValueError("回测报告路径越界")
    equity_path, equity_sha = _write_immutable_frame(directory, "equity.parquet", equity)
    trades_path, trades_sha = _write_immutable_frame(directory, "trades.parquet", trades)
    final_equity = Decimal(equity.equity.iloc[-1]) if len(equity) else initial_equity
    net_return = final_equity / initial_equity - Decimal("1")
    max_drawdown = max((Decimal(value) for value in equity.drawdown), default=Decimal("0"))
    return {
        "status": "COMPLETED" if results else "NO_DATA",
        "account_id": account.id,
        "account_name": account.name,
        "account_revision": account.revision,
        "configuration_sha256": account.configuration_sha256,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "initial_equity": format(initial_equity, "f"),
        "final_equity": format(final_equity, "f"),
        "net_return_pct": format(net_return * 100, "f"),
        "max_drawdown_pct": format(max_drawdown * 100, "f"),
        "trade_count": int(len(trades)),
        "fees_assumption": {"taker_rate": str(FEE_RATE), "slippage_rate": str(SLIPPAGE_RATE)},
        "holding_policy": {
        "minimum_hours": int(configuration.get("min_holding_hours", 1)),
            "maximum_hours": configuration["max_holding_hours"],
        },
        "multi_timeframe_policy": {
            "direction_mode": configuration.get("direction_mode", "trend_following"),
            "timeframe_weights": configuration.get("timeframe_weights", {}),
            "decision_horizon_hours": int(configuration.get("decision_horizon_hours", 168)),
            "horizon_consensus_weight": str(configuration.get("horizon_consensus_weight", "0.25")),
        },
        "forecast_definition": {
            "status": "UNCALIBRATED_SCENARIO",
            "horizons_hours": list(FORECAST_HORIZONS_HOURS),
            "scenario_band": "rolling historical volatility scaled by square-root time; not a calibrated confidence interval",
        },
        "margin_assumptions": {
            "maintenance_rate": "0.01",
            "liquidation_fee_rate": "0.01",
            "note": "固定研究假设，不等同Binance按名义价值分档的实际维持保证金表",
        },
        "score_algorithm": "weighted_causal_structure_vote_v1",
        "algorithm_sha256": _algorithm_sha256(),
        "allocation_policy": "equal_cash_subaccounts_missing_assets_remain_cash_v1",
        "per_asset": [{key: value for key, value in item.items() if key not in {"equity", "trades", "raw_metrics"}}
                      for item in results],
        "skipped_assets": skipped,
        "equity_artifact": {"path": equity_path, "sha256": equity_sha},
        "trades_artifact": {"path": trades_path, "sha256": trades_sha},
        "research_only": True,
    }


def read_backtest_artifact(run_id, artifact, limit=5000, root=None):
    """按受控run UUID和manifest SHA读取本地净值或成交报告文件。

    Args:
        run_id: 任务UUID；不接受路径片段或目录分隔符。
        artifact: 结果中的`equity_artifact`或`trades_artifact`记录。
        limit: 返回最近最多5000行，避免超大API响应。
        root: 隔离测试可提供的模拟数据根目录。

    Returns:
        文件SHA匹配且转为JSON安全字符串的报告记录列表。

    Raises:
        ValueError: 路径不受控、SHA错误、文件缺失或读取数量越界。
    """
    if (not isinstance(artifact, dict) or not isinstance(run_id, str) or len(run_id) != 36 or "/" in run_id or "\\" in run_id
            or artifact.get("path") not in {f"simulation_runs/{run_id}/equity.parquet",
                                             f"simulation_runs/{run_id}/trades.parquet"}
            or not 1 <= limit <= 5000):
        raise ValueError("回测报告路径或读取数量无效")
    base = DATA_ROOT if root is None else Path(root)
    path = (base / artifact["path"]).resolve()
    if not path.is_relative_to(base.resolve()) or not path.is_file():
        raise ValueError("回测报告文件不存在或路径越界")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != artifact.get("sha256"):
        raise ValueError("回测报告SHA256校验失败")
    frame = pq.read_table(path).to_pandas()
    selected = frame.tail(limit)
    return selected.astype(object).where(pd.notna(selected), None).to_dict(orient="records")
