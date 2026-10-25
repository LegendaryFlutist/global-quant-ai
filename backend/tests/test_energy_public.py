"""Isolated energy product, timestamp, cache and durable scheduler regression tests."""
import json
from datetime import datetime, timezone
from decimal import Decimal
import httpx
import pytest
from sqlalchemy import select
from app.adapters.energy_public import parse_yahoo_energy, parse_eia_history, public_response, save_public_evidence
from app.adapters.underlying_public import UnderlyingDataError
from app.adapters.research_public import ResearchPublicError
from app.core.db import SessionLocal
from app.domains.models import Asset, MarketData, Setting
from app.services.energy_market import fetch_energy_history, refresh_streams_once, read_energy_snapshot, energy_status_key
from app.storage.provider_rate_limit import _redis_client

NOW = int(datetime(2026, 10, 22, 12, tzinfo=timezone.utc).timestamp()*1000)


def yahoo_body():
    """Return deterministic native USD/NYM bars plus off-grid quote snapshots."""
    times = [NOW//1000-3600, NOW//1000-1800, NOW//1000-600, NOW//1000-120]
    return {"chart": {"result": [{"meta": {"symbol": "CL=F", "currency": "USD", "instrumentType": "FUTURE",
        "exchangeName": "NYM", "exchangeTimezoneName": "America/New_York", "dataGranularity": "15m",
        "regularMarketTime": NOW//1000-120}, "timestamp": times,
        "indicators": {"quote": [{"open": [70]*4, "high": [72]*4, "low": [69]*4, "close": [71]*4, "volume": [100]*4}]}}]}}


def test_confirmed_native_candles_only():
    """Delayed bars stay provisional; irregular trailing quotes never become candles."""
    closed, forming, meta = parse_yahoo_energy(json.dumps(yahoo_body()), "CL=F", "15m", NOW)
    assert len(closed) == 1 and closed[0]["close"] == Decimal("71")
    assert forming["open_time"] == NOW-1800000
    assert meta["source_event_at_ms"] == NOW-120000
    assert meta["execution_eligible"] is False


@pytest.mark.parametrize("field,value", [("symbol", "NG=F"), ("currency", "CNY"), ("instrumentType", "EQUITY"), ("dataGranularity", "1d")])
def test_wrong_product_rejected(field, value):
    """Reject identity mismatches even when numerical prices appear plausible."""
    body = yahoo_body()
    body["chart"]["result"][0]["meta"][field] = value
    with pytest.raises(UnderlyingDataError):
        parse_yahoo_energy(json.dumps(body), "CL=F", "15m", NOW)


def test_missing_negative_and_duplicate_values():
    """Preserve real negative oil, omit unknown volume, reject duplicate epochs."""
    body = yahoo_body()
    quote = body["chart"]["result"][0]["indicators"]["quote"][0]
    quote.update(open=[-2]*4, high=[-1]*4, low=[-3]*4, close=[-2]*4)
    assert parse_yahoo_energy(json.dumps(body), "CL=F", "15m", NOW)[0][0]["close"] == Decimal(-2)
    quote["volume"][0] = None
    assert parse_yahoo_energy(json.dumps(body), "CL=F", "15m", NOW)[0] == []
    body["chart"]["result"][0]["timestamp"][1] = body["chart"]["result"][0]["timestamp"][0]
    with pytest.raises(UnderlyingDataError):
        parse_yahoo_energy(json.dumps(body), "CL=F", "15m", NOW)


def test_eia_dates_missing_and_negative_spot():
    """Parse Monday–Friday scalars without filling holidays or inventing OHLC."""
    raw = b"""<title>Cushing, OK WTI Spot Price</title><tr><td class='B6'>&nbsp;2026 Oct-19 to Oct-23</td>
    <td class='B3'>-37.63</td><td class='B3'>107.89</td><td class='B3'></td><td class='B3'>NA</td><td class='B3'>--</td></tr>"""
    assert parse_eia_history(raw, "RWTC", "2026-10-01", "2026-10-22") == [
        {"period": "2026-10-19", "value": Decimal("-37.63")}, {"period": "2026-10-20", "value": Decimal("107.89")}]
    with pytest.raises(UnderlyingDataError):
        parse_eia_history(raw, "RNGWHHD", "2026-10-01", "2026-10-22")
    with pytest.raises(UnderlyingDataError):
        parse_eia_history(raw.replace(b"Oct-19", b"Oct-20"), "RWTC", "2026-10-01", "2026-10-22")


def test_http_bounds_retry_and_evidence(monkeypatch):
    """Respect long Retry-After and bound bodies; public raw bytes retain stable SHA."""
    from app.adapters import energy_public

    def limited(request):
        """Return a rate-limit fixture without contacting the network."""
        return httpx.Response(429, headers={"Retry-After": "3601"}, text="secret upstream text")

    with pytest.raises(ResearchPublicError) as error:
        public_response("https://example.test", transport=httpx.MockTransport(limited))
    assert error.value.retry_after_seconds == 3601 and "secret" not in str(error.value)
    monkeypatch.setattr(energy_public, "MAX_BYTES", 2)

    def oversize(request):
        """Return an oversized body to exercise streaming byte limits."""
        return httpx.Response(200, content=b"123")

    with pytest.raises(UnderlyingDataError):
        public_response("https://example.test", transport=httpx.MockTransport(oversize))
    assert save_public_evidence(b"public-price", "eia", NOW) == save_public_evidence(b"public-price", "eia", NOW)


def test_persistence_freshness_and_product_boundary(monkeypatch, logged_in):
    """Write reference bars only; chart calls read cache and recompute UTC age."""
    from app.services import energy_market
    closed, forming, evidence = parse_yahoo_energy(json.dumps(yahoo_body()), "CL=F", "15m", NOW)

    closed[0]["close"] = Decimal("71.010000000000005")

    def fetch(symbol, interval, start, end):
        """Return fixed public candles while asserting the capped history request."""
        assert symbol == "CL=F" and end-start == 59*86400000
        return closed, forming, {**evidence, "provider": "yahoo_energy", "received_at_ms": NOW}

    monkeypatch.setattr(energy_market, "fetch_yahoo_energy", fetch)
    with SessionLocal() as db:
        result = fetch_energy_history(db, "yahoo_energy:CL", "15m", 1095, NOW)
        assert result["source_window_days"] == 59
        from app.services.market_data import read_dataset
        stored = db.scalar(select(MarketData).where(MarketData.asset_id == "yahoo_energy:CL"))
        assert read_dataset(stored)[0]["close"] == Decimal("71.010000000000005")
        assert db.scalar(select(MarketData).where(MarketData.asset_id == "binance_usdm:CLUSDT")) is None
        assert not read_energy_snapshot(db, "yahoo_energy:CL", "15m", NOW)["stale"]
        assert read_energy_snapshot(db, "yahoo_energy:CL", "15m", NOW+500000)["stale"]
        assert db.get(Asset, "yahoo_energy:CL").underlying_id == db.get(Asset, "binance_usdm:CLUSDT").underlying_id
    response = logged_in.post("/api/market/live", json={"asset_id": "yahoo_energy:CL", "interval": "15m"})
    assert response.status_code == 200 and response.json()["source_event_at_ms"] == NOW-120000
    assert response.json()["health"]["session"]["display_only"] is True
    health = response.json()["health"]
    assert health["source_age_seconds"] == (health["checked_at_ms"]-(NOW-120000))//1000


def test_persistent_retry_and_concurrent_stream_lock():
    """Failures do not block other streams; retry clocks and locks survive restart."""
    calls = []

    def candles(db, asset_id, interval, days, now_ms):
        """Fail CL intraday only; capture each native source window without I/O."""
        calls.append((asset_id, interval, days))
        if asset_id == "yahoo_energy:CL" and interval == "15m":
            raise ResearchPublicError("RATE_LIMITED", retry_after_seconds=600)
        return {"last_close_ms": NOW-1000}

    def observations(db, asset_id, interval, days, now_ms):
        """Return a scalar date, never synthesizing intraday bars."""
        calls.append((asset_id, interval, days))
        return {"last_observed_at_ms": NOW-86400000}

    with SessionLocal() as db:
        assert len(refresh_streams_once(db, now_ms=NOW, fetch_candles=candles, fetch_observations=observations)) == 6
        state = db.get(Setting, energy_status_key("yahoo_energy:CL", "15m")).value
        assert state["status"] == "ERROR" and state["next_attempt_at_ms"] == NOW+600000
    with SessionLocal() as db:
        assert refresh_streams_once(db, now_ms=NOW+60000, fetch_candles=candles, fetch_observations=observations) == []
        assert len(calls) == 6
        with _redis_client().lock("energy-stream-lock:yahoo_energy:CL:15m", timeout=180):
            results = refresh_streams_once(db, now_ms=NOW+600000, fetch_candles=candles, fetch_observations=observations)
        assert len(results) == 1 and results[0]["asset_id"] == "yahoo_energy:NG"


def test_transient_failure_retry_is_bounded_and_safe():
    """Intraday retry remains within five minutes; 429's longer deadline is separate."""
    from app.services.energy_market import energy_failure_code
    assert energy_failure_code(UnderlyingDataError("能源公开源 HTTP 503")) == "UPSTREAM_HTTP_503"
    assert energy_failure_code(UnderlyingDataError("能源公开源暂时不可达")) == "UPSTREAM_UNREACHABLE"
    assert energy_failure_code(UnderlyingDataError("unknown upstream secret")) == "SOURCE_VALIDATION"

    def failed(db, asset_id, interval, days, now_ms):
        """Model a transient connection outage, without making network calls."""
        raise UnderlyingDataError("能源公开源暂时不可达")

    with SessionLocal() as db:
        db.add(Setting(key=energy_status_key("yahoo_energy:CL", "15m"), value={
            "status": "ERROR", "error_code": "UnderlyingDataError", "failures": 6,
            "last_attempt_at_ms": NOW-600000, "next_attempt_at_ms": NOW+3000000}))
        db.commit()
        refresh_streams_once(db, now_ms=NOW, fetch_candles=failed, fetch_observations=failed)
        state = db.get(Setting, energy_status_key("yahoo_energy:CL", "15m")).value
        assert state["error_code"] == "UPSTREAM_UNREACHABLE"
        assert state["next_attempt_at_ms"] == NOW+300000


def test_invalid_forming_roll_bar_does_not_block_valid_daily_history():
    """An inconsistent provisional day is excluded, while confirmed corruption still fails."""
    body = yahoo_body()
    result = body["chart"]["result"][0]
    result["meta"]["dataGranularity"] = "1d"
    result["timestamp"] = [NOW//1000-172800, NOW//1000-3600]
    result["indicators"]["quote"] = [{"open": [70, 2.78], "high": [72, 3.16],
        "low": [69, 3.07], "close": [71, 3.11], "volume": [100, 50]}]
    closed, forming, meta = parse_yahoo_energy(json.dumps(body), "CL=F", "1d", NOW)
    assert len(closed) == 1 and forming is None and meta["discarded_provisional_bars"] == 1
    with pytest.raises(UnderlyingDataError):
        parse_yahoo_energy(json.dumps(body), "CL=F", "1d", NOW+172800000)


def test_parser_upgrade_retries_validation_once_without_bypassing_rate_limit():
    """A new parser can retry a validation failure; provider 429 deadlines stay in force."""
    from app.adapters.energy_public import PARSER_VERSION
    with SessionLocal() as db:
        for code, error in (("CL", "SOURCE_VALIDATION"), ("NG", "ResearchPublicError")):
            db.add(Setting(key=energy_status_key(f"yahoo_energy:{code}", "1d"), value={
                "status": "ERROR", "error_code": error, "parser_version": "energy_public_v1",
                "next_attempt_at_ms": NOW+3600000}))
        db.commit()
        calls = []

        def fetch(db, asset_id, interval, days, now_ms):
            """Capture source attempts and return fixed persisted bounds without network."""
            calls.append((asset_id, interval))
            return {"last_close_ms": NOW-1, "last_observed_at_ms": NOW-86400000}

        refresh_streams_once(db, now_ms=NOW, fetch_candles=fetch, fetch_observations=fetch)
        assert ("yahoo_energy:CL", "1d") in calls
        assert ("yahoo_energy:NG", "1d") not in calls
        assert db.get(Setting, energy_status_key("yahoo_energy:CL", "1d")).value["parser_version"] == PARSER_VERSION
        calls.clear()
        refresh_streams_once(db, now_ms=NOW+1000, fetch_candles=fetch, fetch_observations=fetch)
        assert calls == []
