"""Offline contracts for independent Yahoo equity and Coinbase spot daily pages."""

import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from app.adapters.research_public import (
    ResearchPublicError, fetch_research_page, provider_capabilities,
)
from app.services.research_calendar import (
    CRYPTO_CALENDAR, US_CALENDAR, session_bounds,
)


def _ms(iso_time: str) -> int:
    """Convert a fixed UTC fixture timestamp into integer milliseconds.

    Args:
        iso_time: ISO datetime including a UTC offset, never the wall clock.

    Returns:
        Exact fixture epoch milliseconds used for independent expected boundaries.
    """
    return int(datetime.fromisoformat(iso_time).timestamp() * 1000)


def _yahoo_body(timestamps, *, volume=None):
    """Build minimal native Yahoo fixtures without introducing real market claims.

    Args:
        timestamps: Supplied provider opening instants in integer seconds.
        volume: Optional aligned list; omitted volume models missing information.

    Returns:
        JSON-safe response containing distinct synthetic prices and exact metadata.
    """
    quotes = {"open": [100] * len(timestamps), "high": [103] * len(timestamps),
              "low": [99] * len(timestamps), "close": [102] * len(timestamps)}
    if volume is not None:
        quotes["volume"] = volume
    return {"chart": {"error": None, "result": [{
        "meta": {"symbol": "NVDA", "currency": "USD", "instrumentType": "EQUITY",
                 "exchangeTimezoneName": "America/New_York"},
        "timestamp": timestamps, "indicators": {"quote": [quotes]},
    }]}}


def test_research_calendar_respects_us_dst_and_early_close():
    """Verify regular US session boundaries against fixed clock-time expectations.

    Returns:
        None; fixtures cover both sides of DST and the Thanksgiving half day.
    """
    assert session_bounds("2024-03-08", US_CALENDAR) == (
        _ms("2024-03-08T14:30:00+00:00"), _ms("2024-03-08T21:00:00+00:00") - 1,
    )
    assert session_bounds("2024-03-11", US_CALENDAR) == (
        _ms("2024-03-11T13:30:00+00:00"), _ms("2024-03-11T20:00:00+00:00") - 1,
    )
    assert session_bounds("2024-11-29", US_CALENDAR) == (
        _ms("2024-11-29T14:30:00+00:00"), _ms("2024-11-29T18:00:00+00:00") - 1,
    )


def test_research_calendar_rejects_holidays_and_preserves_crypto_weekends():
    """Distinguish exchange closure from a valid 24/7 spot trading day.

    Returns:
        None; holidays/weekends are never filled with synthetic US session bars.
    """
    for day in ("2024-01-01", "2024-07-04", "2024-03-10"):
        with pytest.raises(ValueError, match="常规交易"):
            session_bounds(day, US_CALENDAR)
    assert session_bounds("2024-03-10", CRYPTO_CALENDAR) == (
        _ms("2024-03-10T00:00:00+00:00"), _ms("2024-03-11T00:00:00+00:00") - 1,
    )
    with pytest.raises(ValueError, match="日历"):
        session_bounds("2024-03-11", "UNKNOWN")
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        session_bounds("20240311", US_CALENDAR)


def test_yahoo_preserves_decimal_raw_evidence_and_observed_availability():
    """Retain response bytes and price precision while using real session boundaries.

    Returns:
        None; historical receipt is not invented from the historical candle date.
    """
    observed = _ms("2024-03-12T00:00:00+00:00")
    payload = _yahoo_body([_ms("2024-03-11T13:30:00+00:00") // 1000], volume=[123])
    raw = json.dumps(payload).replace('"open": [100]', '"open": [100.123456789012]').encode()
    requests = []

    def handler(request):
        """Capture an anonymous fixed-host GET and return deterministic raw bytes."""
        requests.append(request)
        return httpx.Response(200, content=raw)

    result = fetch_research_page("yahoo_chart", "NVDA", "2024-03-11", "2024-03-11",
                                 transport=httpx.MockTransport(handler), now_ms=observed)
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.host == "query1.finance.yahoo.com"
    assert "authorization" not in requests[0].headers and "cookie" not in requests[0].headers
    assert result["raw_bytes"] == raw
    assert result["request_parameters"]["includePrePost"] == "false"
    row = result["rows"][0]
    assert row["open"] == Decimal("100.123456789012")
    assert row["open_time"] == _ms("2024-03-11T13:30:00+00:00")
    assert row["close_time"] == _ms("2024-03-11T20:00:00+00:00") - 1
    assert row["session_date"] == "2024-03-11"
    assert row["available_at_ms"] == observed == row["observed_at_ms"]
    assert row["source_published_at_ms"] is None
    assert result["capabilities"]["adjustment"] == "provider_native"
    assert result["capabilities"]["execution_eligible"] is False


def test_yahoo_excludes_forming_sessions_and_preserves_unknown_volume():
    """Keep null volume distinct from zero and reject current half-day completion.

    Returns:
        None; an unclosed daily bar is not available for historical decisions.
    """
    payload = _yahoo_body([
        _ms("2024-11-27T14:30:00+00:00") // 1000,
        _ms("2024-11-29T14:30:00+00:00") // 1000,
    ])
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    result = fetch_research_page("yahoo_chart", "NVDA", "2024-11-27", "2024-11-29",
                                 transport=transport, now_ms=_ms("2024-11-29T17:59:00+00:00"))
    assert [row["session_date"] for row in result["rows"]] == ["2024-11-27"]
    assert result["rows"][0]["volume"] is None
    assert result["rows"][0]["quality_status"] == "MISSING_VOLUME"
    assert result["quality"]["forming_rows"] == 1
    assert result["quality"]["missing_volume_rows"] == 1


def test_coinbase_native_ordering_gaps_and_product_are_preserved():
    """Decode low/high/open/close correctly without filling a missing BTC day.

    Returns:
        None; provider-specific BTC/USD spot data remains separate from USDT futures.
    """
    first = _ms("2024-03-08T00:00:00+00:00") // 1000
    third = _ms("2024-03-10T00:00:00+00:00") // 1000
    payload = [[third, 60, 80, 65, 75, 3], [first, 40, 60, 45, 55, 2]]
    captured = []

    def handler(request):
        """Capture one spot-only request and return reverse-ordered fixture candles."""
        captured.append(request)
        return httpx.Response(200, json=payload)

    result = fetch_research_page("coinbase_exchange", "BTC-USD", "2024-03-08", "2024-03-10",
                                 transport=httpx.MockTransport(handler), now_ms=_ms("2024-03-12T00:00:00+00:00"))
    assert captured[0].url.host == "api.exchange.coinbase.com"
    assert captured[0].url.path == "/products/BTC-USD/candles"
    assert [row["session_date"] for row in result["rows"]] == ["2024-03-08", "2024-03-10"]
    assert result["rows"][0]["open"] == Decimal("45")
    assert result["rows"][0]["low"] == Decimal("40")
    assert result["rows"][0]["close"] == Decimal("55")
    assert result["rows"][0]["volume"] == Decimal("2")
    assert result["capabilities"]["currency"] == "USD"


def test_research_sources_reject_unsupported_identity_interval_and_window():
    """Reject wrong products, unsupported resolutions and oversized pages before I/O.

    Returns:
        None; the allowlist cannot be turned into an arbitrary outbound request.
    """
    def never_request(request):
        """Fail immediately if invalid public input reaches a network transport."""
        raise AssertionError("invalid input reached transport")

    transport = httpx.MockTransport(never_request)
    requests = [
        ("coinbase_exchange", "BTCUSDT", "2024-01-01", "2024-01-02", "1d"),
        ("yahoo_chart", "TSLA", "2024-01-01", "2024-01-02", "1d"),
        ("yahoo_chart", "NVDA", "2024-01-01", "2024-01-02", "15m"),
        ("coinbase_exchange", "BTC-USD", "2024-01-01", "2024-12-31", "1d"),
        ("yahoo_chart", "NVDA", "20240101", "2024-01-02", "1d"),
        ("https://example.com", "NVDA", "2024-01-01", "2024-01-02", "1d"),
    ]
    for provider, symbol, start, end, interval in requests:
        with pytest.raises(ValueError):
            fetch_research_page(provider, symbol, start, end, interval=interval, transport=transport)


@pytest.mark.parametrize("status,code", [(429, "RATE_LIMITED"), (403, "HTTP_REJECTED"), (451, "HTTP_REJECTED")])
def test_research_rejections_are_sanitized_and_not_retried(status, code):
    """Preserve cooldown/status metadata without repeating a denied request.

    Args:
        status: Provider rejection code from the fixed parameterized fixtures.
        code: Expected safe internal failure category.

    Returns:
        None; upstream bodies and locations are absent from exception messages.
    """
    calls = []

    def handler(request):
        """Return one denied fixture response containing a sentinel private string."""
        calls.append(request)
        return httpx.Response(status, text="private-provider-error-content",
                              headers={"Retry-After": "125", "Location": "https://unexpected.example"})

    with pytest.raises(ResearchPublicError) as failure:
        fetch_research_page("coinbase_exchange", "BTC-USD", "2024-03-08", "2024-03-08",
                            transport=httpx.MockTransport(handler), now_ms=_ms("2024-03-09T00:00:00+00:00"))
    assert len(calls) == 1
    assert failure.value.code == code
    assert failure.value.status_code == status
    assert failure.value.retry_after_seconds == 125
    assert "private-provider-error-content" not in str(failure.value)
    assert "unexpected.example" not in str(failure.value)


def test_research_duplicate_conflicts_and_bad_ohlc_are_rejected():
    """Reject revisions within one response and impossible native OHLC ranges.

    Returns:
        None; differing duplicates never silently overwrite another quote.
    """
    timestamp = _ms("2024-03-08T00:00:00+00:00") // 1000
    scenarios = [
        ([[timestamp, 40, 60, 45, 55, 2], [timestamp, 40, 60, 45, 56, 2]], "CONFLICTING_DUPLICATE_BAR"),
        ([[timestamp, 40, 50, 45, 55, 2]], "INVALID_OHLCV"),
        ([[timestamp + 1, 40, 60, 45, 55, 2]], "INVALID_PROVIDER_TIMESTAMP"),
    ]
    for payload, code in scenarios:
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
        with pytest.raises(ResearchPublicError) as failure:
            fetch_research_page("coinbase_exchange", "BTC-USD", "2024-03-08", "2024-03-08",
                                transport=transport, now_ms=_ms("2024-03-09T00:00:00+00:00"))
        assert failure.value.code == code


def test_research_empty_and_duplicate_pages_are_not_fabricated():
    """Allow legitimate empty pages and deduplicate only identical source rows.

    Returns:
        None; an empty page has no invented coverage or zero-volume candles.
    """
    timestamp = _ms("2024-03-08T00:00:00+00:00") // 1000
    for payload, count in [([], 0), ([[timestamp, 40, 60, 45, 55, 0]] * 2, 1)]:
        result = fetch_research_page("coinbase_exchange", "BTC-USD", "2024-03-08", "2024-03-08",
                                     transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
                                     now_ms=_ms("2024-03-09T00:00:00+00:00"))
        assert len(result["rows"]) == count
        assert result["quality"]["accepted_rows"] == count
        assert result["quality"]["missing_volume_rows"] == 0
    assert result["quality"]["duplicate_rows"] == 1
    assert result["rows"][0]["volume"] == Decimal("0")


def test_research_capabilities_are_copied_and_explicitly_nonexecuting():
    """Keep immutable source semantics and unsupported methods visible to callers.

    Returns:
        None; modifying returned metadata cannot expand the server allowlist.
    """
    first = provider_capabilities("yahoo_chart")
    first["intervals"].append("1m")
    assert provider_capabilities("yahoo_chart")["intervals"] == ["1d"]
    assert "actions" in first["unsupported"]
    assert first["execution_eligible"] is False
