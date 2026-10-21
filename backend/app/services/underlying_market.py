"""Ingest source-native public underlying data without mixing related products."""

import os
import time
from datetime import date, datetime, time as day_time, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

from app.adapters.underlying_public import (
    EIAOpenDataPublic, MassiveStocksPublic, SinaChinaIndicesPublic,
    TencentChinaIndicesPublic, TwelveDataPublic, UnderlyingDataError,
)
from app.adapters.public_binance import INTERVALS
from app.domains.models import Asset, MarketData, MarketObservation, Provider, utcnow
from app.services.market_data import FIELDS, read_dataset
from app.storage.market_observations import read_observations, store_observations
from app.storage.market_data_segments import save_underlying_segments
from app.storage.redis_cache import cache_underlying_refresh, read_cached_underlying_refresh

HISTORY_DAYS = 1095
MAX_HISTORY_DAYS = 3650
REFRESH_TTLS = {
    "massive_stocks": 30, "twelve_data": 21600,
    "sina_cn_public": 60, "tencent_cn_public": 60,
}
BAR_DURATIONS_MS = {"15m": 900_000, "1h": 3_600_000, "1d": 86_400_000}
PROVIDER_CREDENTIAL_ENVS = {
    "massive_stocks": "MASSIVE_API_KEY",
    "twelve_data": "TWELVE_DATA_API_KEY",
    "eia": "EIA_API_KEY",
    "sina_cn_public": None,
    "tencent_cn_public": None,
}
ALLOWED_MARKET_PROVIDERS = frozenset(PROVIDER_CREDENTIAL_ENVS)


def _source_for_asset(asset):
    """Return a validated provider binding from the server-owned asset registry.

    Args:
        asset: Registered database asset; callers never supply a provider URL.

    Returns:
        A provider-binding dictionary for a research-only underlying asset.

    Raises:
        ValueError: No supported source binding exists for the exact product.
    """
    if asset is None or asset.venue == "BINANCE_USDM":
        raise ValueError("只允许选择已登记的原始底层资产；永续合约仍走Binance数据边界")
    source = (asset.specification or {}).get("market_data_source")
    if not isinstance(source, dict) or source.get("kind") not in {"candles", "observations"}:
        raise ValueError("该原始资产尚未绑定公开行情供应商")
    if source.get("provider_id") not in ALLOWED_MARKET_PROVIDERS:
        raise ValueError("资产行情供应商未在服务端白名单中")
    fallbacks = source.get("fallback_provider_ids", [])
    if not isinstance(fallbacks, list) or any(item not in ALLOWED_MARKET_PROVIDERS for item in fallbacks):
        raise ValueError("资产备用行情供应商未在服务端白名单中")
    if not source.get("alias"):
        raise ValueError("供应商产品代码缺失")
    return source


def validate_underlying_request(asset, interval, *, require_credential=True):
    """Validate one UI/job request against a registered source and local key presence.

    Args:
        asset: Exact asset ORM row selected by its authenticated caller.
        interval: Requested native candle period, or `1d` for scalar series.
        require_credential: When true, require the provider key without exposing it.

    Returns:
        Safe provider ID, data kind and source freshness contract.

    Raises:
        ValueError: Product, interval or credentials are not configured for research.
    """
    source = _source_for_asset(asset)
    if source["kind"] == "observations":
        if interval != "1d":
            raise ValueError("能源现货参考源只发布日度数值，不提供盘中OHLC")
    elif interval not in source.get("intervals", []):
        raise ValueError("该原始行情源未提供所选原生周期")
    credential_env = PROVIDER_CREDENTIAL_ENVS[source["provider_id"]]
    if require_credential and credential_env and not os.environ.get(credential_env, "").strip():
        raise ValueError(f"缺少本机数据源配置 {credential_env}；密钥不要发到聊天或浏览器")
    return {
        "provider_id": source["provider_id"], "kind": source["kind"],
        "credential_required": credential_env is not None, "source": source,
    }


def _set_provider_state(db, provider_id, status, *, asset_id=None, interval=None, error_code=None, latest_at_ms=None):
    """Record safe provider health metadata without storing keys or raw URLs.

    Args:
        db: Current SQLAlchemy session.
        provider_id: Whitelisted provider row ID.
        status: `CONNECTED`, `UNAVAILABLE`, or credential configuration state.
        asset_id: Optional exact research asset ID, not a secret.
        interval: Optional native source interval.
        error_code: Optional sanitized category string, never exception text.
        latest_at_ms: Optional newest provider observation timestamp in UTC ms.

    Returns:
        None; the caller controls the surrounding database commit.
    """
    provider = db.get(Provider, provider_id)
    if provider is None:
        return
    detail = dict(provider.detail or {})
    detail.update({"last_attempt_at": utcnow().isoformat()})
    if status == "CONNECTED":
        detail.update({"last_success_at": utcnow().isoformat(), "last_asset_id": asset_id})
        detail.pop("last_error_code", None)
    if interval:
        detail["last_interval"] = interval
    if latest_at_ms is not None:
        detail["latest_data_at_ms"] = int(latest_at_ms)
    if error_code:
        detail["last_error_code"] = error_code
    provider.status = status
    provider.detail = detail


def _iso_date_range(now_ms, days, timezone_name):
    """Convert an observation instant and lookback days to exchange-local dates.

    Args:
        now_ms: Current UTC timestamp in milliseconds.
        days: Requested history lookback in calendar days.
        timezone_name: IANA timezone declared for the registered source.

    Returns:
        Inclusive `(start_date, end_date)` ISO strings in the exchange calendar.
    """
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, TypeError):
        raise ValueError("來源交易所時區未配置") from None
    local_today = datetime.fromtimestamp(now_ms / 1000, timezone.utc).astimezone(zone).date()
    return (local_today.fromordinal(local_today.toordinal() - days).isoformat(), local_today.isoformat())


def _daily_date_open_ms(value):
    """Represent a provider's date-only daily bar at its UTC calendar label.

    Args:
        value: ISO calendar date or provider timestamp whose exchange date is known.

    Returns:
        UTC-millisecond midnight for that provider session date.
    """
    if isinstance(value, date):
        session_date = value
    else:
        text_value = str(value)
        try:
            session_date = date.fromisoformat(text_value[:10])
        except ValueError:
            raise ValueError("供应商日线交易日格式无效") from None
    midnight = datetime.combine(session_date, day_time.min, tzinfo=timezone.utc)
    return int(midnight.timestamp() * 1000)


def _decimal_bar(item, open_ms, interval):
    """Validate a source-native OHLCV object before converting it for storage.

    Args:
        item: Provider object with exact `o/h/l/c/v` or `open/high/low/close/volume` fields.
        open_ms: Candle start or date-label UTC time in milliseconds.
        interval: Whitelisted native candle interval.

    Returns:
        A typed candle dictionary; price values stay `Decimal` until JSON display.

    Raises:
        ValueError: Any price, volume, interval or OHLC invariant is invalid.
    """
    if interval not in BAR_DURATIONS_MS:
        raise ValueError("底层行情周期未支持")
    aliases = {"open": ("open", "o"), "high": ("high", "h"), "low": ("low", "l"), "close": ("close", "c"), "volume": ("volume", "v")}
    values = {}
    try:
        for field, choices in aliases.items():
            raw = next(item[key] for key in choices if key in item)
            values[field] = Decimal(str(raw))
    except (KeyError, StopIteration, TypeError, InvalidOperation):
        raise ValueError("供应商OHLCV字段缺失或格式无效") from None
    if not all(value.is_finite() for value in values.values()):
        raise ValueError("供应商OHLCV包含非有限数值")
    if min(values[key] for key in ("open", "high", "low", "close")) <= 0 or values["volume"] < 0:
        raise ValueError("供应商OHLCV数值范围无效")
    if values["high"] < max(values["open"], values["close"], values["low"]) or values["low"] > min(values["open"], values["close"], values["high"]):
        raise ValueError("供应商OHLC范围无效")
    return {"open_time": int(open_ms), **values, "close_time": int(open_ms) + BAR_DURATIONS_MS[interval] - 1}


def _parse_massive_bars(values, interval, now_ms):
    """Convert Massive aggregates while preserving session bars and open-bar status.

    Args:
        values: JSON objects returned for one ticker and exact interval.
        interval: Requested native `15m`, `1h`, or `1d` interval.
        now_ms: Frozen current UTC timestamp used to exclude forming bars from storage.

    Returns:
        `(closed, live)` where closed rows are ordered and live is display-only.
    """
    closed, live = [], None
    for item in values:
        try:
            provider_ms = int(item["t"])
            if interval == "1d":
                session_day = datetime.fromtimestamp(provider_ms / 1000, timezone.utc).date()
                open_ms = _daily_date_open_ms(session_day)
            else:
                open_ms = provider_ms
            row = _decimal_bar(item, open_ms, interval)
        except (KeyError, TypeError, ValueError, OverflowError):
            raise ValueError("Massive聚合K线字段或时间无效") from None
        if row["close_time"] < now_ms:
            closed.append(row)
        elif row["open_time"] <= now_ms:
            live = {**row, "is_closed": False}
    closed.sort(key=lambda row: row["open_time"])
    return closed, live


def _parse_twelve_data_daily(body, now_ms):
    """Convert XKRX end-of-day candles from their provider date labels.

    Args:
        body: Twelve Data response containing provider timezone metadata and values.
        now_ms: Frozen UTC milliseconds; future/current date candles remain display-only.

    Returns:
        `(closed_rows, live_row)` with UTC date labels and exact Decimal OHLCV.
    """
    values = body.get("values", [])
    closed, live = [], None
    for item in values:
        try:
            open_ms = _daily_date_open_ms(item["datetime"])
            row = _decimal_bar(item, open_ms, "1d")
        except (KeyError, TypeError, ValueError):
            raise ValueError("Twelve Data日线结构无效") from None
        if row["close_time"] < now_ms:
            closed.append(row)
        else:
            live = {**row, "is_closed": False}
    closed.sort(key=lambda row: row["open_time"])
    return closed, live


def _parse_public_index_daily(values, now_ms, start_date, end_date):
    """Validate public Sina/Tencent daily index bars against the requested window.

    Args:
        values: Provider-native dictionaries with a date label and OHLCV values.
        now_ms: UTC milliseconds used to keep an unfinished UTC-day candle display-only.
        start_date: Inclusive exchange-local ISO date requested by the job.
        end_date: Inclusive exchange-local ISO date requested by the job.

    Returns:
        `(closed_rows, live_row)` containing only real provider observations in range.

    Raises:
        ValueError: A returned bar has malformed date or invalid OHLCV values.
    """
    lower, upper = date.fromisoformat(start_date), date.fromisoformat(end_date)
    closed, live = [], None
    for item in values:
        try:
            label = item.get("day", item.get("datetime", item.get("date")))
            session_day = date.fromisoformat(str(label)[:10])
            if not lower <= session_day <= upper:
                continue
            row = _decimal_bar(item, _daily_date_open_ms(session_day), "1d")
        except (AttributeError, TypeError, ValueError):
            raise ValueError("中国指数公开源日线结构或数值无效") from None
        if row["close_time"] < now_ms:
            closed.append(row)
        else:
            live = {**row, "is_closed": False}
    closed.sort(key=lambda row: row["open_time"])
    return closed, live


def _fetch_candle_source(source, interval, start_date, end_date, now_ms, adapter=None):
    """Try only the registered primary and fallback GET sources for one asset.

    Args:
        source: Validated server-owned market source binding.
        interval: Native interval accepted by the binding.
        start_date: Inclusive ISO date in the exchange timezone.
        end_date: Inclusive ISO date in the exchange timezone.
        now_ms: UTC cutoff used to classify closed and forming candles.
        adapter: Optional primary-provider fake/client for tests.

    Returns:
        Closed rows, an optional live row, and the provider that supplied them.

    Raises:
        UnderlyingDataError: Every registered source fails or returns invalid bars.
    """
    provider_ids = [source["provider_id"], *source.get("fallback_provider_ids", [])]
    failures = []
    for provider_id in provider_ids:
        try:
            client = adapter if provider_id == source["provider_id"] else None
            if provider_id == "massive_stocks":
                client = client or MassiveStocksPublic()
                values = client.aggregates(source["alias"], interval, start_date, end_date)
                closed, live = _parse_massive_bars(values, interval, now_ms)
            elif provider_id == "twelve_data":
                if interval != "1d":
                    raise ValueError("XKRX当前仅有可信日终周期")
                client = client or TwelveDataPublic()
                body = client.daily_bars(source["alias"], source["mic_code"], start_date, end_date)
                closed, live = _parse_twelve_data_daily(body, now_ms)
            elif provider_id == "sina_cn_public":
                if interval != "1d":
                    raise ValueError("新浪指数公开源当前仅登记日线")
                client = client or SinaChinaIndicesPublic()
                values = client.daily_bars(source["alias"], start_date, end_date)
                closed, live = _parse_public_index_daily(values, now_ms, start_date, end_date)
            elif provider_id == "tencent_cn_public":
                if interval != "1d":
                    raise ValueError("腾讯指数备用源当前仅登记日线")
                client = client or TencentChinaIndicesPublic()
                values = client.daily_bars(source["alias"], start_date, end_date)
                closed, live = _parse_public_index_daily(values, now_ms, start_date, end_date)
            else:
                raise ValueError("底层行情供应商未支持K线读取")
            if closed or live:
                return closed, live, provider_id
        except UnderlyingDataError as exc:
            failures.append(f"{provider_id}:{exc}")
        except ValueError:
            failures.append(f"{provider_id}:公开源返回的K线不符合数据校验")
            continue
    raise UnderlyingDataError("公开行情主备源均不可用；" + "；".join(failures))


def _merge_save_bars(db, asset, interval, rows, source, now_ms, lookback_days):
    """Merge a source window with local history and publish its immutable dataset.

    Args:
        db: Current worker/API SQLAlchemy session.
        asset: Exact registered underlying asset.
        interval: Native provider candle interval.
        rows: New closed provider candles.
        source: Sanitized provider provenance metadata.
        now_ms: Current UTC milliseconds used for the closed-bar cutoff.
        lookback_days: Requested fetch window; stored history is never shortened
            below its already retained span or the three-year platform target.

    Returns:
        Market-data metadata summary, or None when no new candle was closed.
    """
    if not rows:
        return None
    existing = db.scalar(select(MarketData).where(MarketData.asset_id == asset.id, MarketData.interval == interval))
    previous = read_dataset(existing) if existing is not None else []
    earliest = min([int(row["open_time"]) for row in previous] + [int(row["open_time"]) for row in rows])
    retained_days = max(lookback_days + 2, HISTORY_DAYS + 2, int((now_ms - earliest) / 86_400_000) + 2)
    if retained_days > MAX_HISTORY_DAYS:
        retained_days = MAX_HISTORY_DAYS
    return save_underlying_segments(db, asset, interval, rows, source, now_ms, max_age_days=retained_days)


def _parse_eia_observations(rows, start_date, end_date):
    """Convert EIA scalar values while preserving dates with no published value.

    Args:
        rows: EIA APIv2 rows containing daily `period` and scalar `value` fields.
        start_date: Requested inclusive lower date, used in provenance only.
        end_date: Requested inclusive upper date, used in provenance only.

    Returns:
        Date-ordered scalar records; missing or nonnumeric provider values are omitted.
    """
    output = {}
    for row in rows:
        raw = row.get("value")
        if raw in (None, "", "--", "null"):
            continue
        try:
            day = date.fromisoformat(str(row["period"]))
            value = Decimal(str(raw))
        except (KeyError, TypeError, ValueError, InvalidOperation):
            raise ValueError("EIA日度参考值格式无效") from None
        if not value.is_finite():
            continue
        timestamp = _daily_date_open_ms(day)
        output[timestamp] = {"observed_at_ms": timestamp, "value": value}
    if start_date and end_date:
        lower, upper = date.fromisoformat(start_date), date.fromisoformat(end_date)
        output = {timestamp: row for timestamp, row in output.items() if lower <= datetime.fromtimestamp(timestamp / 1000, timezone.utc).date() <= upper}
    return [output[timestamp] for timestamp in sorted(output)]


def fetch_underlying_history(db, asset_id, interval="1d", days=HISTORY_DAYS, now_ms=None, adapter=None):
    """Fetch and persist three-year source-native history for one exact underlying.

    Args:
        db: Durable RQ worker SQLAlchemy session.
        asset_id: Exact registered original stock, ETF, or energy reference ID.
        interval: Native provider interval; EIA observations use the daily `1d` marker.
        days: Calendar-day lookback, from 1 to 3650; startup defaults to three years.
        now_ms: Optional UTC timestamp for deterministic provider tests.
        adapter: Optional provider-specific fake or configured GET adapter.

    Returns:
        Source, dataset count and SHA256 summary; the related perpetual is never written.

    Raises:
        ValueError: Asset or interval is not whitelisted, or provider rows are invalid.
        UnderlyingDataError: A key is missing or the configured public source fails.
    """
    current_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    if not isinstance(days, int) or not 1 <= days <= MAX_HISTORY_DAYS:
        raise ValueError("底层历史范围必须在1到3650天之间")
    asset = db.get(Asset, asset_id)
    source = _source_for_asset(asset)
    provider_id = source["provider_id"]
    start_date, end_date = _iso_date_range(current_ms, days, source.get("timezone", "UTC"))
    try:
        if source["kind"] == "observations":
            if interval != "1d":
                raise ValueError("EIA能源现货是日度参考值，不存在15分钟OHLC")
            client = adapter or EIAOpenDataPublic()
            response = client.daily_observations(source["route"], source["alias"], start_date, end_date)
            observations = _parse_eia_observations(response, start_date, end_date)
            if not observations:
                raise UnderlyingDataError("EIA所选日期范围没有已发布参考值")
            result = store_observations(
                db, asset.id, source["metric"], observations, source["unit"],
                {"provider": provider_id, "series": source["alias"], "route": source["route"],
                 "mode": "daily_spot_observations", "lookback_days": days,
                 "retrieved_at_ms": current_ms, "value_semantics": "scalar_observation_not_ohlc"},
            )
            _set_provider_state(db, provider_id, "CONNECTED", asset_id=asset.id, interval="1d", latest_at_ms=result["last_observed_at_ms"])
            db.commit()
            return {**result, "source": provider_id, "kind": "observations", "research_only": True}

        supported = source.get("intervals", [])
        if interval not in supported or interval not in BAR_DURATIONS_MS:
            raise ValueError("所选周期不是该原始行情源支持的原生周期")
        closed, _forming, provider_id = _fetch_candle_source(
            source, interval, start_date, end_date, current_ms, adapter,
        )
        if not closed:
            raise UnderlyingDataError("供应商没有返回已收盘的真实K线")
        provenance = {
            "provider": provider_id, "provider_symbol": source["alias"],
            "provider_chain": [source["provider_id"], *source.get("fallback_provider_ids", [])],
            "exchange": source.get("mic_code", source.get("exchange", "US")), "market_timezone": source.get("timezone"),
            "mode": "source_native_underlying_history", "lookback_days": days,
            "retrieved_at_ms": current_ms, "timestamp_alignment": "exchange_session",
            "instrument_identity": asset.id, "not_a_related_perpetual": True,
        }
        saved = _merge_save_bars(db, asset, interval, closed, provenance, current_ms, days)
        _set_provider_state(db, provider_id, "CONNECTED", asset_id=asset.id, interval=interval, latest_at_ms=saved["last_close_ms"])
        db.commit()
        return {**saved, "source": provider_id, "provider_symbol": source["alias"], "kind": "candles", "first_open_ms": min(row["open_time"] for row in closed), "rows_fetched": len(closed), "research_only": True}
    except UnderlyingDataError:
        attempted = False
        for failed_provider_id in [source["provider_id"], *source.get("fallback_provider_ids", [])]:
            provider = db.get(Provider, failed_provider_id)
            credential_env = PROVIDER_CREDENTIAL_ENVS[failed_provider_id]
            if provider and (credential_env is None or os.environ.get(credential_env, "").strip()):
                _set_provider_state(
                    db, failed_provider_id, "UNAVAILABLE", asset_id=asset.id,
                    interval=interval, error_code="UPSTREAM_OR_DATA_VALIDATION_ERROR",
                )
                attempted = True
        if attempted:
            db.commit()
        raise


def refresh_underlying_latest(db, asset_id, interval, now_ms=None):
    """Rate-limit a chart refresh against a source and retain only true provider bars.

    Args:
        db: Current authenticated chart SQLAlchemy session.
        asset_id: Exact registered underlying asset, never a related derivative.
        interval: Registered provider-native candle interval.
        now_ms: Optional UTC timestamp used to determine forming candles in tests.

    Returns:
        Cached or freshly fetched closed/live source-native bars with provenance status.

    Raises:
        ValueError: The asset is a scalar series or does not support the interval.
        UnderlyingDataError: A required key or public source is unavailable.
    """
    current_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    asset = db.get(Asset, asset_id)
    source = _source_for_asset(asset)
    if source["kind"] != "candles":
        raise ValueError("每日能源现貨参考值是标量序列，请读取参考序列页面")
    ttl = REFRESH_TTLS.get(source["provider_id"])
    if interval not in source.get("intervals", []) or ttl is None:
        raise ValueError("所选周期没有配置实时/日线刷新")
    cached = read_cached_underlying_refresh(asset_id, interval)
    if cached and current_ms - int(cached.get("updated_at_ms", 0)) < ttl * 1000:
        return cached
    start_date, end_date = _iso_date_range(current_ms, 3, source.get("timezone", "UTC"))
    closed, live, provider_id = _fetch_candle_source(
        source, interval, start_date, end_date, current_ms,
    )
    provenance = {
        "provider": provider_id, "provider_symbol": source["alias"],
        "provider_chain": [source["provider_id"], *source.get("fallback_provider_ids", [])],
        "exchange": source.get("mic_code", source.get("exchange", "US")), "market_timezone": source.get("timezone"),
        "mode": "source_native_live_poll", "retrieved_at_ms": current_ms,
        "timestamp_alignment": "exchange_session", "instrument_identity": asset.id,
        "not_a_related_perpetual": True,
    }
    saved = _merge_save_bars(db, asset, interval, closed, provenance, current_ms, 3) if closed else None
    latest_closed = closed[-1] if closed else None
    result = {
        "asset_id": asset_id, "symbol": asset.symbol, "interval": interval,
        "closed_candles": [{**row, **{field: str(row[field]) for field in FIELDS}} for row in closed[-100:]],
        "live_candle": ({**live, **{field: str(live[field]) for field in FIELDS}} if live else None),
        "last_closed_at_ms": saved["last_close_ms"] if saved else (latest_closed["close_time"] if latest_closed else None),
        "updated_at_ms": current_ms, "source": provider_id,
        "source_freshness": source.get("freshness", (
            "供应商套餐可能为实时或延迟" if provider_id == "massive_stocks"
            else "交易所日终行情" if provider_id == "twelve_data"
            else "公开页面行情；可能延迟，以源时间戳为准"
        )),
        "source_event_at_ms": (live["open_time"] if live else latest_closed["close_time"] if latest_closed else None),
        "dataset_sha256": saved["sha256"] if saved else None,
        "research_only": True, "stale": False,
    }
    cache_underlying_refresh(asset_id, interval, result, ttl)
    _set_provider_state(db, provider_id, "CONNECTED", asset_id=asset_id, interval=interval,
                        latest_at_ms=result["last_closed_at_ms"])
    db.commit()
    return result


def underlying_series(db, asset_id, metric=None, limit=5000, end_ms=None):
    """Read one authenticated scalar reference series and verify its data digest.

    Args:
        db: Current authenticated read-only research SQLAlchemy session.
        asset_id: Exact registered EIA reference asset ID.
        metric: Optional exact metric name; omitted selects that asset's registered metric.
        limit: Maximum number of ascending observations returned.
        end_ms: Optional inclusive UTC-millisecond as-of cutoff.

    Returns:
        Decimal-safe scalar points, true unit, source identity and Parquet SHA256.

    Raises:
        ValueError: Asset mapping, metric, limit or file provenance is invalid.
    """
    asset = db.get(Asset, asset_id)
    source = _source_for_asset(asset)
    if source["kind"] != "observations":
        raise ValueError("该资产使用K线数据，不是参考值序列")
    selected_metric = metric or source["metric"]
    if selected_metric != source["metric"] or not isinstance(limit, int) or not 1 <= limit <= 10000:
        raise ValueError("参考序列指标或返回条数无效")
    metadata = db.scalar(select(MarketObservation).where(
        MarketObservation.asset_id == asset_id,
        MarketObservation.metric == selected_metric,
    ))
    if metadata is None:
        return {"asset_id": asset_id, "metric": selected_metric, "available": False, "unit": source["unit"], "observations": [], "research_only": True}
    rows = read_observations(metadata)
    if end_ms is not None:
        if not isinstance(end_ms, int) or end_ms < 0:
            raise ValueError("参考序列截止时间必须为非负UTC毫秒")
        rows = [row for row in rows if row["observed_at_ms"] <= end_ms]
    selected = rows[-limit:]
    return {
        "asset_id": asset_id, "symbol": asset.symbol, "metric": selected_metric,
        "unit": metadata.unit, "available": bool(selected), "observations": [
            {"observed_at_ms": row["observed_at_ms"], "value": format(row["value"].normalize(), "f"), "unit": row["unit"]}
            for row in selected
        ], "available_count": len(rows), "first_observed_at_ms": metadata.first_observed_at_ms,
        "last_observed_at_ms": metadata.last_observed_at_ms, "source": metadata.source,
        "sha256": metadata.sha256, "research_only": True,
    }
