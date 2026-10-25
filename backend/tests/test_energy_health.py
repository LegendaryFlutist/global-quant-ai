"""Clock-based diagnostics tested without upstream requests or production writes."""

from datetime import datetime
import pytest
from app.services.energy_health import energy_health, weekly_energy_session


def epoch(value):
    """Convert an explicit UTC ISO fixture to milliseconds; never use host timezone."""
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()*1000)


@pytest.mark.parametrize("time,status,reopen", [
    ("2026-10-23T20:59:59Z", "REGULAR_WINDOW", None),
    ("2026-10-23T21:00:00Z", "WEEKEND", "2026-10-25T22:00:00Z"),
    ("2026-10-25T21:59:59Z", "WEEKEND", "2026-10-25T22:00:00Z"),
    ("2026-10-25T22:00:00Z", "REGULAR_WINDOW", None),
    ("2026-10-26T21:00:00Z", "DAILY_BREAK", "2026-10-26T22:00:00Z"),
    ("2026-10-26T22:00:00Z", "REGULAR_WINDOW", None),
    ("2026-10-31T12:00:00Z", "WEEKEND", "2026-11-01T23:00:00Z"),
    ("2026-03-07T12:00:00Z", "WEEKEND", "2026-03-08T22:00:00Z"),
    ("2026-12-25T12:00:00Z", "REGULAR_WINDOW", None),
])
def test_weekly_boundaries_and_dst(time, status, reopen):
    """Normal hours respect DST; Christmas is explicitly not verified as open."""
    result = weekly_energy_session(epoch(time))
    assert result["status"] == status
    assert result["nominal_reopen_at_ms"] == (epoch(reopen) if reopen else None)
    assert result["holiday_calendar_verified"] is False and result["display_only"] is True


def test_closed_market_does_not_hide_failure_or_old_price():
    """A successful Sunday poll cannot make Friday's quote current or hide errors."""
    now = epoch("2026-10-25T02:00:00Z")
    quote = epoch("2026-10-23T20:59:58Z")
    snapshot = {"source_event_at_ms": quote, "updated_at_ms": now}
    state = {"status": "OK", "next_attempt_at_ms": now+300000, "last_success_at_ms": now}
    health = energy_health(snapshot, state, "15m", 300, now)
    assert health["collector_status"] == "OK" and health["source_status"] == "STALE"
    assert health["stale"] and health["session"]["status"] == "WEEKEND"
    assert not health["stale_before_closure"]
    state.update(status="ERROR", error_code="UPSTREAM_UNREACHABLE")
    health = energy_health(snapshot, state, "15m", 300, now)
    assert health["collector_status"] == "ERROR" and health["collector_error"] == "公开源连接失败"
    snapshot["source_event_at_ms"] = quote-86400000
    assert energy_health(snapshot, state, "15m", 300, now)["stale_before_closure"]


def test_retry_deadline_stalled_collector_and_missing_cache():
    """Long provider cooldown is honored; late workers and expired cache stay distinct."""
    now = epoch("2026-10-22T12:00:00Z")
    snapshot = {"source_event_at_ms": now-60000, "updated_at_ms": now-600000}
    state = {"status": "ERROR", "error_code": "ResearchPublicError", "next_attempt_at_ms": now+3600000}
    health = energy_health(snapshot, state, "15m", 300, now)
    assert health["collector_status"] == "ERROR" and health["cache_status"] == "EXPIRED"
    assert health["source_status"] == "DELAYED_REFERENCE" and health["stale"]
    state["next_attempt_at_ms"] = now-120001
    assert energy_health(snapshot, state, "15m", 300, now)["collector_status"] == "OVERDUE"
    health = energy_health({}, {}, "15m", 300, now)
    assert health["collector_status"] == "WAITING" and health["cache_status"] == "MISSING" and health["stale"]


def test_future_timestamps_and_safe_error_output():
    """Future quote timestamps cannot be fresh, and unknown exception text is hidden."""
    now = epoch("2026-10-22T12:00:00Z")
    snapshot = {"source_event_at_ms": now+60001, "updated_at_ms": now}
    health = energy_health(snapshot, {"status": "ERROR", "error_code": "private text"}, "15m", 300, now)
    assert health["source_status"] == "INVALID_TIME" and health["stale"]
    assert "private" not in str(health)
    health = energy_health(snapshot, {"status": "ERROR", "error_code": "UPSTREAM_HTTP_503"}, "15m", 300, now)
    assert health["collector_error"] == "公开源返回 HTTP 503"
