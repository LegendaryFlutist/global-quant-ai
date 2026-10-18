"""冻结GRU验证区间的评分/仓位/减仓对照；不触碰保留留出集。"""

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
import numpy as np
import pandas as pd
from quant_lab.core.paths import PROJECT_ROOT
from quant_lab.models.checkpoints import load_gru_checkpoint
from quant_lab.strategies.pattern_features import (
    prepare_timeframe,
    FEATURE_COLUMNS,
    MINUTES,
)
from quant_lab.backtesting.pattern_execution import simulate_patterns
from quant_lab.risk.policy import load_risk_policy
from quant_lab.risk.scoring import score_entries, daily_volatility
from quant_lab.risk.sizing import position_budget

SYMBOLS = ("KORUUSDT", "CLUSDT", "NVDAUSDT", "BTCUSDT")


def research_symbol(symbol, timeframe="1h"):
    """同一GRU/区间/成本比较三种风险模式；返回历史末条评分、真实成交与曲线。

    Args:
        symbol: 四个优先标的白名单中的精确合约代码。
        timeframe: 15m到4h周期；上级周期严格为其四倍。
    Returns:
        JSON可序列化实验；只评估已用验证窗，不能作为独立样本外证明。
    """
    if symbol not in SYMBOLS or timeframe not in MINUTES:
        raise ValueError("Unknown research selection")
    root = PROJECT_ROOT / "runs/priority_structure_gru_20261018/symbols" / symbol
    prior = json.loads((root / "report.json").read_text())
    left, right = prior["validation_windows"][-1]
    if right > prior["holdout_cut_ms"]:
        raise ValueError("Reserved holdout boundary violation")
    audit = next(
        x
        for x in prior["deep_training"]
        if x["timeframe"] == timeframe and x["fold"] == 3
    )
    if audit["train_last_label_end"] >= left:
        raise ValueError("Training label overlaps validation")
    model, digest = load_gru_checkpoint(
        root / "deep_checkpoints" / timeframe / "fold_3.pt"
    )
    if model.feature_names_ != list(FEATURE_COLUMNS):
        raise ValueError("Feature contract changed")
    data = PROJECT_ROOT / "data" / f"{symbol.lower()}_90d_connector"
    raw = pd.read_csv(data / "bars.csv")
    marks = pd.read_csv(data / "mark_bars.csv")
    funding = pd.read_csv(data / "funding.csv")
    source_hash = hashlib.sha256(
        (data / "bars.csv").read_bytes() + (data / "funding.csv").read_bytes()
    ).hexdigest()
    if source_hash != prior["dataset_sha256"]:
        raise ValueError("Frozen input fingerprint changed")
    if (
        hashlib.sha256((data / "mark_bars.csv").read_bytes()).hexdigest()
        != prior["mark_sha256"]
    ):
        raise ValueError("Mark input fingerprint changed")
    # Cut BEFORE all features and inference; reserved holdout never enters this experiment.
    bars = raw[raw.close_time < right].reset_index(drop=True)
    marks = marks[marks.close_time < right].reset_index(drop=True)
    frame = prepare_timeframe(
        bars,
        marks,
        timeframe,
        execution_delay_ms=900000,
        label_holding_ms=48 * 3600000,
        risk_atr_floor_minutes=1440,
        swing_lookback_hours=168,
        label_min_move_pct=0.02,
    )
    policy = load_risk_policy()
    finite = np.isfinite(frame[list(FEATURE_COLUMNS)].to_numpy(dtype=float)).all(axis=1)
    probabilities = np.tile([0.0, 1.0, 0.0], (len(frame), 1))
    probabilities[finite] = model.predict_proba(
        frame.loc[finite, list(FEATURE_COLUMNS)]
    )
    scores = score_entries(frame, probabilities, min_probability=policy.min_probability)
    sigma = daily_volatility(bars)
    volatility = pd.merge_asof(
        frame[["close_time"]],
        pd.DataFrame({"close_time": bars.close_time, "daily_volatility": sigma}),
        on="close_time",
        direction="backward",
    ).daily_volatility
    plan = pd.DataFrame(
        {
            "close_time": frame.close_time,
            "score": scores.score,
            "daily_volatility": volatility,
        }
    )
    valid = (frame.open_time >= left) & (frame.close_time < right)
    sample = frame.loc[valid].copy()
    plans = plan.loc[valid].copy()
    signals = pd.DataFrame(
        {"direction": scores.loc[valid, "direction"], "pattern": "GRU_SCORE_STRUCTURE"},
        index=sample.index,
    )
    execution = bars[
        (bars.open_time >= int(sample.open_time.iloc[0]))
        & (bars.close_time <= int(sample.close_time.iloc[-1]))
    ].copy()
    mark_subset = marks.set_index("open_time").loc[execution.open_time]
    for name in ("open", "high", "low", "close"):
        execution["mark_" + name] = mark_subset[name].to_numpy()
    kwargs = dict(
        leverage=5,
        margin_fraction=0.1,
        fee=0.0005,
        slip=0.0002,
        maintenance_rate=0.01,
        liquidation_fee=0.01,
        exit_mode="swing_percent",
        execution_data=execution,
        funding_events=funding,
        min_profit_move_pct=0.02,
        price_target_pct=0.05,
        price_stop_pct=0.03,
        execution_delay_ms=900000,
        min_holding_ms=24 * 3600000,
        max_holding_ms=504 * 3600000,
        scale_out_after_ms=168 * 3600000,
        scale_out_interval_ms=48 * 3600000,
        scale_out_fraction=0.25,
        scale_out_deviation_atr=3.0,
    )
    cases = []
    for name, overlay, reduce in [
        ("fixed_5x", False, False),
        ("score_volatility", True, False),
        ("score_volatility_derisk", True, True),
    ]:
        result = simulate_patterns(
            sample,
            signals,
            **kwargs,
            **(
                {"risk_policy": policy, "risk_plan": plans, "risk_reductions": reduce}
                if overlay
                else {}
            ),
        )
        equity = result["equity"]
        stride = max(1, len(equity) // 800)
        curve = pd.concat([equity.iloc[::stride], equity.tail(1)]).drop_duplicates(
            "close_time"
        )
        cases.append(
            dict(
                name=name,
                metrics=result["metrics"],
                curve=curve[["close_time", "equity"]].to_dict("records"),
                trades=json.loads(result["trades"].to_json(orient="records")),
                events=json.loads(result["events"].to_json(orient="records")),
            )
        )
    last = frame.iloc[-1]
    latest = scores.iloc[-1].to_dict()
    vol = float(volatility.iloc[-1])
    stop = min(float(last.atr) * 1.5 / float(last.close), 0.03)
    latest.update(
        as_of_ms=int(last.close_time),
        daily_volatility=vol,
        stop_fraction=stop,
        budget=position_budget(
            latest["score"], vol, stop, int(latest["direction"]), policy=policy
        ),
        actionable=False,
    )
    return dict(
        symbol=symbol,
        timeframe=timeframe,
        higher_minutes=MINUTES[timeframe] * 4,
        model="torch_gru",
        model_sha256=digest,
        model_status=prior["status"],
        dataset_sha256=source_hash,
        train_last_label_end=audit["train_last_label_end"],
        validation_start=left,
        validation_end=right,
        holdout_used=False,
        retrained=False,
        probabilities_calibrated=False,
        latest=latest,
        cases=cases,
    )


def main():
    """运行显式周期研究并原子发布只读Web产物；不覆盖历史批次、不训练或发邮件。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeframe", choices=tuple(MINUTES), default="1h")
    parser.add_argument(
        "--out", type=Path, default=PROJECT_ROOT / "runs/risk_latest/report.json"
    )
    args = parser.parse_args()
    started = int(time.time() * 1000)
    results = []
    for symbol in SYMBOLS:
        print(f"Risk research {symbol} {args.timeframe}", flush=True)
        results.append(research_symbol(symbol, args.timeframe))
    code_paths = [
        PROJECT_ROOT / "quant_lab" / name
        for name in (
            "risk/policy.py",
            "risk/scoring.py",
            "risk/sizing.py",
            "risk/reductions.py",
            "backtesting/pattern_execution.py",
            "strategies/pattern_features.py",
            "models/checkpoints.py",
            "services/risk_research.py",
        )
    ]
    code_sha256 = hashlib.sha256(
        b"".join(path.read_bytes() for path in code_paths)
    ).hexdigest()
    report = dict(
        code_sha256=code_sha256,
        schema_version=1,
        created_at_ms=int(time.time() * 1000),
        started_at_ms=started,
        policy=load_risk_policy().to_dict(),
        evaluation="REUSED_VALIDATION_EXPLORATORY",
        real_orders_enabled=False,
        notes=[
            "仅重复使用原第3验证窗，未触碰保留留出；不是独立样本外业绩。",
            "固定5倍与两种动态模式使用相同GRU候选、成本、成交延迟与持仓规则。",
            "评分不是胜率；所有原模型均未获晋升。历史评分不可用作即时开仓。",
            "逐仓维持保证金按1%研究假设；未核验账户实际档位，跳空损失可超过预算。",
        ],
        results=results,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2)
    archive = args.out.parent / f"report-{started}.json"
    archive.write_text(content)
    temp = args.out.with_suffix(".tmp")
    temp.write_text(content)
    os.replace(temp, args.out)
    print(str(args.out), flush=True)


if __name__ == "__main__":
    main()
