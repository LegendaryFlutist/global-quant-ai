"""Isolated fixture acceptance of the authenticated source/version/task vertical slice."""

import json
from datetime import datetime, timezone, date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.domains.models import Asset, Provider, Task, MarketData
from app.domains.datasets import DatasetVersion
from app.services import research_data as service
from app.services.catalog import seed
from app.storage import research_datasets as storage
from app.adapters.research_public import ResearchPublicError

NOW = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


@pytest.fixture
def isolated_versions(tmp_path, monkeypatch):
    """Keep dataset artifacts in isolated disk; database fixture already enforces quant_test."""
    monkeypatch.setattr(storage, "DATA_ROOT", tmp_path)
    return tmp_path


def page(provider_id, symbol, start, end):
    """Create one synthetic spot bar per calendar day; no network or real-market claim."""
    rows = []
    day, last = date.fromisoformat(start), date.fromisoformat(end)
    while day <= last:
        opening = int(datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000)
        rows.append({"open_time": opening, "close_time": opening + 86400000 - 1,
                     "open": Decimal("100"), "high": Decimal("103"), "low": Decimal("99"),
                     "close": Decimal("102"), "volume": Decimal("1"), "session_date": day.isoformat(),
                     "observed_at_ms": NOW, "available_at_ms": NOW})
        day += timedelta(days=1)
    return {"rows": rows, "raw_bytes": json.dumps(rows, default=str).encode(), "request_parameters": {"start": start, "end": end},
            "observed_at_ms": NOW, "quality": {"fixture": True}}


def task(db, start, end, result=None):
    """Create a durable fixture task with an optional existing page checkpoint."""
    import uuid
    value = Task(id=str(uuid.uuid4()), kind="INGEST_DATASET", parameters={"asset_id": "coinbase_spot:BTC-USD",
                 "provider_id": "coinbase_exchange", "start_date": start, "end_date": end}, result=result or {}, status="RUNNING")
    db.add(value)
    db.commit()
    return value


def test_increment_over_90_days_preserves_old_version_and_month_blobs(isolated_versions):
    """Append 100 days without rewriting old months or moving original known-at timestamps."""
    with SessionLocal() as db:
        first = service.ingest_dataset(db, task(db, "2024-01-01", "2024-04-09"), fetcher=page, now_ms=NOW)
        original = db.get(DatasetVersion, first["version"]["id"])
        original_hash = original.dataset_sha256
        manifest = json.loads((isolated_versions / original.manifest_path).read_bytes())
        first_file = isolated_versions / original.manifest_path
        blob = first_file.parent / manifest["segments"][0]["file"]
        original_inode = blob.stat().st_ino
        second = service.ingest_dataset(db, task(db, "2024-04-10", "2024-07-18"), fetcher=page, now_ms=NOW + 1000)
        latest = db.get(DatasetVersion, second["version"]["id"])
        assert original.row_count == 100 and latest.row_count == 200
        assert original.dataset_sha256 == original_hash and len(storage.read_version(original)) == 100
        newer_manifest = json.loads((isolated_versions / latest.manifest_path).read_bytes())
        newer_blob = (isolated_versions / latest.manifest_path).parent / newer_manifest["segments"][0]["file"]
        assert newer_blob.stat().st_ino == original_inode
        assert storage.read_version(latest, NOW - 1) == []
        assert storage.read_version(latest)[0]["available_at_ms"] == NOW
        repeated = service.ingest_dataset(db, task(db, "2024-04-10", "2024-07-18"), fetcher=page, now_ms=NOW + 2000)
        assert repeated["version"]["id"] == latest.id
        assert db.scalar(select(MarketData).where(MarketData.asset_id == "coinbase_spot:BTC-USD")) is None


def test_failed_second_page_resumes_without_refetching_first(isolated_versions):
    """Persist successful pages before a source failure and continue from the saved date."""
    calls = []

    def broken(provider_id, symbol, start, end):
        """Fail the second public-response fixture without logging request secrets."""
        calls.append(start)
        if len(calls) == 2:
            raise ResearchPublicError("HTTP_REJECTED", status_code=403)
        return page(provider_id, symbol, start, end)

    with SessionLocal() as db:
        pending = task(db, "2024-01-01", "2024-12-01")
        with pytest.raises(ResearchPublicError):
            service.ingest_dataset(db, pending, fetcher=broken, now_ms=NOW)
        db.refresh(pending)
        assert pending.result["progress"]["completed_pages"] == 1
        assert db.get(DatasetVersion, pending.result["latest_version_id"]).row_count == 250
        result = service.ingest_dataset(db, pending, fetcher=page, now_ms=NOW)
        assert result["version"]["row_count"] == 336 and result["progress"]["completed_pages"] == 2


def test_seed_keeps_custom_source_and_product_separation():
    """Restart seeding cannot overwrite a human source binding or merge spot with a perpetual."""
    with SessionLocal() as db:
        asset = db.get(Asset, "us:NVDA")
        custom = {"provider_id": "massive_stocks", "alias": "NVDA", "kind": "candles", "intervals": ["1d"], "custom": True}
        asset.specification = {**asset.specification, "market_data_source": custom}
        db.commit()
        seed(db)
        assert db.get(Asset, "us:NVDA").specification["market_data_source"] == custom
        assert db.get(Asset, "coinbase_spot:BTC-USD").id != db.get(Asset, "binance_usdm:BTCUSDT").id


def test_legacy_bridge_keeps_source_pointer_and_decimal(isolated_versions, monkeypatch):
    """Fixed perpetual snapshots never overwrite legacy pointers or become USD spot prices."""
    from app.services import market_data
    monkeypatch.setattr(market_data, "DATA_ROOT", isolated_versions)
    row = page("coinbase_exchange", "BTC-USD", "2024-12-01", "2024-12-01")["rows"][0]
    row["close_time"] = row["open_time"] + 900000 - 1
    with SessionLocal() as db:
        saved = market_data.save_dataset(db, "binance_usdm:BTCUSDT", "15m", [row], {"provider": "fixture_binance"}, now_ms=NOW)
        pending = task(db, "2024-12-01", "2024-12-01")
        pending.parameters = {**pending.parameters, "asset_id": "binance_usdm:BTCUSDT", "provider_id": "legacy_binance_snapshot"}
        db.commit()
        result = service.ingest_dataset(db, pending, now_ms=NOW)
        version = db.get(DatasetVersion, result["version"]["id"])
        assert version.provenance["currency"] == "USDT" and version.provenance["origin_source"] == "fixture_binance"
        assert version.provenance["source_sha256"] == saved["sha256"]
        assert storage.read_version(version)[0]["close"] == Decimal("102")
        assert db.scalar(select(MarketData).where(MarketData.asset_id == "binance_usdm:BTCUSDT")).sha256 == saved["sha256"]
        assert db.scalar(select(DatasetVersion).where(DatasetVersion.asset_id == "coinbase_spot:BTC-USD")) is None


def test_dataset_api_boundaries_versions_and_asof(client, logged_in, isolated_versions, monkeypatch):
    """Require session, CSRF and Origin; return fixed native history with an as-of cutoff."""
    with SessionLocal() as db:
        result = service.ingest_dataset(db, task(db, "2024-01-01", "2024-01-02"), fetcher=page, now_ms=NOW)
    version_id = result["version"]["id"]
    catalog = logged_in.get("/api/datasets/catalog")
    assert catalog.status_code == 200 and len(catalog.json()["items"]) == 3
    assert catalog.json()["live_enabled"] is False
    assert logged_in.get(f"/api/datasets/{version_id}/bars?as_of_ms={NOW - 1}").json()["candles"] == []
    response = logged_in.get(f"/api/datasets/{version_id}/bars").json()
    assert response["candles"][0]["close"] == "102.000000000000000000"
    assert len(logged_in.get("/api/datasets?asset_id=coinbase_spot:BTC-USD").json()["versions"]) == 1
    payload = {"asset_id": "coinbase_spot:BTC-USD", "provider_id": "coinbase_exchange", "start_date": "2024-01-01", "end_date": "2024-01-02"}
    assert logged_in.post("/api/datasets/ingest", json=payload, headers={"Origin": "https://invalid.example"}).status_code == 403
    assert logged_in.post("/api/datasets/ingest", json=payload, headers={"X-CSRF-Token": "invalid"}).status_code == 403
    assert logged_in.post("/api/datasets/ingest", json={**payload, "asset_id": "binance_usdm:BTCUSDT"}).status_code == 422
    assert logged_in.post("/api/datasets/ingest", json={**payload, "url": "https://invalid.example"}).status_code == 422
    monkeypatch.setattr("app.services.jobs.queue", lambda: type("Q", (), {"enqueue": lambda *a, **k: None})())
    created = logged_in.post("/api/datasets/ingest", json=payload)
    assert created.status_code == 202
    assert logged_in.post("/api/datasets/ingest", json=payload).json()["id"] == created.json()["id"]
    logged_in.cookies.clear()
    assert client.get("/api/datasets/catalog").status_code == 401
    assert client.get(f"/api/datasets/{version_id}/bars").status_code == 401


def test_additive_migration_and_database_immutability(isolated_versions):
    """Run 0001→0004 only in quant_test and prove direct SQL cannot mutate a frozen version."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from app.core.config import DATABASE_URL
    from app.core.db import Base, engine
    assert "/quant_test" in DATABASE_URL
    Base.metadata.drop_all(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        connection.execute(text("DROP FUNCTION IF EXISTS reject_dataset_version_mutation()"))
    command.upgrade(Config("alembic.ini"), "head")
    with SessionLocal() as db:
        seed(db)
        result = service.ingest_dataset(db, task(db, "2024-01-01", "2024-01-02"), fetcher=page, now_ms=NOW)
        with pytest.raises(DBAPIError, match="immutable"):
            db.execute(text("UPDATE dataset_versions SET row_count=999 WHERE id=:id"), {"id": result["version"]["id"]})
        db.rollback()
        assert db.get(DatasetVersion, result["version"]["id"]).row_count == 2
