"""Versioned daily-session boundaries for the initial independent research feeds.

The US calendar is the XNYS regular-session schedule from exchange_calendars,
used explicitly as a Nasdaq regular-session approximation. It is not a source
of historical NVDA trading halts or an authorization to simulate executions.
"""

from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache

import exchange_calendars


US_CALENDAR = "US_REGULAR_XNYS_V1"
CRYPTO_CALENDAR = "CRYPTO_24_7_V1"
CALENDAR_VERSION = "exchange_calendars-4.13.2:research_daily_v1"


@lru_cache(maxsize=16)
def _us_calendar(year: int):
    """Cache a fixed calendar-year regular-session schedule without network I/O.

    Args:
        year: Calendar year in the supported 1990..2099 research range.

    Returns:
        XNYS calendar with local holidays, DST and early closes; this is not a
        historical security-specific halt or listing-status database.
    """
    if not 1990 <= year <= 2099:
        raise ValueError("研究日历年份必须在1990至2099之间")
    return exchange_calendars.get_calendar(
        "XNYS", start=f"{year}-01-01", end=f"{year}-12-31",
    )


def session_bounds(session_date: str, calendar_id: str) -> tuple[int, int]:
    """Resolve one session label to inclusive UTC-millisecond candle boundaries.

    Args:
        session_date: Strict ISO date, interpreted in the calendar's local zone.
        calendar_id: US_REGULAR_XNYS_V1 or CRYPTO_24_7_V1; no implicit fallback.

    Returns:
        `(open_time, close_time)` in UTC milliseconds. The closing boundary is
        exclusive minus one millisecond, preserving existing candle conventions.

    Raises:
        ValueError: Unsupported calendar, invalid date or a nontrading US day.
    """
    try:
        day = date.fromisoformat(session_date)
        if day.isoformat() != session_date:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("交易日必须使用YYYY-MM-DD格式") from None
    if calendar_id == CRYPTO_CALENDAR:
        opening = datetime.combine(day, time.min, tzinfo=timezone.utc)
        closing = opening + timedelta(days=1)
    elif calendar_id == US_CALENDAR:
        calendar = _us_calendar(day.year)
        try:
            is_session = calendar.is_session(day.isoformat())
        except ValueError:
            is_session = False  # A holiday can sit outside the year's first/last actual session.
        if not is_session:
            raise ValueError("该日期不是美国常规交易时段")
        opening = calendar.session_open(day.isoformat())
        closing = calendar.session_close(day.isoformat())
    else:
        raise ValueError("未支持的研究交易日历")
    return int(opening.timestamp() * 1000), int(closing.timestamp() * 1000) - 1
