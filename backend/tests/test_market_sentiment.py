"""隔离验证总体情绪仅由本地已确认结构状态构成。"""

from app.core.db import SessionLocal
from app.services import market_sentiment


def _report(trend_4h=1, trend_1d=1, break_state="HOLDING_OR_UNAVAILABLE"):
    """构造不访问网络的四小时/日线确认结构输入。

    Args:
        trend_4h: 4小时已收盘结构方向，取-1、0或1。
        trend_1d: 日线已收盘结构方向，取-1、0或1。
        break_state: 已确认趋势线状态。

    Returns:
        与本地多周期结构服务一致的测试字典和来源hash。
    """
    return {
        "timeframes": {
            interval: {
                "status": "OK",
                "bars": 200,
                "last_closed_at": 1_800_000_000_000,
                "source_sha256": "a" * 64,
                "trend": trend,
                "dow123_state": trend,
                "trendline_break_state": break_state,
                "trend_lines": {"support": {"state": "HOLDING", "value": 99.5}},
                "wave": {"status": "AMBIGUOUS"},
                "latest_divergence": None,
            }
            for interval, trend in (("4h", trend_4h), ("1d", trend_1d))
        },
        "events": [],
        "missing_archives": ["15m", "1h"],
    }


def test_priority_market_sentiment_preserves_unknown_assets_and_explains_score(monkeypatch):
    """验证跨标的摘要既报告方向分来源，也不填补缺行情资产。

    Args:
        monkeypatch: pytest提供的临时替换器，阻止读取归档或访问网络。

    Returns:
        无返回值；缺失资产被伪装或规则汇总不透明时测试失败。
    """
    monkeypatch.setattr(
        market_sentiment,
        "analyze_archived_structure",
        lambda db, asset_id, intervals, as_of_ms, limit: _report()
        if asset_id.endswith("BTCUSDT")
        else {"timeframes": {}, "events": [], "missing_archives": list(intervals)},
    )
    with SessionLocal() as db:
        result = market_sentiment.aggregate_priority_sentiment(db, ("4h", "1d"), as_of_ms=1_800_000_000_000)

    assert result["known_asset_count"] == 1
    assert result["unknown_asset_count"] == 3
    assert result["regime"] == "UPTREND"
    assert result["signed_score"] == 100.0
    assert result["assets"][-1]["symbol"] == "BTCUSDT"
    assert result["assets"][-1]["timeframes"][0]["support_resistance"]["support"]["price"] == "99.5"
    assert result["assets"][0]["regime"] == "UNKNOWN"


def test_opposing_high_cycles_mark_overall_sentiment_transitioning(monkeypatch):
    """验证同一标的4小时与日线方向相反时总体状态标为过渡。

    Args:
        monkeypatch: pytest提供的临时替换器，不读取网络或真实行情文件。

    Returns:
        无返回值；跨周期反向若被折成稳定方向则测试失败。
    """
    monkeypatch.setattr(
        market_sentiment,
        "analyze_archived_structure",
        lambda db, asset_id, intervals, as_of_ms, limit: _report(1, -1)
        if asset_id.endswith("BTCUSDT")
        else {"timeframes": {}, "events": [], "missing_archives": list(intervals)},
    )
    with SessionLocal() as db:
        result = market_sentiment.aggregate_priority_sentiment(db, ("4h", "1d"), as_of_ms=1_800_000_000_000)

    assert result["known_asset_count"] == 1
    assert result["regime"] == "TRANSITIONING"


def test_authenticated_sentiment_endpoint_validates_timeframes(logged_in, monkeypatch):
    """验证情绪摘要REST路由经过会话认证并白名单校验周期参数。

    Args:
        logged_in: 通过登录握手并携带CSRF会话头的测试客户端。
        monkeypatch: pytest提供的服务替换器，避免访问文件/外部行情。

    Returns:
        无返回值；允许未定义周期或错误API响应时测试失败。
    """
    from app.api import market as market_api

    monkeypatch.setattr(
        market_api,
        "aggregate_priority_sentiment",
        lambda db, intervals, as_of_ms: {
            "regime": "UNKNOWN", "signed_score": None,
            "known_asset_count": 0, "unknown_asset_count": 4,
            "assets": [], "research_only": True,
        },
    )
    response = logged_in.get("/api/market/sentiment?intervals=15m%2C4h")
    assert response.status_code == 200
    assert response.json()["research_only"] is True
    assert logged_in.get("/api/market/sentiment?intervals=5m").status_code == 422
