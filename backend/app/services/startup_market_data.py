"""Idempotent startup reconciliation for missing verified Binance archives."""

from datetime import datetime, timedelta, timezone
import os

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.domains.models import Asset, Task
from app.services.long_history import archive_manifest
from app.domains.models import MarketData, MarketObservation
from app.services.underlying_market import _source_for_asset, HISTORY_DAYS

COMPLETE_ARCHIVE_STATUSES = {"THREE_YEARS", "LISTING_SHORTER_THAN_THREE_YEARS"}
ACTIVE_TASK_STATUSES = {"QUEUED", "RUNNING", "RETRYING", "QUEUE_ERROR"}
STARTUP_RETRY_COOLDOWN = timedelta(minutes=15)


def _as_utc(value: datetime) -> datetime:
    """Normalize a database timestamp to aware UTC for cooldown comparison.

    Args:
        value: Task timestamp returned by PostgreSQL or a test SQLite database.

    Returns:
        The same instant represented as a timezone-aware UTC datetime.
    """
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def queue_missing_binance_archives(db: Session, *, enqueue_task=None, manifest_reader=None, now=None) -> dict:
    """Queue resumable 15-minute archive work for verified contracts with gaps.

    The function only creates a durable RQ task. It never performs network I/O in
    API startup, and it skips complete archives, active work, and a recently
    failed automatic attempt. Only exact verified Binance USDM assets qualify.

    Args:
        db: Application SQLAlchemy session; no private account information is read.
        enqueue_task: Optional task enqueue function for deterministic tests.
        manifest_reader: Optional local manifest reader for deterministic tests.
        now: Optional current UTC time used to evaluate the retry cooldown.

    Returns:
        Counts and an optional durable task ID describing the reconciliation result.
    """
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": "startup-binance-archive-backfill-v1"})
    if enqueue_task is None:
        from app.services.jobs import enqueue as enqueue_task
    manifest_reader = manifest_reader or archive_manifest
    now_utc = _as_utc(now or datetime.now(timezone.utc))
    assets = list(db.scalars(select(Asset).where(
        Asset.venue == "BINANCE_USDM",
        Asset.verification.in_(["VERIFIED", "VERIFIED_SNAPSHOT"]),
    ).order_by(Asset.priority, Asset.symbol)))
    incomplete = {
        asset.id for asset in assets
        if (manifest := manifest_reader(asset.id, "15m")) is None
        or manifest.get("status") not in COMPLETE_ARCHIVE_STATUSES
    }
    if not incomplete:
        return {"status": "COMPLETE", "asset_count": len(assets), "queued_asset_count": 0, "task_id": None}

    active = list(db.scalars(select(Task).where(Task.status.in_(ACTIVE_TASK_STATUSES))))
    covered: set[str] = set()
    for task in active:
        if task.kind == "FETCH_BINANCE_ARCHIVES" and task.parameters.get("interval") == "15m":
            covered.update(asset_id for asset_id in task.parameters.get("asset_ids", []) if asset_id in incomplete)
        elif task.kind == "FETCH_ARCHIVE" and task.parameters.get("interval") == "15m":
            asset_id = task.parameters.get("asset_id")
            if asset_id in incomplete:
                covered.add(asset_id)

    cutoff = now_utc - STARTUP_RETRY_COOLDOWN
    for task in db.scalars(select(Task).where(
        Task.kind == "FETCH_BINANCE_ARCHIVES",
        Task.status.in_(["FAILED", "QUEUE_ERROR"]),
    )):
        error = str(task.result.get("error", ""))
        connection_failure = "Binance 公开连接失败" in error or "Binance 公开接口 HTTP" in error
        if (
            not task.parameters.get("startup_reconcile")
            or not connection_failure
            or _as_utc(task.updated_at) < cutoff
        ):
            continue
        covered.update(asset_id for asset_id in task.parameters.get("asset_ids", []) if asset_id in incomplete)

    to_queue = [asset.id for asset in assets if asset.id in incomplete and asset.id not in covered]
    if not to_queue:
        return {"status": "ALREADY_QUEUED_OR_RETRY_COOLDOWN", "asset_count": len(assets), "queued_asset_count": 0, "task_id": None}
    task = enqueue_task(db, "FETCH_BINANCE_ARCHIVES", {
        "asset_ids": to_queue,
        "interval": "15m",
        "startup_reconcile": True,
    })
    return {"status": task.status, "asset_count": len(assets), "queued_asset_count": len(to_queue), "task_id": task.id}


def queue_missing_underlying_history(db: Session, *, enqueue_task=None, now=None) -> dict:
    """Queue configured source-native history gaps without exposing credentials.

    Args:
        db: Application SQLAlchemy session after migrations and asset registration.
        enqueue_task: Optional queue publisher for deterministic startup tests.
        now: Optional timezone-aware clock used for failure-cooldown decisions.

    Returns:
        Provider-independent queued counts; no task is created for an unconfigured key.

    Notes:
        A configured key is evidence that the local operator enabled that provider,
        but successful upstream access is only recorded after a real job succeeds.
    """
    if enqueue_task is None:
        from app.services.jobs import enqueue as enqueue_task
    now_utc = _as_utc(now or datetime.now(timezone.utc))
    assets = list(db.scalars(select(Asset).where(Asset.venue != "BINANCE_USDM").order_by(Asset.priority, Asset.symbol)))
    active_or_recent = list(db.scalars(select(Task).where(
        Task.kind == "FETCH_UNDERLYING_HISTORY",
        Task.status.in_(["QUEUED", "RUNNING", "RETRYING", "FAILED", "QUEUE_ERROR"]),
    )))
    pending = {(task.parameters.get("asset_id"), task.parameters.get("interval")) for task in active_or_recent
               if task.status in {"QUEUED", "RUNNING", "RETRYING"}}
    cooldown = now_utc - timedelta(hours=6)
    recent_failures = {(task.parameters.get("asset_id"), task.parameters.get("interval")) for task in active_or_recent
                       if task.status in {"FAILED", "QUEUE_ERROR"} and _as_utc(task.updated_at) >= cooldown}
    candidates = []
    for asset in assets:
        try:
            source = _source_for_asset(asset)
        except ValueError:
            continue
        credential_names = {"massive_stocks":"MASSIVE_API_KEY", "twelve_data":"TWELVE_DATA_API_KEY", "eia":"EIA_API_KEY"}
        if not os.environ.get(credential_names[source["provider_id"]], "").strip():
            continue
        if source["kind"] == "observations":
            configured_intervals = ["1d"]
            metric_exists = db.scalar(select(MarketObservation.id).where(
                MarketObservation.asset_id == asset.id,
                MarketObservation.metric == source["metric"],
            )) is not None
            missing_intervals = [] if metric_exists else configured_intervals
        else:
            configured_intervals = source.get("intervals", [])
            existing = set(db.scalars(select(MarketData.interval).where(MarketData.asset_id == asset.id)))
            missing_intervals = [interval for interval in configured_intervals if interval not in existing]
        for interval in missing_intervals:
            key = (asset.id, interval)
            if key not in pending and key not in recent_failures:
                candidates.append(key)
    created = []
    for asset_id, interval in candidates:
        task = enqueue_task(db, "FETCH_UNDERLYING_HISTORY", {
            "asset_id": asset_id, "interval": interval, "days": HISTORY_DAYS,
            "startup_reconcile": True,
        })
        created.append(task)
    return {
        "status": "QUEUED" if created else "NO_CONFIGURED_GAPS",
        "queued_count": len(created),
        "task_ids": [task.id for task in created],
        "provider_key_values_exposed": False,
    }
