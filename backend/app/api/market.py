"""Phase 1 market API."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession
from app.core.db import get_db
from app.core.security import current_user
from app.domains.models import User, MarketData
from app.services.market_data import candles, MINUTES
from app.adapters.public_binance import INTERVALS


router=APIRouter()

@router.get('/api/market/candles')
def market_candles(asset_id:str,interval:str='4h',limit:int=Query(800,ge=1,le=10000),end_ms:int|None=None,user:User=Depends(current_user),db:DBSession=Depends(get_db)):
    try:return candles(db,asset_id,interval,limit,end_ms)
    except ValueError as exc:raise HTTPException(422,str(exc)) from None

@router.get('/api/market/intervals')
def market_intervals(asset_id:str,user:User=Depends(current_user),db:DBSession=Depends(get_db)):
    sources=list(db.scalars(select(MarketData).where(MarketData.asset_id==asset_id)))
    output=[]
    for interval in INTERVALS:
        available=any(m.interval==interval or m.interval in MINUTES and (interval=='1M' or interval in MINUTES and MINUTES[m.interval]<=MINUTES[interval] and MINUTES[interval]%MINUTES[m.interval]==0) for m in sources)
        output.append({'interval':interval,'available':available,'reason':None if available else '暂无原生小周期数据；可尝试公开接口采集，不做插值'})
    return output
