"""Source isolation, causal availability and immutable version storage using fixtures."""

import json
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.domains.datasets import DatasetVersion
from app.domains.models import Asset, Provider
from app.storage import research_datasets as storage

NOW = 1_700_000_000_000
SOURCE = {"price_type": "trade", "adjustment": "none", "calendar_version": "24x7_v1",
          "parser_version": "fixture_v1", "license": "TEST_FIXTURE"}


@pytest.fixture
def dataset_store(tmp_path, monkeypatch):
    """Use isolated disk and quant_test products; never import real market or account data."""
    monkeypatch.setattr(storage, "DATA_ROOT", tmp_path)
    with SessionLocal() as db:
        for source in ("fixture_one", "fixture_two"):
            db.add(Provider(id=source, name=source, status="TEST", detail={}))
        db.add(Asset(id="fixture:BTCUSD:SPOT", symbol="BTCUSD", name="Fixture BTC spot",
                     underlying_id="BTC", asset_type="crypto_spot", venue="FIXTURE",
                     verification="TEST", specification={}))
        db.commit()
    return tmp_path


def bars():
    """Return two deterministic closed daily fixtures with exact Decimal prices and zero volume."""
    return [{"open_time": NOW - 3 * 86400000 + index * 86400000,
             "close_time": NOW - 2 * 86400000 + index * 86400000 - 1,
             "open": Decimal("100.123456789012345678"), "high": Decimal("110"),
             "low": Decimal("99"), "close": Decimal("105"), "volume": Decimal("0")}
            for index in range(2)]


def publish(db, rows=None, provider="fixture_one", now=NOW, body=b'{"fixture":true}', provenance=None):
    """Publish controlled public-response fixtures without committing the caller's transaction."""
    return storage.publish_dataset(db, "fixture:BTCUSD:SPOT", provider, "1d", rows or bars(),
                                   provenance or SOURCE,
                                   [{"body": body, "endpoint_id": "fixture_daily", "params": {"symbol": "BTCUSD"}}], now)


def test_version_decimal_duplicates_asof_and_repeat(dataset_store):
    """Preserve decimals and zero volume, deduplicate bars, and exclude unseen history."""
    with SessionLocal() as db:
        version = publish(db, [bars()[1], bars()[0], bars()[0]])
        db.commit()
        rows = storage.read_version(version)
        assert len(rows) == 2 and rows[0]["open"] == Decimal("100.123456789012345678")
        assert rows[0]["volume"] == Decimal("0") and not rows[0]["volume_missing"]
        assert storage.read_version(version, NOW - 1) == []
        assert len(storage.read_version(version, NOW)) == 2
        repeated = publish(db, now=NOW + 10000)
        assert repeated.id == version.id
        assert storage.read_version(repeated)[0]["observed_at_ms"] == NOW
        assert db.scalar(select(func.count()).select_from(DatasetVersion)) == 1


def test_sources_and_revisions_are_separate(dataset_store):
    """Never merge providers or revise frozen prices when a changed source snapshot arrives."""
    with SessionLocal() as db:
        first = publish(db)
        other = publish(db, provider="fixture_two")
        changed = bars()
        changed[0]["close"] = Decimal("106")
        revision = publish(db, changed, now=NOW + 1000, body=b'{"fixture":"revision"}')
        db.commit()
        assert len({first.id, other.id, revision.id}) == 3
        assert first.manifest_path != other.manifest_path
        assert storage.read_version(first)[0]["close"] == Decimal("105")
        assert storage.read_version(revision)[0]["close"] == Decimal("106")
        assert first.dataset_sha256 != revision.dataset_sha256


def test_missing_volume_and_preserved_row_observation(dataset_store):
    """Unknown volume stays null, and existing observed rows keep their true availability."""
    rows = bars()
    rows[0].update(volume=None, observed_at_ms=NOW - 100, available_at_ms=NOW - 50)
    with SessionLocal() as db:
        version = publish(db, rows)
        db.commit()
        actual = storage.read_version(version, NOW - 1)
        assert len(actual) == 1 and actual[0]["volume"] is None and actual[0]["volume_missing"]
        assert version.quality["missing_volume_rows"] == 1
        assert not version.quality["historical_point_in_time_verified"]


@pytest.mark.parametrize("corrupt", ["manifest", "segments", "raw_pages"])
def test_tampered_artifacts_fail_read(dataset_store, corrupt):
    """Reject modified manifest, Parquet or raw response bytes before chart/model consumption."""
    with SessionLocal() as db:
        version = publish(db)
        db.commit()
        manifest_path = dataset_store / version.manifest_path
        manifest = json.loads(manifest_path.read_bytes())
        path = manifest_path if corrupt == "manifest" else manifest_path.parent / manifest[corrupt][0]["file"]
        path.write_bytes(path.read_bytes() + b"corruption")
        with pytest.raises(ValueError, match="SHA256"):
            storage.read_version(version)


def test_conflict_unclosed_precision_and_secret_params_rejected(dataset_store):
    """Reject ambiguous duplicates, unclosed bars, rounded prices and credential fields."""
    with SessionLocal() as db:
        conflict = bars()[0].copy()
        conflict["close"] = Decimal("106")
        with pytest.raises(ValueError, match="冲突"):
            publish(db, [bars()[0], conflict])
        unclosed = bars()
        unclosed[-1]["close_time"] = NOW
        with pytest.raises(ValueError, match="闭合"):
            publish(db, unclosed)
        precision = bars()
        precision[0]["open"] = Decimal("100.1234567890123456789")
        with pytest.raises(ValueError, match="精度"):
            publish(db, precision)
        with pytest.raises(ValueError, match="不允许"):
            storage.publish_dataset(db, "fixture:BTCUSD:SPOT", "fixture_one", "1d", bars(), SOURCE,
                                    [{"body": b"fixture", "endpoint_id": "fixture", "params": {"api_key": "test-only"}}], NOW)
        assert db.scalar(select(func.count()).select_from(DatasetVersion)) == 0


def test_immutable_metadata_and_transaction_recovery(dataset_store):
    """Recover durable files after a rolled-back DB write and reject later ORM mutation."""
    with SessionLocal() as db:
        version_id = publish(db).id
        db.rollback()
        recovered = publish(db, now=NOW + 1000)
        db.commit()
        assert recovered.id == version_id
        assert storage.read_version(recovered)[0]["observed_at_ms"] == NOW
        recovered.row_count = 99
        with pytest.raises(ValueError, match="不可修改"):
            db.flush()
        db.rollback()


def test_manifest_keeps_raw_observation_and_request_allowlist(dataset_store):
    """Persist auditable public request parameters and each page's UTC observation time."""
    with SessionLocal() as db:
        version = storage.publish_dataset(
            db, "fixture:BTCUSD:SPOT", "fixture_one", "1d", bars(), SOURCE,
            [{"body": b"fixture", "endpoint_id": "fixture", "observed_at_ms": NOW - 10,
              "params": {"period1": 0, "period2": NOW // 1000, "includeAdjustedClose": False,
                         "granularity": 86400}}], NOW,
        )
        db.commit()
        manifest = json.loads((dataset_store / version.manifest_path).read_bytes())
        assert manifest["raw_pages"][0]["observed_at_ms"] == NOW - 10
        assert manifest["raw_pages"][0]["params"]["includeAdjustedClose"] is False
