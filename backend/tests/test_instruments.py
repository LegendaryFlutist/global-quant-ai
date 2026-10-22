"""Historical rule and source-code isolation using real PostgreSQL, never execution APIs."""
import copy
import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.config import CONFIG_ROOT
from app.core.db import SessionLocal
from app.domains.instrument_contracts import InstrumentTimeline
from app.domains.instruments import InstrumentAliasPeriod, InstrumentPeriod, InstrumentVersion
from app.domains.models import Asset, utcnow
from app.services.catalog import seed
from app.services.instruments import publish_timeline, read_timeline, resolve_alias, resolve_specification


def payload(index=0):
    """Read only the public checked-in timeline; return a fresh copy for isolated corrections."""
    return json.loads((CONFIG_ROOT / "instrument_timelines.json").read_text())["items"][index]["timeline"]


def instant(value):
    """Parse an explicitly zoned ISO instant for exact boundary and knowledge-cutoff assertions."""
    return datetime.fromisoformat(value)


def test_jpx_change_boundary_and_actual_knowledge():
    """The actual 1321 2026-10-05 rule change must not backfill lot=10 into prior sessions."""
    with SessionLocal() as db:
        version = db.scalar(select(InstrumentVersion).where(InstrumentVersion.asset_id == "xtks:1321"))
        boundary = instant("2026-10-05T00:00:00+09:00")
        before = resolve_specification(db, "xtks:1321", boundary - timedelta(microseconds=1), utcnow())
        after = resolve_specification(db, "xtks:1321", boundary, utcnow())
        assert before["specification"]["lot_size"] == "1"
        assert after["specification"]["lot_size"] == "10"
        assert before["specification"]["tick_rule"] == "JPX_ONE_UNIT_ETF_TABLE"
        assert after["specification"]["tick_rule"] == "JPX_TOPIX500_TABLE"
        assert after["specification"]["tick_size"] is None
        assert after["specification"]["cash_settlement"] == "UNKNOWN"
        assert after["execution_ready"] is False and after["live_tradable"] is False
        assert resolve_specification(db, "xtks:1321", boundary, version.recorded_at - timedelta(microseconds=1))["status"] == "NOT_KNOWN"
        assert resolve_specification(db, "xtks:1321", boundary, version.recorded_at)["version_id"] == version.id
        assert resolve_specification(db, "xtks:1321", instant("2002-01-01T00:00:00Z"), utcnow())["status"] == "NO_EFFECTIVE_SPEC"


def test_seed_preserves_manual_metadata_and_timeline_corrections():
    """A reviewed correction is new knowledge; restart cannot reset it or edit legacy bindings."""
    with SessionLocal() as db:
        asset = db.get(Asset, "xtks:1321")
        asset.name = "manual display name"
        asset.specification = {"market_data_source": {"provider_id": "research_registry", "alias": "manual"}}
        old = db.scalar(select(InstrumentVersion).where(InstrumentVersion.asset_id == asset.id))
        corrected = read_timeline(db, old).model_dump(mode="json")
        corrected["note"] = "Isolated test correction: removes later coverage without filling it from old rules"
        corrected["periods"] = corrected["periods"][:1]
        current = publish_timeline(db, corrected)
        db.commit()
        assert current.id != old.id
        assert read_timeline(db, old).periods[-1].lot_size == Decimal("10")
        when = instant("2026-10-10T00:00:00Z")
        assert resolve_specification(db, asset.id, when, old.recorded_at)["specification"]["lot_size"] == "10"
        assert resolve_specification(db, asset.id, when, current.recorded_at)["status"] == "NO_EFFECTIVE_SPEC"
        seed(db)
        db.refresh(asset)
        assert asset.name == "manual display name" and asset.specification["market_data_source"]["alias"] == "manual"
        assert db.scalar(select(func.count()).select_from(InstrumentVersion).where(InstrumentVersion.asset_id == asset.id)) == 2
        assert publish_timeline(db, payload()).id == old.id
        assert resolve_specification(db, asset.id, when, utcnow())["version_id"] == current.id


def test_alias_validity_leading_zeroes_and_ambiguity():
    """Exact provider codes respect cutoffs, leading zeroes and known corrections; collisions reject."""
    with SessionLocal() as db:
        body = payload()
        body["aliases"] = [{"provider_id": "jpx_reference", "alias": "001321",
                            "effective_from": "2026-09-15T00:00:00+09:00", "effective_to": "2026-10-05T00:00:00+09:00"}]
        version = publish_timeline(db, body)
        db.commit()
        when = instant("2026-10-01T00:00:00Z")
        assert resolve_alias(db, "jpx_reference", "001321", when, utcnow())["asset_id"] == "xtks:1321"
        assert resolve_alias(db, "jpx_reference", "1321", when, utcnow()) is None
        assert resolve_alias(db, "ishares_reference", "001321", when, utcnow()) is None
        assert resolve_alias(db, "jpx_reference", "001321", instant("2026-10-05T00:00:00+09:00"), utcnow()) is None
        assert resolve_alias(db, "jpx_reference", "001321", when, version.recorded_at - timedelta(microseconds=1)) is None
        other = copy.deepcopy(body)
        other["asset_id"] = "xetr:EXW1"
        publish_timeline(db, other)
        db.commit()
        with pytest.raises(ValueError, match="多个产品"):
            resolve_alias(db, "jpx_reference", "001321", when, utcnow())


@pytest.mark.parametrize("problem", ["overlap", "naive_time", "negative", "too_precise", "credentials", "live_flag", "alias_overlap"])
def test_invalid_timeline_rejected(problem):
    """Reject ambiguous periods, unsafe citations and numeric/time coercions before durable publication."""
    body = payload()
    if problem == "overlap":
        body["periods"][0]["effective_to"] = "2026-10-06T00:00:00+09:00"
    elif problem == "naive_time":
        body["periods"][0]["effective_from"] = "2026-09-15T00:00:00"
    elif problem == "negative":
        body["periods"][0]["lot_size"] = "-1"
    elif problem == "too_precise":
        body["periods"][0]["lot_size"] = "0.0000000000000000001"
    elif problem == "credentials":
        body["evidence"][0]["url"] = "https://user:password@example.invalid"
    elif problem == "live_flag":
        body["live_enabled"] = True
    else:
        body["aliases"].append(copy.deepcopy(body["aliases"][0]))
    with pytest.raises(ValidationError):
        InstrumentTimeline.model_validate(body)


def test_exact_numeric_roundtrip_and_immutable_snapshot_integrity():
    """NUMERIC survives roundtrip; ORM edits and undeclared child additions cannot silently change rules."""
    with SessionLocal() as db:
        body = payload(1)
        body["periods"][0]["min_notional"] = "12345678901234567890.123456789012345678"
        version = publish_timeline(db, body)
        db.commit()
        db.expire_all()
        assert str(read_timeline(db, version).periods[0].min_notional) == "12345678901234567890.123456789012345678"
        period = db.get(InstrumentPeriod, (version.id, 0))
        period.lot_size = Decimal("123")
        with pytest.raises(ValueError, match="不可修改"):
            db.commit()
        db.rollback()
        db.add(InstrumentAliasPeriod(version_id=version.id, ordinal=99, provider_id="jpx_reference",
                                     alias="extra", effective_from=instant("2026-10-22T00:00:00Z")))
        db.commit()
        with pytest.raises(ValueError, match="完整性"):
            read_timeline(db, version)


def test_instrument_api_auth_history_and_boundaries(client, logged_in):
    """Authenticated read-only history cannot accept naive timestamps or turn specifications into orders."""
    root = "/api/instruments"
    assert logged_in.get(root + "/history?asset_id=xtks:1321").json()["versions"][0]["timeline"]["periods"][1]["lot_size"] == "10"
    result = logged_in.get(root + "/specification", params={"asset_id": "xtks:1321", "effective_at": "2026-10-04T15:00:00Z"})
    assert result.status_code == 200 and result.json()["specification"]["lot_size"] == "10"
    assert result.json()["live_tradable"] is False
    assert logged_in.get(root + "/specification", params={"asset_id": "xtks:1321", "effective_at": "2026-10-05"}).status_code == 422
    assert logged_in.get(root + "/specification?asset_id=us:NVDA").json()["status"] == "NOT_KNOWN"
    assert logged_in.get(root + "/specification?asset_id=missing").status_code == 404
    assert logged_in.get(root + "/resolve-alias", params={"provider_id": "jpx_reference", "symbol": "1321"}).json()["asset_id"] == "xtks:1321"
    assert logged_in.post(root + "/specification", json={"live_enabled": True}).status_code == 405
    logged_in.cookies.clear()
    for url in ("/history?asset_id=xtks:1321", "/specification?asset_id=xtks:1321", "/resolve-alias?provider_id=jpx_reference&symbol=1321"):
        assert client.get(root + url).status_code == 401
