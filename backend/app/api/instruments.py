"""Read-only authenticated effective/known-at specification queries; no execution capability."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import current_user
from app.domains.models import Asset, User, utcnow
from app.domains.instruments import InstrumentVersion
from app.services.instruments import read_timeline, resolve_specification, resolve_alias

router = APIRouter(prefix="/api/instruments")


@router.get("/specification")
def specification(asset_id: str = Query(min_length=1, max_length=100), effective_at: AwareDatetime | None = None,
                  known_at: AwareDatetime | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Return rules at a zoned instant using only metadata received by the optional knowledge cutoff."""
    if db.get(Asset, asset_id) is None:
        raise HTTPException(404, "资产未登记")
    now = utcnow()
    try:
        return resolve_specification(db, asset_id, effective_at or now, known_at or now)
    except ValueError:
        raise HTTPException(409, "产品规格版本无效或完整性检查失败") from None


@router.get("/history")
def history(asset_id: str = Query(min_length=1, max_length=100), user: User = Depends(current_user), db: Session = Depends(get_db)):
    """List up to 50 immutable snapshots with evidence and exact Decimal strings for inspection."""
    if db.get(Asset, asset_id) is None:
        raise HTTPException(404, "资产未登记")
    versions = db.scalars(select(InstrumentVersion).where(InstrumentVersion.asset_id == asset_id)
                         .order_by(InstrumentVersion.recorded_at.desc()).limit(50))
    try:
        return {"asset_id": asset_id, "versions": [{"id": version.id, "recorded_at": version.recorded_at,
                 "timeline": read_timeline(db, version).model_dump(mode="json")} for version in versions],
                 "research_only": True, "live_tradable": False}
    except ValueError:
        raise HTTPException(409, "产品规格版本无效或完整性检查失败") from None


@router.get("/resolve-alias")
def alias(provider_id: str = Query(min_length=1, max_length=50), symbol: str = Query(min_length=1, max_length=100),
          effective_at: AwareDatetime | None = None, known_at: AwareDatetime | None = None,
          user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Resolve a source-native string without stripping zeros or merging related instruments."""
    now = utcnow()
    try:
        result = resolve_alias(db, provider_id, symbol, effective_at or now, known_at or now)
    except ValueError:
        raise HTTPException(409, "供应商代码存在歧义或版本校验失败") from None
    if result is None:
        raise HTTPException(404, "指定时点没有已知有效的供应商代码映射")
    return result
