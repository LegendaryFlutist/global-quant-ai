"""从优先标的的已确认结构事件生成透明的整体周期摘要。"""

import time

from sqlalchemy import select

from app.domains.models import Asset
from app.services.structure_analysis import analyze_archived_structure

TIMEFRAME_WEIGHTS = {"15m": 1.0, "1h": 2.0, "4h": 3.0, "1d": 4.0}
PRIORITY_INTERVALS = tuple(TIMEFRAME_WEIGHTS)
_INTERVAL_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


def _timeframe_score(summary, recent_events):
    """组合一个周期已确认方向与新近背离，输出有界方向分。

    Args:
        summary: 结构算法为一个已闭合原生周期产生的摘要。
        recent_events: 发生时间落在最近八根本周期K线内的确认事件。

    Returns:
        有界[-1,1]方向分及透明分项；不是获利概率或交易信号。
    """
    trend = float(summary.get("trend", 0))
    dow = float(summary.get("dow123_state", 0))
    divergence = 0.0
    divergences = [event for event in recent_events if event["kind"] in (
        "BULLISH_RSI_DIVERGENCE", "BEARISH_RSI_DIVERGENCE"
    )]
    if divergences:
        latest = divergences[-1]["kind"]
        divergence = 0.2 if latest == "BULLISH_RSI_DIVERGENCE" else -0.2 if latest == "BEARISH_RSI_DIVERGENCE" else 0.0
    score = max(-1.0, min(1.0, 0.7 * trend + 0.3 * dow + divergence))
    return {
        "score": round(score, 4),
        "trend_component": trend,
        "dow123_component": dow,
        "recent_divergence_component": divergence,
        "trendline_break_state": summary.get("trendline_break_state", "UNKNOWN"),
        "volatility_20_bar_pct": summary.get("volatility_20_bar_pct"),
        "last_closed_at": summary.get("last_closed_at"),
        "source_sha256": summary.get("source_sha256"),
                "support_resistance": {
                    key: {"state": line.get("state"), "price": str(line["value"]) if line.get("value") is not None else None}
                    for key, line in summary.get("trend_lines", {}).items()
                },
    }


def _label(score):
    """把有界方向分映射为非概率市场状态文字。

    Args:
        score: [-1,1]的结构方向合成分。

    Returns:
        `UPTREND`、`DOWNTREND`或`MIXED`，不表示预期收益。
    """
    if score >= 0.25:
        return "UPTREND"
    if score <= -0.25:
        return "DOWNTREND"
    return "MIXED"


def aggregate_priority_sentiment(db, intervals=PRIORITY_INTERVALS, as_of_ms=None):
    """聚合四个用户优先标的的结构状态并保留缺档/来源信息。

    Args:
        db: 当前会话认证请求使用的SQLAlchemy会话。
        intervals: 需要分析的已支持原生K线周期，按从小到大排序。
        as_of_ms: 可选统一UTC毫秒观察时点；不读取其后K线。

    Returns:
        每标的和总体周期状态、透明方向分、事件证据、数据哈希及缺失清单。

    Notes:
        只读取本地长期归档，不联网。未知周期不进入均值；不足以判断时整体为UNKNOWN。
        方向分是规则汇总而非概率、收益承诺或下单信号。
    """
    if not intervals or any(interval not in TIMEFRAME_WEIGHTS for interval in intervals):
        raise ValueError("情绪摘要只支持15m、1h、4h和1d周期")
    selected = list(dict.fromkeys(intervals))
    assets = list(db.scalars(select(Asset).where(Asset.priority < 4).order_by(Asset.priority, Asset.symbol)))
    asset_reports = []
    for asset in assets:
        try:
            report = analyze_archived_structure(
                db, asset.id, selected, as_of_ms=as_of_ms, limit=10000
            )
        except ValueError:
            report = {"timeframes": {}, "events": [], "missing_archives": list(selected)}
        rows = []
        for timeframe, summary in report.get("timeframes", {}).items():
            if summary.get("status") != "OK":
                continue
            last_closed = summary.get("last_closed_at", 0)
            recent_cutoff = last_closed - _INTERVAL_MS[timeframe] * 8
            events = [
                event for event in report.get("events", [])
                if event.get("timeframe") == timeframe
                and event.get("occurred_at", 0) >= recent_cutoff
                and event.get("confirmed_at", last_closed) <= last_closed
            ]
            rows.append({
                "timeframe": timeframe,
                **_timeframe_score(summary, events),
                "recent_events": [
                    {"kind": event["kind"], "occurred_at": event["occurred_at"], "confirmed_at": event["confirmed_at"]}
                    for event in events[-5:]
                ],
                "wave_candidate": summary.get("wave", {}).get("status", "UNKNOWN"),
            })
        total_weight = sum(TIMEFRAME_WEIGHTS[row["timeframe"]] for row in rows)
        score = (
            sum(row["score"] * TIMEFRAME_WEIGHTS[row["timeframe"]] for row in rows) / total_weight
            if total_weight else None
        )
        asset_reports.append({
            "asset_id": asset.id,
            "symbol": asset.symbol,
            "name": asset.name,
            "priority": asset.priority,
            "status": "OK" if rows else "UNKNOWN",
            "regime": _label(score) if score is not None else "UNKNOWN",
            "signed_score": round(score * 100, 2) if score is not None else None,
            "timeframes": rows,
            "missing_archives": sorted(set(report.get("missing_archives", selected) + [
                timeframe for timeframe, summary in report.get("timeframes", {}).items()
                if summary.get("status") != "OK"
            ])),
        })
    known = [item for item in asset_reports if item["signed_score"] is not None]
    if not known:
        regime, score = "UNKNOWN", None
    else:
        score = sum(item["signed_score"] for item in known) / len(known)
        transitioning = False
        for item in known:
            high_timeframes = {
                row["timeframe"]: row for row in item["timeframes"]
                if row["timeframe"] in ("4h", "1d")
            }
            conflict = (
                "4h" in high_timeframes and "1d" in high_timeframes
                and high_timeframes["4h"]["trend_component"] * high_timeframes["1d"]["trend_component"] < 0
            )
            broken = any(row["trendline_break_state"] == "BROKEN" for row in high_timeframes.values())
            transitioning = transitioning or conflict or broken
        regime = "TRANSITIONING" if transitioning else _label(score)
    return {
        "schema_version": 1,
        "algorithm": "priority_structure_sentiment_v1",
        "as_of_ms": int(as_of_ms if as_of_ms is not None else time.time() * 1000),
        "regime": regime,
        "signed_score": round(score, 2) if score is not None else None,
        "score_meaning": "-100至100的结构方向汇总，不是胜率、收益预测或交易信号",
        "known_asset_count": len(known),
        "unknown_asset_count": len(asset_reports) - len(known),
        "assets": asset_reports,
        "research_only": True,
        "source": "local_verified_binance_archive_only",
    }
