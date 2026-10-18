"""Phase 1 assets API."""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession
from app.core.db import get_db
from app.core.security import current_user
from app.domains.models import User, Asset, Provider, Membership, MarketData


router=APIRouter()

def asset_dict(db,asset):
    data=list(db.scalars(select(MarketData).where(MarketData.asset_id==asset.id)))
    return {'id':asset.id,'symbol':asset.symbol,'name':asset.name,'underlying_id':asset.underlying_id,'asset_type':asset.asset_type,'venue':asset.venue,'priority':asset.priority,'verification':asset.verification,'verified_at':asset.verified_at,'themes':list(db.scalars(select(Membership.sector).where(Membership.asset_id==asset.id))),'specification':asset.specification,'datasets':[{'interval':m.interval,'rows':m.row_count,'as_of_ms':m.last_close_ms,'first_open_ms':m.first_open_ms,'sha256':m.sha256} for m in data],'live_tradable':False}

@router.get('/api/assets')
def assets(user:User=Depends(current_user),db:DBSession=Depends(get_db)):
    return [asset_dict(db,a) for a in db.scalars(select(Asset).order_by(Asset.priority,Asset.symbol))]

@router.get('/api/providers')
def providers(user:User=Depends(current_user),db:DBSession=Depends(get_db)):
    return [{'id':p.id,'name':p.name,'status':p.status,'detail':p.detail} for p in db.scalars(select(Provider))]
