"""Phase 1 assets API."""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession
from app.core.db import get_db
from app.core.security import current_user
from app.domains.models import User, Asset, Provider, Membership, MarketData


router=APIRouter()

def asset_dict(db,asset):
    """组装资产身份、主题、核验状态与历史数据覆盖范围。

    Args:
        db: 本次调用的SQLAlchemy数据库会话。
        asset: 资产ORM记录。

    Returns:
        适合API序列化的资产字典；live_tradable始终为False。
    """
    data=list(db.scalars(select(MarketData).where(MarketData.asset_id==asset.id)))
    return {'id':asset.id,'symbol':asset.symbol,'name':asset.name,'underlying_id':asset.underlying_id,'asset_type':asset.asset_type,'venue':asset.venue,'priority':asset.priority,'verification':asset.verification,'verified_at':asset.verified_at,'themes':list(db.scalars(select(Membership.sector).where(Membership.asset_id==asset.id))),'specification':asset.specification,'datasets':[{'interval':m.interval,'rows':m.row_count,'as_of_ms':m.last_close_ms,'first_open_ms':m.first_open_ms,'sha256':m.sha256} for m in data],'live_tradable':False}

@router.get('/api/assets')
def assets(user:User=Depends(current_user),db:DBSession=Depends(get_db)):
    """向已登录用户返回按优先级排序的资产注册表。

    Args:
        user: 认证依赖已经校验的当前用户，不从请求正文接受替代身份。
        db: 本次调用的SQLAlchemy数据库会话。

    Returns:
        资产字典列表，不包含凭据或私人账户余额。
    """
    return [asset_dict(db,a) for a in db.scalars(select(Asset).order_by(Asset.priority,Asset.symbol))]

@router.get('/api/providers')
def providers(user:User=Depends(current_user),db:DBSession=Depends(get_db)):
    """返回公开数据供应商的实际连接状态与核验记录。

    Args:
        user: 认证依赖已经校验的当前用户，不从请求正文接受替代身份。
        db: 本次调用的SQLAlchemy数据库会话。

    Returns:
        供应商状态列表；未知与失败不伪装为已连接。
    """
    return [{'id':p.id,'name':p.name,'status':p.status,'detail':p.detail} for p in db.scalars(select(Provider))]
