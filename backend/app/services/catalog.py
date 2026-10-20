"""幂等登记公开资产与供应商状态；不创建用户、不读取API凭据。"""
import json
from datetime import datetime
from sqlalchemy import select
from app.core.config import CONFIG_ROOT
from app.domains.models import Asset, Alias, Membership, Provider, Setting, MarketData
from app.domains.asset_catalog import PRIORITY, OTHER, GLOBAL

def seed(db):
    """幂等登记公开资产、别名、多主题及供应商研究状态。

    Args:
        db: 本次调用的SQLAlchemy数据库会话。

    Returns:
        无返回值；不覆盖已有用户/策略/行情，不创建真实交易权限。
    """
    for provider in [('binance_public','Binance USDT 永续公开接口'),('research_snapshot','已验证本地研究快照'),('research_registry','全球资产研究登记（未接行情源）')]:
        if db.get(Provider,provider[0]) is None:db.add(Provider(id=provider[0],name=provider[1],status='UNKNOWN',detail={}))
    db.flush()
    snapshot=json.loads((CONFIG_ROOT/'exchange_snapshot.json').read_text())
    specifications={s['symbol']:s for s in snapshot['symbols']}
    entries=PRIORITY+[(s,n,u,'commodity_related_perpetual' if s=='XAUUSDT' else 'equity_related_perpetual',t) for s,n,u,t in OTHER]
    for index,(symbol,name,underlying,kind,themes) in enumerate(entries):
        aid='binance_usdm:'+symbol
        if db.get(Asset,aid):continue
        spec=specifications.get(symbol)
        if spec and spec.get('underlyingType')=='PREMARKET':kind='preipo_related_perpetual'
        db.add(Asset(id=aid,symbol=symbol,name=name,underlying_id=underlying,asset_type=kind,venue='BINANCE_USDM',priority=index,verification='VERIFIED_SNAPSHOT' if spec else 'UNVERIFIED',specification=spec or {},verified_at=datetime.fromisoformat(snapshot['retrieved_at'].replace('Z','+00:00')) if spec else None));db.flush()
        db.add(Alias(asset_id=aid,provider_id='binance_public',alias=symbol))
        for theme in themes:db.add(Membership(asset_id=aid,sector=theme))
    for symbol,name,underlying,kind,themes,venue in GLOBAL+json.loads((CONFIG_ROOT/'additional_assets.json').read_text()):
        aid=venue.lower()+':'+symbol
        if db.get(Asset,aid):continue
        db.add(Asset(id=aid,symbol=symbol,name=name,underlying_id=underlying,asset_type=kind,venue=venue,priority=100,verification='RESEARCH_ONLY',specification={'currency':{'US':'USD','HK':'HKD','CN':'CNY','KRX':'KRW'}.get(venue,'UNKNOWN'),'trading_calendar':'UNVERIFIED','listing_range':'UNKNOWN','data_source':'NOT_CONNECTED','symbol_is_provider_verified':False}));db.flush()
        for theme in themes:db.add(Membership(asset_id=aid,sector=theme))
    if db.get(Setting,'display') is None:db.add(Setting(key='display',value={'timezone':'Asia/Shanghai','default_interval':'4h','default_asset':'binance_usdm:KORUUSDT'}))
    if db.get(Setting,'chart_indicators') is None:
        db.add(Setting(key='chart_indicators',value={
            'show_ma':True,'ma_periods':[5,10,20],'show_bollinger':False,
            'show_macd':False,'show_td':False,'show_trendlines':False,
            'show_dow123':False,'show_divergence':False,'show_wave':False,
        }))
    for symbol,_,_,_,themes in entries:
        asset=db.get(Asset,'binance_usdm:'+symbol)
        if asset and asset.specification.get('underlyingType')=='PREMARKET':asset.asset_type='preipo_related_perpetual'
        if symbol=='SPCXUSDT':
            membership=db.get(Membership,(asset.id,'未上市相关产品'))
            if membership:db.delete(membership)
    registry=db.get(Provider,'research_registry');registry.status='REGISTERED_RESEARCH_ONLY';registry.detail={'note':'仅资产登记，尚未接入行情源'}
    existing=db.scalar(select(MarketData).limit(1))
    if existing:
        provider=db.get(Provider,'research_snapshot');provider.status='CONNECTED';provider.detail={'mode':'historical_snapshot','last_success_at':existing.updated_at.isoformat(),'note':'真实历史缓存；不是实时报价'}
    db.commit()
