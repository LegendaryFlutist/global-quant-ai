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
    """向已登录用户返回校验过的真实缓存K线。

    Args:
        asset_id: 带市场命名空间的精确产品ID。
        interval: 精确K线周期名称，月线1M区别于分钟。
        limit: 最多返回的已收盘K线条数。
        end_ms: 可选的历史观察截止毫秒时间；不返回结束晚于它的K线。
        user: 认证依赖已经校验的当前用户，不从请求正文接受替代身份。
        db: 本次调用的SQLAlchemy数据库会话。

    Returns:
        包含来源、周期、截止时刻与SHA256的响应；不支持周期报422。
    """
    try:return candles(db,asset_id,interval,limit,end_ms)
    except ValueError as exc:raise HTTPException(422,str(exc)) from None

@router.get('/api/market/intervals')
def market_intervals(asset_id:str,user:User=Depends(current_user),db:DBSession=Depends(get_db)):
    """根据原生数据集判断每个周期是否能直接读取或合法聚合。

    Args:
        asset_id: 带市场命名空间的精确产品ID。
        user: 认证依赖已经校验的当前用户，不从请求正文接受替代身份。
        db: 本次调用的SQLAlchemy数据库会话。

    Returns:
        各周期可用状态与缺失原因，不从大周期伪造小周期。
    """
    sources=list(db.scalars(select(MarketData).where(MarketData.asset_id==asset_id)))
    output=[]
    for interval in INTERVALS:
        available=any(m.interval==interval or m.interval in MINUTES and (interval=='1M' or interval in MINUTES and MINUTES[m.interval]<=MINUTES[interval] and MINUTES[interval]%MINUTES[m.interval]==0) for m in sources)
        output.append({'interval':interval,'available':available,'reason':None if available else '暂无原生小周期数据；可尝试公开接口采集，不做插值'})
    return output
