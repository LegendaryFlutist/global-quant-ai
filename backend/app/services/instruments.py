"""Atomic timeline publishing and bitemporal reads; no price acquisition or execution."""
import hashlib
import json
from datetime import datetime

from sqlalchemy import select, text

from app.core.config import CONFIG_ROOT
from app.domains.instrument_contracts import InstrumentTimeline
from app.domains.instruments import InstrumentAliasPeriod, InstrumentPeriod, InstrumentVersion
from app.domains.models import Asset, Membership, Provider, utcnow

NUMERIC_FIELDS = ("lot_size", "tick_size", "multiplier", "min_notional")
PERIOD_COLUMNS = ("effective_from", "effective_to", *NUMERIC_FIELDS)


def fingerprint(timeline: InstrumentTimeline) -> str:
    """Hash the complete normalized specification, alias and evidence snapshot without floats."""
    return hashlib.sha256(json.dumps(timeline.model_dump(mode="json"), sort_keys=True,
                                   separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def publish_timeline(db, payload: dict) -> InstrumentVersion:
    """Append an entire validated timeline atomically; caller commits, actual receipt time is server-owned.

    Same-content imports are idempotent and never promote an old version to current.
    Existing Asset fields/source bindings are not changed. No externally supplied
    known-at timestamp can turn a newly received announcement into prior knowledge.
    """
    timeline = InstrumentTimeline.model_validate(payload)
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"instrument:{timeline.asset_id}"})
    if db.get(Asset, timeline.asset_id) is None:
        raise ValueError("资产尚未登记")
    for alias in timeline.aliases:
        if db.get(Provider, alias.provider_id) is None:
            raise ValueError("别名供应商尚未登记")
    version_id = fingerprint(timeline)
    existing = db.get(InstrumentVersion, version_id)
    if existing is not None:
        read_timeline(db, existing)
        return existing
    descriptor = timeline.model_dump(mode="json", exclude={"periods", "aliases"})
    version = InstrumentVersion(id=version_id, asset_id=timeline.asset_id, recorded_at=utcnow(), descriptor=descriptor)
    db.add(version)
    db.flush()
    for ordinal, period in enumerate(timeline.periods):
        db.add(InstrumentPeriod(version_id=version.id, ordinal=ordinal,
            **{key: getattr(period, key) for key in PERIOD_COLUMNS},
            rules=period.model_dump(mode="json", exclude=set(PERIOD_COLUMNS))))
    for ordinal, alias in enumerate(timeline.aliases):
        db.add(InstrumentAliasPeriod(version_id=version.id, ordinal=ordinal, **alias.model_dump()))
    db.flush()
    return version


def read_timeline(db, version: InstrumentVersion) -> InstrumentTimeline:
    """Reconstruct and verify the entire snapshot, rejecting missing/extra/tampered child records."""
    periods = [{**period.rules, **{key: getattr(period, key) for key in PERIOD_COLUMNS}}
               for period in db.scalars(select(InstrumentPeriod).where(InstrumentPeriod.version_id == version.id)
                                        .order_by(InstrumentPeriod.ordinal))]
    aliases = [{key: getattr(alias, key) for key in ("provider_id", "alias", "effective_from", "effective_to")}
               for alias in db.scalars(select(InstrumentAliasPeriod).where(InstrumentAliasPeriod.version_id == version.id)
                                       .order_by(InstrumentAliasPeriod.ordinal))]
    timeline = InstrumentTimeline.model_validate({**version.descriptor, "periods": periods, "aliases": aliases})
    if timeline.asset_id != version.asset_id or fingerprint(timeline) != version.id:
        raise ValueError("产品规格版本完整性校验失败")
    return timeline


def latest_known_version(db, asset_id: str, known_at: datetime) -> InstrumentVersion | None:
    """Select only snapshots actually recorded by the UTC cutoff; never infer historic reception."""
    return db.scalar(select(InstrumentVersion).where(InstrumentVersion.asset_id == asset_id,
                     InstrumentVersion.recorded_at <= known_at).order_by(InstrumentVersion.recorded_at.desc()).limit(1))


def contains(period, when: datetime) -> bool:
    """Evaluate [effective_from, effective_to) in UTC without extending gaps or earlier listing history."""
    return period.effective_from <= when and (period.effective_to is None or when < period.effective_to)


def resolve_specification(db, asset_id: str, effective_at: datetime, known_at: datetime) -> dict:
    """Resolve effective rules using the latest snapshot known then; UNKNOWN stays non-executable.

    Source publication dates are evidence only. A corrected snapshot with a gap
    does not fall back to an older snapshot, and data presence is never inferred.
    """
    version = latest_known_version(db, asset_id, known_at)
    result = {"asset_id": asset_id, "effective_at": effective_at, "known_at": known_at,
              "version_id": version.id if version else None, "recorded_at": version.recorded_at if version else None,
              "status": "NOT_KNOWN", "specification": None, "aliases": [],
              "research_only": True, "live_tradable": False, "execution_ready": False}
    if version is None:
        return result
    timeline = read_timeline(db, version)
    result["descriptor"] = version.descriptor
    result["status"] = "NO_EFFECTIVE_SPEC"
    for period in timeline.periods:
        if contains(period, effective_at):
            result["status"] = "SPEC_AVAILABLE_RESEARCH_ONLY"
            result["specification"] = period.model_dump(mode="json")
            result["aliases"] = [alias.model_dump(mode="json") for alias in timeline.aliases if contains(alias, effective_at)]
            break
    return result


def resolve_alias(db, provider_id: str, alias: str, effective_at: datetime, known_at: datetime) -> dict | None:
    """Resolve an exact provider code against current-known timelines; ambiguity fails closed.

    Old aliases cannot match after their validity ends or after a known correction
    removes them. Identical codes at different providers remain separate products.
    """
    candidates = db.scalars(select(InstrumentVersion.asset_id).join(InstrumentAliasPeriod)
                           .where(InstrumentAliasPeriod.provider_id == provider_id,
                                  InstrumentAliasPeriod.alias == alias).distinct())
    matches = []
    for asset_id in candidates:
        resolved = resolve_specification(db, asset_id, effective_at, known_at)
        if any(item["provider_id"] == provider_id and item["alias"] == alias for item in resolved["aliases"]):
            matches.append(resolved)
    if len(matches) > 1:
        raise ValueError("同一供应商代码在该时点对应多个产品，需核对来源身份")
    return matches[0] if matches else None


def seed_instrument_timelines(db):
    """Add the reviewed small pool once, preserving manually edited assets and all later versions.

    Evidence URLs are citations, never fetched at startup. An existing timeline
    blocks automatic replacement; future changes require explicit reviewed import.
    """
    catalog = json.loads((CONFIG_ROOT / "instrument_timelines.json").read_text())
    for entry in catalog["items"]:
        metadata, timeline = entry["asset"], entry["timeline"]
        asset_id = metadata["id"]
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"instrument:{asset_id}"})
        if db.get(Asset, asset_id) is None:
            db.add(Asset(**metadata, priority=110, verification="RESEARCH_ONLY",
                         specification={"data_source": "NOT_CONNECTED", "symbol_is_provider_verified": False}))
            db.flush()
            db.add(Membership(asset_id=asset_id, sector="全球小池"))
        for provider in entry["providers"]:
            if db.get(Provider, provider["id"]) is None:
                db.add(Provider(**provider, status="REFERENCE_ONLY", detail={"note": "产品证据及代码登记；不是行情连接"}))
        db.flush()
        if not db.scalar(select(InstrumentVersion.id).where(InstrumentVersion.asset_id == asset_id).limit(1)):
            publish_timeline(db, timeline)
