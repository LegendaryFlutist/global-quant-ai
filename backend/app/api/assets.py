"""Phase 1 assets API."""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession
from app.core.db import get_db
from app.core.security import current_user
from app.domains.models import User, Asset, Provider, Membership, MarketData, MarketObservation


router=APIRouter()

def classify_asset(asset):
    """按底层资产类别映射注册表条目，保留永续/现货产品身份差异。

    Args:
        asset: 资产注册表ORM记录。

    Returns:
        `crypto`、`stocks`、`etf`或`other`中的一个类别标识。
    """
    if asset.asset_type in {"crypto_spot", "crypto_perpetual"}:
        return "crypto"
    if asset.asset_type in {"etf", "leveraged_etf"} or asset.underlying_id.endswith("_etf"):
        return "etf"
    if asset.asset_type in {"stock", "equity_related_perpetual", "preipo_related_perpetual"}:
        return "stocks"
    return "other"

def classify_product_type(asset):
    """按已登记交易产品类型标注现货、永续、期货或参考数据。

    Args:
        asset: 资产注册表ORM记录；venue和asset_type必须来自已有目录。

    Returns:
        `spot`、`perpetual`、`future`、`reference`或`other`产品标识。
    """
    if asset.venue == "BINANCE_USDM":
        return "perpetual"
    if asset.asset_type.endswith("_spot") or asset.asset_type in {"stock", "etf", "leveraged_etf"}:
        return "spot"
    if asset.asset_type == "commodity_future":
        return "future"
    if asset.asset_type in {
        "index", "yield", "dollar_index", "fx", "macro_indicator",
        "industry_indicator", "company_research", "commodity_indicator",
    }:
        return "reference"
    return "other"

def asset_dict(db,asset):
    """组装资产身份、主题、核验状态与历史数据覆盖范围。

    Args:
        db: 本次调用的SQLAlchemy数据库会话。
        asset: 资产ORM记录。

    Returns:
        适合API序列化的资产字典；live_tradable始终为False。
    """
    data=list(db.scalars(select(MarketData).where(MarketData.asset_id==asset.id)))
    observations=list(db.scalars(select(MarketObservation).where(MarketObservation.asset_id==asset.id)))
    binding=(asset.specification or {}).get('market_data_source')
    provider=db.get(Provider,binding['provider_id']) if isinstance(binding,dict) and binding.get('provider_id') else None
    source_status={
        'provider_id':binding.get('provider_id'),
        'kind':binding.get('kind'),
        'intervals':binding.get('intervals',[]),
        'provider_status':provider.status if provider else 'UNKNOWN',
        'credential_configured':bool((provider.detail or {}).get('credential_configured')) if provider else False,
        'latest_data_at_ms':max((item.last_close_ms for item in data),default=max((item.last_observed_at_ms for item in observations),default=None)),
    } if binding else None
    return {'id':asset.id,'symbol':asset.symbol,'name':asset.name,'underlying_id':asset.underlying_id,'asset_type':asset.asset_type,'market_category':classify_asset(asset),'product_type':classify_product_type(asset),'venue':asset.venue,'priority':asset.priority,'verification':asset.verification,'verified_at':asset.verified_at,'themes':list(db.scalars(select(Membership.sector).where(Membership.asset_id==asset.id))),'specification':asset.specification,'market_source':source_status,'datasets':[{'interval':m.interval,'rows':m.row_count,'as_of_ms':m.last_close_ms,'first_open_ms':m.first_open_ms,'sha256':m.sha256} for m in data],'series':[{'metric':item.metric,'rows':item.row_count,'as_of_ms':item.last_observed_at_ms,'unit':item.unit,'sha256':item.sha256} for item in observations],'live_tradable':False}

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
