"""Phase 1 market API."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession
from app.core.db import get_db
from app.core.security import current_user
from app.domains.models import User, MarketData, Asset
from app.services.market_data import candles, MINUTES
from app.adapters.public_binance import INTERVALS, PublicDataError
from app.services.long_history import read_archive, validate_archive_identity
from app.services.live_market import read_live_snapshot, refresh_latest_candle
from app.services.structure_analysis import STRUCTURE_INTERVALS, analyze_archived_structure
from app.services.market_sentiment import PRIORITY_INTERVALS, aggregate_priority_sentiment
from app.services.underlying_market import refresh_underlying_latest, underlying_series
from app.adapters.underlying_public import UnderlyingDataError


router=APIRouter()

class LiveRefreshRequest(BaseModel):
    """Validated selection for one current authenticated chart refresh."""
    asset_id:str
    interval:str='15m'

@router.get('/api/market/sentiment')
def market_sentiment(intervals:str=Query('15m,1h,4h,1d',max_length=30),as_of_ms:int|None=None,user:User=Depends(current_user),db:DBSession=Depends(get_db)):
    """汇总四个优先标的的本地多周期结构状态与总体市场周期。

    Args:
        intervals: 逗号分隔的15m/1h/4h/1d原生周期子集。
        as_of_ms: 可选统一UTC毫秒回放截止时点。
        user: 认证依赖已经校验的当前用户。
        db: 本次调用的SQLAlchemy数据库会话。

    Returns:
        透明的方向分、UNKNOWN清单、趋势/背离证据、支撑压力和数据SHA256。
    """
    selected=[item for item in intervals.split(',') if item]
    if not selected or len(selected)>4 or len(set(selected))!=len(selected) or any(item not in PRIORITY_INTERVALS for item in selected):
        raise HTTPException(422,'周期列表只允许15m、1h、4h、1d且不能重复')
    try:
        return aggregate_priority_sentiment(db,selected,as_of_ms)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from None

@router.post('/api/market/live')
def market_live(payload:LiveRefreshRequest,user:User=Depends(current_user),db:DBSession=Depends(get_db)):
    """经CSRF校验后刷新当前图表周期并把新闭合K线追加到本机归档。

    Args:
        payload: 请求中的精确资产ID和原生K线周期。
        user: 认证依赖已经校验的当前用户。
        db: 本次调用的SQLAlchemy数据库会话。

    Returns:
        最近新增闭合K线、当前未闭合K线、更新时间和本地manifest哈希。

    Raises:
        HTTPException: CSRF/身份失败、未核验标的、缺少归档或上游临时不可用。
    """
    asset_id,interval=payload.asset_id,payload.interval
    try:
        asset=db.get(Asset,asset_id)
        if asset is not None and asset.venue!='BINANCE_USDM':
            return refresh_underlying_latest(db,asset_id,interval)
        validate_archive_identity(db,asset_id,interval)
        return refresh_latest_candle(db,asset_id,interval)
    except (PublicDataError,UnderlyingDataError) as exc:
        if asset is not None and asset.venue!='BINANCE_USDM':
            try:
                cached_view=candles(db,asset_id,interval,limit=100)
                return {'asset_id':asset_id,'interval':interval,'closed_candles':[],'live_candle':None,
                        'last_closed_at_ms':cached_view.get('as_of_ms'),'updated_at_ms':None,
                        'dataset_sha256':cached_view.get('dataset_sha256'),'source':cached_view.get('source'),
                        'research_only':True,'stale':True,'refresh_error':str(exc)}
            except ValueError:
                raise HTTPException(503,str(exc)) from None
        snapshot=read_live_snapshot(asset_id,interval)
        if snapshot:
            return {'asset_id':asset_id,'interval':interval,'live_candle':snapshot.get('candle'),'closed_candles':[],'updated_at_ms':snapshot.get('fetched_at_ms'),'source':snapshot.get('source'),'research_only':True,'stale':True,'live_snapshot_sha256':snapshot.get('sha256'),'refresh_error':str(exc)}
        raise HTTPException(503,str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from None

@router.get('/api/market/structure')
def market_structure(asset_id:str,intervals:str=Query('15m,1h,4h,1d',max_length=100),as_of_ms:int|None=None,limit:int=Query(10000,ge=1,le=200000),user:User=Depends(current_user),db:DBSession=Depends(get_db)):
    """返回本地归档的多周期趋势线、123、波浪候选与确认背离。

    Args:
        asset_id: 带市场命名空间的精确产品ID。
        intervals: 逗号分隔的原生周期；只允许已归档周期且最多十个。
        as_of_ms: 可选统一观察/回放截止UTC毫秒时间。
        limit: 每周期最多使用K线根数；高频历史大于资源上限时使用最近样本。
        user: 认证依赖已经校验的当前用户。
        db: 本次调用的SQLAlchemy数据库会话。

    Returns:
        带发生/确认时间、数据SHA256和本地归档缺失状态的研究结构。
    """
    selected=[item for item in intervals.split(',') if item]
    if not selected or len(selected)>10 or len(set(selected))!=len(selected) or any(item not in STRUCTURE_INTERVALS for item in selected):
        raise HTTPException(422,'周期列表必须是不重复的原生K线周期')
    try:
        return analyze_archived_structure(db,asset_id,selected,as_of_ms,limit)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from None

@router.get('/api/market/archive')
def market_archive(asset_id:str,interval:str='4h',limit:int=Query(2000,ge=1,le=10000),end_ms:int|None=None,user:User=Depends(current_user),db:DBSession=Depends(get_db)):
    """读取已核验合约的本地三年归档，不因查看页面发出交易所请求。

    Args:
        asset_id: 带市场命名空间的精确USDT永续产品ID。
        interval: 币安原生周期；缺少高周期归档时可由本地15m事实K线向上聚合。
        limit: 最近最多返回10000根已收盘归档K线。
        end_ms: 可选UTC毫秒回放截止时间。
        user: 认证依赖已经校验的当前用户。
        db: 本次调用的SQLAlchemy数据库会话。

    Returns:
        本地归档/合法高周期聚合及分段哈希；未缓存时返回`available=false`。
    """
    try:
        validate_archive_identity(db,asset_id,interval)
        value=read_archive(asset_id,interval,limit,end_ms)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from None
    return value or {'asset_id':asset_id,'interval':interval,'available':False,'research_only':True}

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

@router.get('/api/market/series')
def market_series(asset_id:str,metric:str|None=None,limit:int=Query(5000,ge=1,le=10000),end_ms:int|None=None,user:User=Depends(current_user),db:DBSession=Depends(get_db)):
    """Read a source-native scalar series such as an EIA daily spot reference.

    Args:
        asset_id: Exact registered reference asset ID.
        metric: Optional allowlisted metric from that asset's server registry.
        limit: Maximum number of actual observations returned.
        end_ms: Optional inclusive UTC millisecond as-of cutoff.
        user: Authenticated user; no provider credential enters the request.
        db: Current SQLAlchemy session.

    Returns:
        Provider observations and SHA256 without converting scalar values to candles.
    """
    try:
        return underlying_series(db,asset_id,metric,limit,end_ms)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from None

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
