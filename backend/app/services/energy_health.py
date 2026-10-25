"""Display-only energy diagnostics; never an execution or candle calendar."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

CALENDAR_VERSION = "nymex_weekly_reference_20261025_v1"
CHICAGO = ZoneInfo("America/Chicago")


def weekly_energy_session(now_ms):
    """Describe normal CL/NG weekly hours at UTC epoch milliseconds.

    Return Chicago DST-aware boundaries as UTC milliseconds. Holidays, emergency
    closures and Yahoo roll schedules are unverified: REGULAR_WINDOW does not
    assert that trading is open. This diagnostic must not aggregate candles,
    pause acquisition, suppress staleness, or become a historical model feature.
    """
    local = datetime.fromtimestamp(now_ms / 1000, CHICAGO)
    day = local.weekday()
    at16 = local.replace(hour=16, minute=0, second=0, microsecond=0)
    at17 = at16.replace(hour=17)
    start = end = None
    if (day == 4 and local >= at16) or day == 5 or (day == 6 and local < at17):
        status = "WEEKEND"
        start = at16 - timedelta(days=day - 4)
        end = at17 + timedelta(days=6 - day)
    elif day < 4 and at16 <= local < at17:
        status, start, end = "DAILY_BREAK", at16, at17
    else:
        status = "REGULAR_WINDOW"
    return {"status": status, "version": CALENDAR_VERSION, "timezone": "America/Chicago",
        "holiday_calendar_verified": False, "display_only": True,
        "closure_start_at_ms": int(start.timestamp()*1000) if start else None,
        "nominal_reopen_at_ms": int(end.timestamp()*1000) if end else None}


def energy_health(snapshot, state, interval, poll_seconds, now_ms):
    """Separate source age, cache age and durable collector state without I/O.

    Inputs and returned times use UTC milliseconds; thresholds are diagnostic
    policy, not vendor SLA. Old quotes stay stale even during a regular closure.
    Only safe allowlisted error descriptions leave the service boundary.
    """
    source_at = snapshot.get("source_event_at_ms")
    received_at = snapshot.get("updated_at_ms")
    source_age = now_ms - source_at if source_at else None
    cache_age = now_ms - received_at if received_at else None
    max_age = 90*60_000 if interval == "15m" else 8*3600_000
    freshness = ("MISSING" if source_age is None else "INVALID_TIME" if source_age < -60_000
        else "STALE" if source_age > max_age else "DELAYED_REFERENCE")
    cache = ("MISSING" if cache_age is None else "EXPIRED"
        if cache_age < -60_000 or cache_age > (poll_seconds+120)*1000 else "PRESENT")
    next_at = state.get("next_attempt_at_ms")
    overdue = bool(next_at and now_ms > next_at+120_000)
    collector = ("OVERDUE" if overdue else "ERROR" if state.get("status") == "ERROR"
        else "OK" if state.get("status") == "OK" else "WAITING")
    code = state.get("error_code")
    errors = {"UPSTREAM_UNREACHABLE": "公开源连接失败", "SOURCE_VALIDATION": "源数据校验失败",
        "ResearchPublicError": "公开源拒绝或限流，按退避计划重试",
        "ProviderQueueUnavailable": "等待共享数据源配额"}
    error = errors.get(code, "采集失败，后台将按计划重试") if state.get("status") == "ERROR" else None
    if isinstance(code, str) and code.startswith("UPSTREAM_HTTP_") and code[14:].isdigit():
        error = "公开源返回 HTTP " + code[14:]
    session = weekly_energy_session(now_ms)
    closure_at = session["closure_start_at_ms"]
    return {"version": 1, "checked_at_ms": now_ms, "collector_status": collector,
        "collector_error": error, "last_success_at_ms": state.get("last_success_at_ms"),
        "next_attempt_at_ms": next_at, "source_status": freshness,
        "source_age_seconds": source_age // 1000 if source_age is not None else None,
        "cache_status": cache, "session": session,
        "stale_before_closure": bool(closure_at and source_at and closure_at-source_at > max_age),
        "stale": freshness != "DELAYED_REFERENCE" or cache != "PRESENT" or collector in {"ERROR", "OVERDUE"}}
