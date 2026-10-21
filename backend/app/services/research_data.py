"""Small source-isolated research ingestion loop; no orders or legacy-pointer writes."""

import json
import time
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select, text

from app.adapters.research_public import fetch_research_page, provider_capabilities, ResearchPublicError
from app.domains.datasets import DatasetVersion
from app.domains.models import Asset, Alias, Provider, MarketData, utcnow
from app.services.market_data import read_dataset
from app.storage.research_datasets import publish_dataset, read_version, raw_pages_for_version
from app.storage.provider_rate_limit import provider_request_slot, defer_provider_after_limit

BINDINGS = {
    ("us:NVDA", "yahoo_chart"): {"symbol": "NVDA", "interval": "1d", "name": "英伟达股票", "product_type": "股票"},
    ("coinbase_spot:BTC-USD", "coinbase_exchange"): {"symbol": "BTC-USD", "interval": "1d", "name": "比特币 / 美元现货", "product_type": "spot"},
    ("binance_usdm:BTCUSDT", "legacy_binance_snapshot"): {"symbol": "BTCUSDT", "interval": "15m", "name": "比特币 / USDT 永续快照", "product_type": "perpetual"},
}


def seed_research_catalog(db):
    """Add only missing public provider/product identities; never replace human configuration."""
    for provider_id, name in (("yahoo_chart", "Yahoo个人研究日线"), ("coinbase_exchange", "Coinbase Exchange现货日线"),
                              ("legacy_binance_snapshot", "已有Binance永续快照只读桥接")):
        if db.get(Provider, provider_id) is None:
            db.add(Provider(id=provider_id, name=name, status="INTEGRATION_UNVERIFIED", detail={"research_only": True}))
    asset_id = "coinbase_spot:BTC-USD"
    if db.get(Asset, asset_id) is None:
        db.add(Asset(id=asset_id, symbol="BTC-USD", name="Coinbase比特币美元现货", underlying_id="BTC",
                     asset_type="crypto_spot", venue="COINBASE_EXCHANGE", priority=101,
                     verification="RESEARCH_ONLY", specification={"currency": "USD", "base_currency": "BTC",
                         "quote_currency": "USD", "trading_calendar": "CRYPTO_24_7_V1", "execution_eligible": False}))
    db.flush()
    for (aid, provider_id), binding in BINDINGS.items():
        if db.get(Asset, aid) is not None and db.scalar(select(Alias).where(Alias.provider_id == provider_id, Alias.alias == binding["symbol"])) is None:
            db.add(Alias(asset_id=aid, provider_id=provider_id, alias=binding["symbol"]))


def validate_request(asset_id, provider_id, start_date, end_date, now_ms=None):
    """Validate fixed product/source and inclusive ISO dates up to 3650 days; no URL input."""
    binding = BINDINGS.get((asset_id, provider_id))
    if binding is None:
        raise ValueError("产品与来源未列入本阶段白名单")
    try:
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
        if start.isoformat() != start_date or end.isoformat() != end_date:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError("日期必须使用有效YYYY-MM-DD格式") from None
    today = datetime.fromtimestamp((now_ms if now_ms is not None else time.time_ns() // 1_000_000) / 1000, timezone.utc).date()
    if start.year < 1990 or end < start or (end - start).days > 3650 or end > today:
        raise ValueError("请求日期必须在1990年以后、不晚于今天且范围不超过3650天")
    return binding, start, end


def version_summary(version):
    """Expose safe immutable metadata; filesystem paths and raw bytes stay server-side."""
    return {"id": version.id, "asset_id": version.asset_id, "provider_id": version.provider_id,
            "interval": version.interval, "row_count": version.row_count, "first_open_ms": version.first_open_ms,
            "last_close_ms": version.last_close_ms, "sha256": version.dataset_sha256,
            "manifest_sha256": version.manifest_sha256, "observed_at_ms": int(version.observed_at.timestamp() * 1000),
            "quality": version.quality, "provenance": version.provenance}


def latest_version(db, asset_id, provider_id):
    """Return the most recently observed frozen version for this exact source and product."""
    return db.scalar(select(DatasetVersion).where(DatasetVersion.asset_id == asset_id, DatasetVersion.provider_id == provider_id)
                     .order_by(DatasetVersion.observed_at.desc(), DatasetVersion.id.desc()).limit(1))


def dataset_catalog(db):
    """Describe three implemented collection paths without claiming live or execution access."""
    items = []
    for (asset_id, provider_id), binding in BINDINGS.items():
        if provider_id == "legacy_binance_snapshot":
            contract = {"currency": "USDT", "calendar_id": "CRYPTO_24_7_LEGACY",
                        "freshness": "本地历史快照，不是当前报价", "calendar_limitation": "只读既有15m数据，历史接收时刻未知"}
            limitations = ["不主动请求Binance；无本地数据即失败", "永续合约不是BTC美元现货；不新增模拟成交"]
        else:
            contract = provider_capabilities(provider_id)
            limitations = ["只支持原生日线，非实时feed，尚不具备成交或模型训练准入", contract["license"], contract["calendar_limitation"]]
        latest = latest_version(db, asset_id, provider_id)
        provider = db.get(Provider, provider_id)
        items.append({"asset_id": asset_id, "provider_id": provider_id, **binding,
                      "currency": contract["currency"], "calendar_id": contract["calendar_id"],
                      "freshness": contract["freshness"], "limitations": limitations,
                      "integration_status": provider.status if provider else "INTEGRATION_UNVERIFIED",
                      "latest_version": version_summary(latest) if latest else None})
    return {"items": items, "live_enabled": False}


def _provenance(provider_id):
    """Freeze source semantics and calendar/parser versions; adjustment is never inferred."""
    contract = provider_capabilities(provider_id)
    return {"price_type": contract["price_type"], "adjustment": contract["adjustment"],
            "calendar_version": contract["calendar_version"], "parser_version": "research_public_daily_v1",
            "license": contract["license"], "delay": "EOD research; no realtime entitlement verified",
            "entitlement": "PERSONAL_RESEARCH_ONLY", "source_symbol": contract["symbol"],
            "timezone": contract["timezone"], "currency": contract["currency"],
            "volume_unit": contract["volume_semantics"], "calendar_scope": contract["calendar_limitation"],
            "timestamp_alignment": "session", "session_close_policy": contract["calendar_id"]}


def _merge_page(db, asset_id, provider_id, page, now_ms):
    """Serialize one stream merge and preserve first observation of unchanged historical bars."""
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"research-stream:{asset_id}:{provider_id}"})
    previous = latest_version(db, asset_id, provider_id)
    provenance = _provenance(provider_id)
    if previous is not None and previous.provenance != provenance:
        raise ValueError("来源解析或日历契约已变化，需显式新流迁移，不能静默混合版本")
    rows = {row["open_time"]: row for row in read_version(previous)} if previous else {}
    raw = raw_pages_for_version(previous) if previous else []
    for row in page["rows"]:
        old = rows.get(row["open_time"])
        fields = ("open_time", "close_time", "open", "high", "low", "close", "volume", "session_date")
        if old is None or any(old.get(key) != row.get(key) for key in fields):
            rows[row["open_time"]] = row
    raw.append({"body": page["raw_bytes"], "endpoint_id": provider_id, "params": page["request_parameters"],
                "observed_at_ms": page["observed_at_ms"]})
    # Raw evidence is retained once per exact response/request; newer identical
    # receipts cannot rewrite the first time those bytes were observed.
    unique = {}
    for item in raw:
        key = (item["body"], json.dumps(item["params"], sort_keys=True), item["endpoint_id"])
        unique.setdefault(key, item)
    return publish_dataset(db, asset_id, provider_id, "1d", list(rows.values()), provenance, list(unique.values()), now_ms)


def _snapshot_legacy(db, asset_id, start, end, now_ms):
    """Bridge hash-verified native perpetual bars; never infer old reception or request a broker."""
    metadata = db.scalar(select(MarketData).where(MarketData.asset_id == asset_id, MarketData.interval == "15m"))
    if metadata is None:
        raise ValueError("当前没有该永续合约的本地15m快照")
    rows = [row for row in read_dataset(metadata)
            if start <= datetime.fromtimestamp(row["open_time"] / 1000, timezone.utc).date() <= end]
    if not rows:
        raise ValueError("所选日期没有本地永续K线；不会从现货补造数据")
    # Snapshot bytes describe only verified public candles, never copy arbitrary
    # private metadata from the legacy database into a raw response artifact.
    raw_body = json.dumps(rows, sort_keys=True, default=str, separators=(",", ":")).encode()
    provenance = {"price_type": "perpetual_trade_ohlcv", "adjustment": "not_applicable",
                  "calendar_version": "legacy_utc_continuous_v1", "parser_version": "legacy_snapshot_bridge_v1",
                  "license": "Existing local public research snapshot", "currency": "USDT", "timezone": "UTC",
                  "origin_source": str((metadata.source or {}).get("provider", (metadata.source or {}).get("mode", "legacy_public_snapshot")))[:100],
                  "source_sha256": metadata.sha256, "timestamp_alignment": "legacy_utc_15m",
                  "verification": "Legacy reception unknown; available only since bridge observation"}
    return publish_dataset(db, asset_id, "legacy_binance_snapshot", "15m", rows, provenance,
                           [{"body": raw_body, "endpoint_id": "legacy_parquet_snapshot", "params": {"start": start.isoformat(), "end": end.isoformat()}}], now_ms)


def ingest_dataset(db, task, *, fetcher=None, now_ms=None):
    """Fetch bounded daily pages with durable checkpoints; successful pages survive manual retry.

    Dates are inclusive session labels. Unchanged historical rows retain their
    observation time; revised values publish a new immutable version. HTTP failure
    does not claim completion or delete existing data. Only this worker performs I/O.
    """
    params = task.parameters
    binding, start, end = validate_request(params["asset_id"], params["provider_id"], params["start_date"], params["end_date"], now_ms)
    asset_id, provider_id = params["asset_id"], params["provider_id"]
    if db.get(Asset, asset_id) is None:
        raise ValueError("产品尚未登记")
    current_ms = now_ms if now_ms is not None else time.time_ns() // 1_000_000
    if provider_id == "legacy_binance_snapshot":
        version = _snapshot_legacy(db, asset_id, start, end, current_ms)
        db.commit()
        return {"version": version_summary(version), "research_only": True, "source_mode": "LOCAL_SNAPSHOT_ONLY"}
    progress = dict((task.result or {}).get("progress", {}))
    cursor = date.fromisoformat(progress.get("next_date", start.isoformat()))
    if not start <= cursor <= end + timedelta(days=1):
        raise ValueError("采集检查点超出本次请求日期")
    fetched = int(progress.get("rows_fetched", 0))
    completed = int(progress.get("completed_pages", 0))
    while cursor <= end:
        page_end = min(end, cursor + timedelta(days=249))
        if fetcher is None:
            with provider_request_slot(provider_id, minimum_spacing_seconds=15):
                try:
                    page = fetch_research_page(provider_id, binding["symbol"], cursor.isoformat(), page_end.isoformat())
                except ResearchPublicError as exc:
                    if exc.status_code == 429:
                        defer_provider_after_limit(provider_id, exc.retry_after_seconds or 60)
                    raise
        else:
            page = fetcher(provider_id, binding["symbol"], cursor.isoformat(), page_end.isoformat())
        current_ms = max(current_ms, page["observed_at_ms"])
        version = _merge_page(db, asset_id, provider_id, page, current_ms) if page["rows"] else None
        fetched += len(page["rows"])
        completed += 1
        cursor = page_end + timedelta(days=1)
        progress = {"next_date": cursor.isoformat(), "completed_pages": completed, "rows_fetched": fetched,
                    "phase": "FETCHING", "total_pages": ((end - start).days // 250) + 1}
        task.result = {"progress": progress, "latest_version_id": version.id if version else (task.result or {}).get("latest_version_id"),
                       "last_page_quality": page["quality"], "research_only": True}
        task.updated_at = utcnow()
        db.commit()
    if fetched == 0:
        raise ValueError("所选日期没有已收盘真实K线；请求结束不代表取得数据")
    version = db.get(DatasetVersion, task.result["latest_version_id"])
    provider = db.get(Provider, provider_id)
    provider.status = "CONNECTED_RESEARCH_EOD"
    provider.detail = {**(provider.detail or {}), "last_success_at": utcnow().isoformat(), "execution_eligible": False}
    db.commit()
    return {**task.result, "progress": {**progress, "phase": "COMPLETE"}, "version": version_summary(version)}
