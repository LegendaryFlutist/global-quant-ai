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


def build_multi_theory_signals(bars_by_timeframe, weights, entry_threshold):
    """按已知收盘时点组合多周期理论方向并生成下一根K线候选信号。

    Args:
        bars_by_timeframe: 15m至1d的原生闭合K线DataFrame映射，至少含最小周期。
        weights: 权重和为1的趋势、道氏、波浪、背离、反转、深度模型配置。
        entry_threshold: 0至1之间的绝对方向分数门槛，不代表胜率。

    Returns:
        最小周期逐行信号及分项方向分；只在分数与最大配置周期趋势同向时入场。

    Raises:
        ValueError: 周期缺失、阈值无效或请求尚无注册器支持的深度模型。

    Notes:
        周期特征通过已完成收盘时间向下对齐；高周期尚未收盘时沿用最近已知值。
        反转形态只使用最小周期已收盘K线。entry方向在该收盘后交给模拟器下根开盘。
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
    trend_columns = [column for column in components if column.startswith("trend_")]
    dow_columns = [column for column in components if column.startswith("dow_")]
    wave_columns = [column for column in components if column.startswith("wave_")]
    divergence_columns = [column for column in components if column.startswith("divergence_")]
    components["trend_score"] = components[trend_columns].mean(axis=1)
    components["dow123_score"] = components[dow_columns].mean(axis=1)
    components["wave_score"] = components[wave_columns].mean(axis=1)
    components["divergence_score"] = components[divergence_columns].mean(axis=1)
    components["reversal_score"] = np.sign(components[list(PATTERN_COLUMNS)].sum(axis=1))
    weight_map = {
        "trend": "trend_score",
        "dow123": "dow123_score",
        "wave": "wave_score",
        "divergence": "divergence_score",
        "reversal_patterns": "reversal_score",
    }
    components["entry_score"] = sum(
        float(weights.get(weight_name, 0)) * components[column]
        for weight_name, column in weight_map.items()
    )
    high_trend = components[f"trend_{highest_interval}"].to_numpy(dtype=np.int8)
    score = components.entry_score.to_numpy(dtype=float)
    direction = np.sign(score).astype(np.int8)
    permitted = (np.abs(score) >= float(entry_threshold)) & (direction == high_trend) & (high_trend != 0)
    direction = np.where(permitted, direction, 0).astype(np.int8)
    signal_names = np.where(direction > 0, "MULTI_THEORY_LONG", np.where(direction < 0, "MULTI_THEORY_SHORT", ""))
    signals = pd.DataFrame({"direction": direction, "pattern": signal_names})
    components["highest_timeframe_trend"] = high_trend
    components["direction"] = direction
    components["close_time"] = base.close_time.to_numpy(dtype=np.int64)
    return signals, components, base_interval, highest_interval


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
    signals, components, base_interval, high_interval = build_multi_theory_signals(
        bars_by_timeframe, weights, threshold,
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
            "minimum_hours": int(configuration.get("min_holding_hours", 24)),
            "maximum_hours": configuration["max_holding_hours"],
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
