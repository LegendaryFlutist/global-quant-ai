"""Small GET-only daily research feeds with explicit source and session semantics.

Yahoo chart is an unofficial personal-research supplement, not an exchange
license or a source of executable unadjusted prices. Coinbase is one specific
USD spot venue. Neither adapter has credentials, account methods or orders.
"""

import json
import time
from datetime import date, datetime, time as day_time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from math import ceil
from zoneinfo import ZoneInfo

import httpx

from app.services.research_calendar import (
    CALENDAR_VERSION, CRYPTO_CALENDAR, US_CALENDAR, session_bounds,
)


PARSER_VERSION = "research_public_daily_v1"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
SOURCE_CONTRACTS = {
    "yahoo_chart": {
        "symbol": "NVDA", "intervals": ["1d"], "max_page_days": 366,
        "calendar_id": US_CALENDAR, "calendar_version": CALENDAR_VERSION,
        "currency": "USD", "timezone": "America/New_York",
        "price_type": "trade_ohlcv", "adjustment": "provider_native",
        "freshness": "RESEARCH_EOD", "requires_key": False,
        "volume_semantics": "provider_reported_equity_volume_not_verified_consolidated",
        "license": "Yahoo personal research only; no redistribution entitlement verified",
        "calendar_limitation": "XNYS regular sessions approximate Nasdaq; security halts unverified",
        "execution_eligible": False,
        "unsupported": ["quotes", "actions", "depth", "orders", "websocket"],
        "evidence_url": "https://github.com/ranaroussi/yfinance",
    },
    "coinbase_exchange": {
        "symbol": "BTC-USD", "intervals": ["1d"], "max_page_days": 300,
        "calendar_id": CRYPTO_CALENDAR, "calendar_version": CALENDAR_VERSION,
        "currency": "USD", "timezone": "UTC", "price_type": "trade_ohlcv",
        "adjustment": "not_applicable", "freshness": "RESEARCH_EOD",
        "requires_key": False, "volume_semantics": "Coinbase Exchange BTC base-asset volume",
        "license": "Public research endpoint; redistribution entitlement not verified",
        "calendar_limitation": "No-tick intervals may be absent; never synthesized",
        "execution_eligible": False,
        "unsupported": ["quotes", "actions", "depth", "orders", "websocket"],
        "evidence_url": "https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles",
    },
}


class ResearchPublicError(RuntimeError):
    """Sanitized provider failure with machine-readable retry metadata only."""

    def __init__(self, code: str, *, status_code: int | None = None, retry_after_seconds: int | None = None):
        """Keep bounded status metadata without response bodies or request secrets.

        Args:
            code: Internal fixed error category, not an upstream message.
            status_code: Optional upstream HTTP status, never a response object.
            retry_after_seconds: Optional positive provider cooldown in seconds.

        Returns:
            None; the exception contains no provider-controlled error text.
        """
        self.code = code
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"公开研究数据请求失败：{code}" + (f" (HTTP {status_code})" if status_code else ""))


def provider_capabilities(provider_id: str) -> dict:
    """Return a copy of one fixed daily-only provider contract.

    Args:
        provider_id: Whitelisted provider ID, not a hostname or arbitrary URL.

    Returns:
        JSON-safe capabilities, including unsupported functions and research limits.
    """
    if provider_id not in SOURCE_CONTRACTS:
        raise ValueError("未支持的公开研究数据源")
    return json.loads(json.dumps(SOURCE_CONTRACTS[provider_id]))


def _request_spec(provider_id: str, symbol: str, start_date: str, end_date: str, interval: str):
    """Validate a bounded inclusive date window and build one credential-free GET.

    Args:
        provider_id: Fixed Yahoo or Coinbase provider ID.
        symbol: Exact server-bound NVDA or BTC-USD symbol.
        start_date: Inclusive session date in ISO format.
        end_date: Inclusive session date; caller owns pagination across pages.
        interval: Must equal 1d; no upsampling or implicit intraday request.

    Returns:
        `(contract, start, end, endpoint, params)` for one source-native page.
    """
    contract = provider_capabilities(provider_id)
    if symbol != contract["symbol"] or interval != "1d":
        raise ValueError("公开研究源只支持绑定标的的原生日线")
    try:
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
        if start.isoformat() != start_date or end.isoformat() != end_date:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("采集日期必须使用YYYY-MM-DD格式") from None
    if end < start or (end - start).days + 1 > contract["max_page_days"]:
        raise ValueError("采集日期范围无效或超过单页上限")
    if not 1990 <= start.year <= end.year <= 2099:
        raise ValueError("研究日期必须在1990至2099年之间")
    zone = ZoneInfo(contract["timezone"])
    first = datetime.combine(start, day_time.min, tzinfo=zone)
    after = datetime.combine(end + timedelta(days=1), day_time.min, tzinfo=zone)
    if provider_id == "yahoo_chart":
        endpoint = "https://query1.finance.yahoo.com/v8/finance/chart/NVDA"
        params = {"period1": int(first.timestamp()), "period2": int(after.timestamp()),
                  "interval": "1d", "includePrePost": "false", "events": "history"}
    else:
        endpoint = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
        params = {"granularity": 86400, "start": first.isoformat(),
                  "end": (after - timedelta(seconds=1)).isoformat()}
    return contract, start, end, endpoint, params


def _retry_after(value: str | None, observed_at_ms: int) -> int | None:
    """Parse a Retry-After header without retaining arbitrary header contents.

    Args:
        value: Optional decimal seconds or HTTP date from a rejected request.
        observed_at_ms: Actual response receipt instant in UTC milliseconds.

    Returns:
        At least one second when parseable, otherwise None; long waits are not cut.
    """
    if value is None:
        return None
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        try:
            deadline = parsedate_to_datetime(value)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            seconds = ceil(deadline.timestamp() - observed_at_ms / 1000)
        except (TypeError, ValueError, OverflowError):
            return None
    return max(1, seconds)


def _bar(values: dict, session_date: str, contract: dict, observed_at_ms: int) -> dict:
    """Validate one positive-price bar while preserving missing volume as null.

    Args:
        values: Provider-native price/volume values, with no adjustment applied.
        session_date: Exchange-local date, already constrained to the query window.
        contract: Fixed source capability metadata including a versioned calendar.
        observed_at_ms: Actual response receipt UTC milliseconds, never backdated.

    Returns:
        Decimal daily bar with session boundaries and conservative available_at_ms.
    """
    parsed = {}
    try:
        for field in ("open", "high", "low", "close", "volume"):
            parsed[field] = None if field == "volume" and values.get(field) is None else Decimal(str(values[field]))
    except (KeyError, TypeError, ValueError, InvalidOperation):
        raise ResearchPublicError("INVALID_OHLCV") from None
    prices = [parsed[field] for field in ("open", "high", "low", "close")]
    if any(not value.is_finite() or value <= 0 for value in prices):
        raise ResearchPublicError("INVALID_OHLCV")
    if parsed["volume"] is not None and (not parsed["volume"].is_finite() or parsed["volume"] < 0):
        raise ResearchPublicError("INVALID_OHLCV")
    if parsed["high"] < max(prices) or parsed["low"] > min(prices):
        raise ResearchPublicError("INVALID_OHLCV")
    try:
        opening, closing = session_bounds(session_date, contract["calendar_id"])
    except ValueError:
        raise ResearchPublicError("INVALID_SESSION") from None
    return {
        **parsed, "open_time": opening, "close_time": closing, "session_date": session_date,
        "observed_at_ms": observed_at_ms, "available_at_ms": observed_at_ms,
        "source_published_at_ms": None, "is_final": True, "revision": 1,
        "quality_status": "MISSING_VOLUME" if parsed["volume"] is None else "VALIDATED_RESEARCH",
    }


def _yahoo_values(body: dict):
    """Extract aligned raw Yahoo daily fields with exact identity checks.

    Args:
        body: Decoded response using Decimal for JSON floating-point numbers.

    Returns:
        `(timestamp_seconds, OHLCV)` pairs; null-price gaps stay identifiable.
    """
    try:
        chart = body["chart"]
        result = chart["result"]
        if chart.get("error") is not None or not isinstance(result, list) or len(result) != 1:
            raise ValueError
        item = result[0]
        metadata = item["meta"]
        if (metadata.get("symbol") != "NVDA" or metadata.get("currency") != "USD"
                or metadata.get("instrumentType") != "EQUITY"
                or metadata.get("exchangeTimezoneName") != "America/New_York"):
            raise ValueError
        timestamps = item.get("timestamp", [])
        quotes = item["indicators"]["quote"]
        if not isinstance(timestamps, list) or len(quotes) != 1:
            raise ValueError
        values = quotes[0]
        if any(not isinstance(values.get(key), list) or len(values[key]) != len(timestamps)
               for key in ("open", "high", "low", "close")):
            raise ValueError
        volumes = values.get("volume")
        if volumes is not None and (not isinstance(volumes, list) or len(volumes) != len(timestamps)):
            raise ValueError
        return [(timestamp, {**{key: values[key][index] for key in ("open", "high", "low", "close")},
                             "volume": volumes[index] if volumes is not None else None})
                for index, timestamp in enumerate(timestamps)]
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        raise ResearchPublicError("INVALID_PROVIDER_PAYLOAD") from None


def _coinbase_values(body: list):
    """Extract Coinbase's time/low/high/open/close/volume candle format.

    Args:
        body: Decoded single-page JSON list, containing at most 300 native bars.

    Returns:
        `(timestamp_seconds, OHLCV)` pairs without filling no-tick intervals.
    """
    if not isinstance(body, list) or len(body) > 300:
        raise ResearchPublicError("INVALID_PROVIDER_PAYLOAD")
    output = []
    for row in body:
        if not isinstance(row, list) or len(row) != 6:
            raise ResearchPublicError("INVALID_PROVIDER_PAYLOAD")
        timestamp, low, high, opening, closing, volume = row
        output.append((timestamp, {"open": opening, "high": high, "low": low, "close": closing, "volume": volume}))
    return output


def _parse_rows(body, provider_id: str, contract: dict, start: date, end: date, observed_at_ms: int):
    """Normalize closed native days, recording gaps without interpolating prices.

    Args:
        body: Source JSON decoded with Decimal; never used as model features directly.
        provider_id: Whitelisted source selecting one fixed parser.
        contract: Fixed source/session contract.
        start: Inclusive requested local session date.
        end: Inclusive requested local session date.
        observed_at_ms: Actual response receipt time, or a deterministic test clock.

    Returns:
        Sorted unique rows and JSON-safe quality counts; conflicts reject the page.
    """
    items = _yahoo_values(body) if provider_id == "yahoo_chart" else _coinbase_values(body)
    quality = {"received_rows": len(items), "outside_window_rows": 0, "null_price_rows": 0,
               "forming_rows": 0, "missing_volume_rows": 0, "duplicate_rows": 0,
               "historical_available_at": "OBSERVED_ON_DOWNLOAD_NOT_HISTORICAL_RECEIPT"}
    unique = {}
    zone = ZoneInfo(contract["timezone"])
    for timestamp, values in items:
        try:
            seconds = int(timestamp)
            if isinstance(timestamp, bool) or seconds != Decimal(str(timestamp)):
                raise ValueError
            instant = datetime.fromtimestamp(seconds, timezone.utc)
            session_day = instant.astimezone(zone).date()
        except (TypeError, ValueError, OverflowError, InvalidOperation):
            raise ResearchPublicError("INVALID_PROVIDER_TIMESTAMP") from None
        if not start <= session_day <= end:
            quality["outside_window_rows"] += 1
            continue
        if provider_id == "coinbase_exchange" and seconds % 86400:
            raise ResearchPublicError("INVALID_PROVIDER_TIMESTAMP")
        if any(values[key] is None for key in ("open", "high", "low", "close")):
            quality["null_price_rows"] += 1
            continue
        row = _bar(values, session_day.isoformat(), contract, observed_at_ms)
        if row["close_time"] >= observed_at_ms:
            quality["forming_rows"] += 1
            continue
        previous = unique.get(row["open_time"])
        if previous is not None:
            if previous != row:
                raise ResearchPublicError("CONFLICTING_DUPLICATE_BAR")
            quality["duplicate_rows"] += 1
            continue
        unique[row["open_time"]] = row
        quality["missing_volume_rows"] += int(row["volume"] is None)
    rows = [unique[key] for key in sorted(unique)]
    quality["accepted_rows"] = len(rows)
    return rows, quality


def fetch_research_page(
    provider_id: str, symbol: str, start_date: str, end_date: str, *,
    interval: str = "1d", transport=None, now_ms: int | None = None,
) -> dict:
    """Fetch one bounded public daily page and return raw evidence plus native bars.

    Args:
        provider_id: yahoo_chart or coinbase_exchange; the host cannot be supplied.
        symbol: Fixed NVDA or BTC-USD provider symbol matching the chosen source.
        start_date: Inclusive exchange-local ISO session date.
        end_date: Inclusive date; at most 366 Yahoo or 300 Coinbase calendar days.
        interval: Only 1d is supported; unsupported requests fail before networking.
        transport: Optional HTTPX mock transport, used by offline acceptance tests.
        now_ms: Optional test clock in UTC milliseconds; production samples receipt
            time after the response and never infers historical receipt times.

    Returns:
        A dictionary with rows, raw_bytes, endpoint, sanitized request_parameters,
        observed_at_ms, parser_version, capabilities and quality metadata. Empty
        valid pages are reported honestly and must not advance data coverage.

    Raises:
        ResearchPublicError: Sanitized HTTP/network/JSON/quality failure. The client
            does not retry a rejection, switch hosts, rotate proxies or add cookies.
    """
    contract, start, end, endpoint, params = _request_spec(provider_id, symbol, start_date, end_date, interval)
    if now_ms is not None and (not isinstance(now_ms, int) or isinstance(now_ms, bool) or now_ms < 0):
        raise ValueError("测试时钟必须是非负UTC毫秒整数")
    try:
        with httpx.Client(timeout=25, follow_redirects=False, transport=transport,
                          headers={"User-Agent": "global-quant-ai/1.0 personal-research"}) as client:
            with client.stream("GET", endpoint, params=params) as response:
                observed_at_ms = now_ms if now_ms is not None else time.time_ns() // 1_000_000
                if response.status_code != 200:
                    code = "RATE_LIMITED" if response.status_code == 429 else "HTTP_REJECTED"
                    raise ResearchPublicError(code, status_code=response.status_code,
                                              retry_after_seconds=_retry_after(response.headers.get("Retry-After"), observed_at_ms))
                chunks = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise ResearchPublicError("RESPONSE_TOO_LARGE")
                    chunks.append(chunk)
                raw_bytes = b"".join(chunks)
                observed_at_ms = now_ms if now_ms is not None else time.time_ns() // 1_000_000
    except ResearchPublicError:
        raise
    except httpx.HTTPError:
        raise ResearchPublicError("NETWORK_UNAVAILABLE") from None
    try:
        body = json.loads(raw_bytes, parse_float=Decimal)
    except (ValueError, UnicodeDecodeError):
        raise ResearchPublicError("INVALID_JSON") from None
    rows, quality = _parse_rows(body, provider_id, contract, start, end, observed_at_ms)
    return {"provider_id": provider_id, "symbol": symbol, "interval": interval,
            "raw_bytes": raw_bytes, "endpoint": endpoint, "request_parameters": params,
            "observed_at_ms": observed_at_ms, "parser_version": PARSER_VERSION,
            "capabilities": contract, "rows": rows, "quality": quality}
