"""Credential-free energy references; never an exchange execution price feed."""

import hashlib
import html
import json
import re
import os
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import httpx

from app.adapters.underlying_public import UnderlyingDataError
from app.adapters.research_public import ResearchPublicError, _retry_after
from app.core.config import DATA_ROOT
from app.storage.provider_rate_limit import provider_request_slot

ENERGY_SYMBOLS = {"CL": "CL=F", "NG": "NG=F"}
EIA_PAGES = {
    "RWTC": ("https://www.eia.gov/dnav/pet/hist/RWTCd.htm", "Cushing, OK WTI Spot Price"),
    "RNGWHHD": ("https://www.eia.gov/dnav/ng/hist/rngwhhdd.htm", "Henry Hub Natural Gas Spot Price"),
}
MAX_BYTES = 4 * 1024 * 1024


def public_response(url, params=None, *, transport=None):
    """Read one fixed caller-selected public GET, bounded to 4 MiB and 20 seconds.

    Return raw bytes and actual receipt UTC milliseconds; redirects and arbitrary
    provider error text are rejected. Proxy environment is honored without logging.
    """
    try:
        with httpx.Client(timeout=20, follow_redirects=False, transport=transport) as client:
            with client.stream("GET", url, params=params, headers={"User-Agent": "GlobalQuantAI/1.0 personal-research"}) as response:
                if response.status_code == 429:
                    raise ResearchPublicError("RATE_LIMITED", status_code=429,
                        retry_after_seconds=_retry_after(response.headers.get("Retry-After"), int(time.time()*1000)) or 300)
                if response.status_code != 200:
                    raise UnderlyingDataError(f"能源公开源 HTTP {response.status_code}")
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise UnderlyingDataError("能源公开响应超过大小限制")
                    chunks.append(chunk)
                return b"".join(chunks), int(time.time()*1000)
    except httpx.HTTPError:
        raise UnderlyingDataError("能源公开源暂时不可达") from None


def save_public_evidence(raw, provider, received_ms):
    """Persist content-addressed public bytes and return digest/path/receipt metadata.

    Raw bodies contain public prices only. Immutable files are never overwritten;
    callers store their returned pointer with parsed Parquet provenance.
    """
    digest = hashlib.sha256(raw).hexdigest()
    path = DATA_ROOT / "energy-public-raw" / f"{digest}.bin"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".raw-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("能源源文件完整性校验失败") from None
    finally:
        os.unlink(temporary)
    return {"provider": provider, "raw_sha256": digest, "raw_path": str(path.relative_to(DATA_ROOT)),
            "received_at_ms": received_ms, "parser_version": "energy_public_v1"}


def parse_yahoo_energy(raw, symbol, interval, received_ms):
    """Return real native bars and source metadata for whitelisted USD energy references.

    Intraday trailing quote snapshots are discarded unless on the 15-minute grid.
    Bars remain provisional until their duration plus a conservative 30-minute
    delay has elapsed. Daily close bounds are conservative 24h labels, not a
    verified futures session calendar. Missing bars/volume are never fabricated.
    """
    if symbol not in ENERGY_SYMBOLS.values() or interval not in {"15m", "1d"}:
        raise ValueError("未登记的能源参考产品或周期")
    try:
        result = json.loads(raw, parse_float=Decimal)["chart"]["result"][0]
        meta = result["meta"]
        if (meta["symbol"] != symbol or meta["currency"] != "USD"
                or meta["instrumentType"] != "FUTURE" or meta["exchangeName"] != "NYM"
                or meta["exchangeTimezoneName"] != "America/New_York"
                or meta.get("dataGranularity") != interval):
            raise ValueError
        timestamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]
        fields = ("open", "high", "low", "close", "volume")
        if any(len(quote[field]) != len(timestamps) for field in fields):
            raise ValueError
        duration = 900_000 if interval == "15m" else 86_400_000
        closed, forming, previous = [], None, None
        for index, timestamp in enumerate(timestamps):
            if not isinstance(timestamp, int) or isinstance(timestamp, bool) or timestamp < 0:
                raise ValueError
            if previous is not None and timestamp <= previous:
                raise ValueError
            previous = timestamp
            if interval == "15m" and timestamp % 900:
                continue  # Yahoo's final tick is a quote snapshot, not a native candle.
            values = {field: quote[field][index] for field in fields}
            if any(value is None for value in values.values()):
                continue
            values = {field: Decimal(str(value)) for field, value in values.items()}
            if any(not value.is_finite() for value in values.values()) or values["volume"] < 0:
                raise ValueError
            if values["high"] < max(values[k] for k in fields[:4]) or values["low"] > min(values[k] for k in fields[:4]):
                raise ValueError
            opening = timestamp * 1000
            row = {"open_time": opening, "close_time": opening + duration - 1, **values}
            if row["close_time"] + 1_800_000 < received_ms:
                closed.append(row)
            elif opening <= received_ms:
                forming = {**row, "is_closed": False}
        event_ms = int(meta.get("regularMarketTime", 0)) * 1000
        if event_ms <= 0 or event_ms > received_ms + 60_000:
            raise ValueError
        return closed, forming, {"source_event_at_ms": event_ms,
            "contract_label": str(meta.get("shortName", ""))[:120],
            "source_freshness": "Yahoo延迟参考行情；延迟分钟数未承诺；连续代码换月规则未核验",
            "session_calendar": "UNVERIFIED_CONSERVATIVE_24H_DAILY", "execution_eligible": False}
    except (KeyError, IndexError, TypeError, ValueError, InvalidOperation):
        raise UnderlyingDataError("Yahoo能源产品身份或K线校验失败") from None


def fetch_yahoo_energy(symbol, interval, start_ms, end_ms, *, transport=None):
    """Fetch a bounded native page with shared Yahoo pacing; return bars and raw proof.

    UTC millisecond bounds are exclusive at the end. The caller caps intraday
    history at 59 days; daily windows at 3 years. No cookie or block bypass exists.
    """
    if symbol not in ENERGY_SYMBOLS.values() or interval not in {"15m", "1d"}:
        raise ValueError("未登记的能源参考源")
    max_days = 59 if interval == "15m" else 1095
    if end_ms <= start_ms or end_ms - start_ms > max_days * 86_400_000:
        raise ValueError("能源源历史窗口超出限制")
    with provider_request_slot("yahoo_chart", 15, max_wait_seconds=30):
        raw, received = public_response(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            {"period1": start_ms//1000, "period2": end_ms//1000, "interval": interval}, transport=transport)
    closed, forming, metadata = parse_yahoo_energy(raw, symbol, interval, received)
    closed = [row for row in closed if start_ms <= row["open_time"] < end_ms]
    return closed, forming, {**metadata, **save_public_evidence(raw, "yahoo_energy", received)}


def parse_eia_history(raw, series, start_date, end_date):
    """Parse official Monday–Friday scalar rows; preserve holidays, negatives and dates.

    Inclusive ISO date bounds filter actual published values. A changed table or
    identity fails closed; no daily close is converted into an OHLC candle.
    """
    if series not in EIA_PAGES:
        raise ValueError("未登记的EIA公开序列")
    text = raw.decode("utf-8")
    if EIA_PAGES[series][1] not in text:
        raise UnderlyingDataError("EIA公开表身份不匹配")
    lower, upper = date.fromisoformat(start_date), date.fromisoformat(end_date)
    output = {}
    for block in re.findall(r"<tr\b[^>]*>(.*?)</tr>", text, re.I | re.S):
        label = re.search(r"class=['\"]B6['\"][^>]*>(.*?)</td>", block, re.I | re.S)
        if not label:
            continue
        label = html.unescape(re.sub(r"<[^>]+>", "", label[1])).strip()
        match = re.fullmatch(r"(\d{4}) ([A-Za-z]{3})-\s*(\d{1,2}) to ([A-Za-z]{3})-\s*(\d{1,2})", label)
        cells = re.findall(r"class=['\"]B3['\"][^>]*>(.*?)</td>", block, re.I | re.S)
        if not match or len(cells) != 5:
            raise UnderlyingDataError("EIA公开表结构发生变化")
        monday = datetime.strptime(f"{match[1]} {match[2]} {match[3]}", "%Y %b %d").date()
        if monday.weekday() != 0:
            raise UnderlyingDataError("EIA公开表星期边界无效")
        for index, cell in enumerate(cells):
            day = monday + timedelta(days=index)
            value = html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
            if not lower <= day <= upper or value in {"", "-", "--", "NA", "W"}:
                continue
            try:
                decimal_value = Decimal(value)
                if not decimal_value.is_finite():
                    raise InvalidOperation
            except InvalidOperation:
                raise UnderlyingDataError("EIA公开表数值无效") from None
            if day.isoformat() in output:
                raise UnderlyingDataError("EIA公开表日期重复")
            output[day.isoformat()] = {"period": day.isoformat(), "value": decimal_value}
    if not output:
        raise UnderlyingDataError("EIA所选日期没有已发布参考值")
    return [output[day] for day in sorted(output)]


class EIAHistoryPublic:
    """Official public history tables, with the API adapter's scalar return contract."""

    def daily_observations(self, route, series, start_date, end_date):
        """Return actual daily values for a fixed series; route cannot choose a URL."""
        if series not in EIA_PAGES:
            raise ValueError("未登记的EIA公开序列")
        raw, received = public_response(EIA_PAGES[series][0])
        rows = parse_eia_history(raw, series, start_date, end_date)
        self.evidence = save_public_evidence(raw, "eia", received)
        return rows
