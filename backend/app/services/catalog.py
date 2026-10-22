"""幂等登记公开资产与供应商状态；不创建用户、不读取API凭据。"""
import json
import os
from datetime import datetime
from sqlalchemy import select
from app.core.config import CONFIG_ROOT
from app.domains.models import Asset, Alias, Membership, Provider, Setting, MarketData
from app.domains.asset_catalog import PRIORITY, OTHER, GLOBAL

def _global_asset_id(symbol, kind, venue):
    """Build a stable ID that separates Chinese indices from same-code equities.

    Args:
        symbol: Exact provider-neutral symbol from the immutable catalog tuple.
        kind: Registered product type, such as index, stock or ETF.
        venue: Catalog venue used for market identity and currency metadata.

    Returns:
        A namespaced asset ID; the existing CSI 300 ID stays unchanged for compatibility.
    """
    if venue == 'CN' and kind == 'index' and symbol != '000300':
        return 'cn_index:' + symbol
    return venue.lower() + ':' + symbol

def seed(db):
    """幂等登记公开资产、别名、多主题及供应商研究状态。

    Args:
        db: 本次调用的SQLAlchemy数据库会话。

    Returns:
        无返回值；不覆盖已有用户/策略/行情，不创建真实交易权限。
    """
    providers = [
        ('binance_public','Binance USDT 永续公开接口'),
        ('research_snapshot','已验证本地研究快照'),
        ('research_registry','全球资产研究登记（未接行情源）'),
        ('massive_stocks','Massive 美国股票与ETF公开行情'),
        ('twelve_data','Twelve Data 韩国股票日线'),
        ('eia','美国能源信息署每日现货参考序列'),
        ('sina_cn_public','新浪中国指数公开行情（研究用途）'),
        ('tencent_cn_public','腾讯中国指数公开行情备用源（研究用途）'),
    ]
    for provider in providers:
        if db.get(Provider,provider[0]) is None:db.add(Provider(id=provider[0],name=provider[1],status='UNKNOWN',detail={}))
    db.flush()
    source_registry=json.loads((CONFIG_ROOT/'market_sources.json').read_text())
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
    global_assets=GLOBAL+json.loads((CONFIG_ROOT/'additional_assets.json').read_text())
    for symbol,name,underlying,kind,themes,venue in global_assets:
        aid=_global_asset_id(symbol,kind,venue)
        if db.get(Asset,aid):continue
        db.add(Asset(id=aid,symbol=symbol,name=name,underlying_id=underlying,asset_type=kind,venue=venue,priority=100,verification='RESEARCH_ONLY',specification={'currency':{'US':'USD','HK':'HKD','CN':'CNY','KRX':'KRW'}.get(venue,'UNKNOWN'),'trading_calendar':'UNVERIFIED','listing_range':'UNKNOWN','data_source':'NOT_CONNECTED','symbol_is_provider_verified':False}));db.flush()
        for theme in themes:db.add(Membership(asset_id=aid,sector=theme))
    # Bind exact underlying products to their source. The perpetual aliases remain
    # separate and are never overwritten by the source's spot/ETF price history.
    configured_sources=dict(source_registry.get('assets',{}))
    for symbol,name,underlying,kind,themes,venue in global_assets:
        asset_id=_global_asset_id(symbol,kind,venue)
        if venue=='US' and kind in {'stock','etf','leveraged_etf'}:
            configured_sources.setdefault(asset_id,{
                'provider_id':'massive_stocks','alias':symbol,'kind':'candles',
                'intervals':['15m','1h','1d'],'exchange':'US',
                'timezone':'America/New_York',
            })
    for asset_id,source in configured_sources.items():
        asset=db.get(Asset,asset_id)
        if asset is None:
            continue
        spec=dict(asset.specification or {})
        if 'market_data_source' not in spec:
            spec['market_data_source']=source
            asset.specification=spec
        else:
            source=spec['market_data_source']
        if not isinstance(source,dict) or not source.get('alias') or not db.get(Provider,source.get('provider_id')):
            continue  # Preserve incomplete/custom bindings; do not silently replace them at startup.
        alias=source['alias']
        for provider_id in [source['provider_id'], *source.get('fallback_provider_ids',[])]:
            existing_alias=db.scalar(select(Alias).where(Alias.provider_id==provider_id,Alias.alias==alias))
            if existing_alias is None and db.get(Provider,provider_id):
                db.add(Alias(asset_id=asset_id,provider_id=provider_id,alias=alias))
    for provider_id,contract in source_registry.get('providers',{}).items():
        provider=db.get(Provider,provider_id)
        credential_env=contract.get('credential_env')
        configured=bool(os.environ.get(credential_env, '').strip()) if credential_env else False
        if provider.status not in {'CONNECTED','DEGRADED','UNAVAILABLE'}:
            provider.status=(
                'PUBLIC_NO_KEY' if credential_env is None
                else 'CONFIGURED_NOT_VALIDATED' if configured
                else 'NEEDS_CONFIGURATION'
            )
        provider.detail={**(provider.detail or {}),
            'credential_env':credential_env,
            'credential_configured':configured,
            'credential_required':credential_env is not None,
            'freshness_contract':contract['freshness'],
            'data_kind':contract['data_kind'],
            'request_pacing':contract.get('request_pacing'),
            'terms_note':contract.get('terms_note'),
        }
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
    registry=db.get(Provider,'research_registry');registry.status='REGISTERED_RESEARCH_ONLY';registry.detail={'note':'仅资产登记；底层行情需配置供应商后验证'}
    existing=db.scalar(select(MarketData).limit(1))
    if existing:
        provider=db.get(Provider,'research_snapshot');provider.status='CONNECTED';provider.detail={'mode':'historical_snapshot','last_success_at':existing.updated_at.isoformat(),'note':'真实历史缓存；不是实时报价'}
    from app.services.research_data import seed_research_catalog
    seed_research_catalog(db)
    from app.services.instruments import seed_instrument_timelines
    seed_instrument_timelines(db)
    db.commit()
