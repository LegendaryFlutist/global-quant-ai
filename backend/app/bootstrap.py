import json
import os
import secrets
from pathlib import Path
from datetime import datetime
from sqlalchemy import select
from app.core.config import CONFIG_ROOT
from app.core.db import SessionLocal
from app.core.security import hasher
from app.domains.models import Asset, Alias, Membership, Provider, Setting, User, Audit, MarketData

PRIORITY=[('KORUUSDT','韩国指数 3 倍做多 ETF 相关永续','koru_etf','equity_related_perpetual',['韩国','全球指数','杠杆ETF']),('CLUSDT','原油相关永续','crude_oil','commodity_related_perpetual',['能源','商品']),('NVDAUSDT','英伟达相关永续','nvidia','equity_related_perpetual',['半导体','AI']),('BTCUSDT','比特币永续','bitcoin','crypto_perpetual',['加密货币'])]
OTHER=[('SKHYNIXUSDT','SK 海力士相关永续','sk_hynix',['存储芯片','HBM','半导体']),('SPCXUSDT','SpaceX 相关永续','spacex',['商业航天']),('SNDKUSDT','闪迪相关永续','sandisk',['存储芯片']),('XAUUSDT','黄金相关永续','gold',['贵金属','商品']),('QQQUSDT','纳斯达克 ETF 相关永续','qqq_etf',['全球指数','AI']),('INTCUSDT','英特尔相关永续','intel',['半导体']),('TSLAUSDT','特斯拉相关永续','tesla',['电动车','AI']),('OPENAIUSDT','OpenAI 相关永续','openai',['AI','未上市相关产品']),('ZHIPUUSDT','智谱相关永续','zhipu',['AI'])]
GLOBAL=[('SPX','标普500指数','sp500','index',['全球指数'],'US'),('SPY','标普500 ETF','spy_etf','etf',['全球指数'],'US'),('NDX','纳斯达克100指数','nasdaq100','index',['全球指数','AI'],'US'),('QQQ','纳斯达克100 ETF','qqq_etf','etf',['全球指数','AI'],'US'),('DJI','道琼斯指数','dow_jones','index',['全球指数'],'US'),('IWM','罗素2000 ETF','iwm_etf','etf',['全球指数'],'US'),('VIX','波动率指数','vix','index',['波动率'],'US'),('KORU','韩国指数3倍做多 ETF','koru_etf','leveraged_etf',['韩国','杠杆ETF'],'US'),('QID','纳斯达克2倍做空 ETF','qid_etf','leveraged_etf',['全球指数','杠杆ETF'],'US'),('NVDA','英伟达股票','nvidia','stock',['半导体','AI'],'US'),('MU','美光','micron','stock',['存储芯片','HBM','半导体'],'US'),('AMD','AMD','amd','stock',['半导体','AI'],'US'),('AVGO','博通','broadcom','stock',['半导体','AI'],'US'),('TSM','台积电 ADR','tsmc','stock',['半导体'],'US'),('ASML','阿斯麦','asml','stock',['半导体'],'US'),('000660','SK 海力士','sk_hynix','stock',['存储芯片','HBM','韩国'],'KRX'),('005930','三星电子','samsung','stock',['存储芯片','韩国'],'KRX'),('HSI','恒生指数','hang_seng','index',['香港','全球指数'],'HK'),('HSTECH','恒生科技指数','hang_seng_tech','index',['香港','AI'],'HK'),('000300','沪深300指数','csi300','index',['中国','全球指数'],'CN'),('0700','腾讯','tencent','stock',['香港','AI'],'HK'),('9988','阿里巴巴','alibaba','stock',['香港','AI'],'HK'),('DXY','美元指数','dollar_index','index',['外汇','宏观'],'GLOBAL'),('US10Y','美国十年期国债收益率','us10y_yield','yield',['宏观'],'GLOBAL'),('XAUUSD','现货黄金','gold','commodity_spot',['贵金属','商品'],'GLOBAL'),('CL','WTI 原油期货','crude_oil','commodity_future',['能源','商品'],'NYMEX'),('EURUSD','欧元美元','eur_usd','fx',['外汇'],'GLOBAL')]

def seed(db):
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

def main():
    with SessionLocal() as db:
        seed(db)
        if db.scalar(select(User.id).limit(1)) is None:
            password=secrets.token_urlsafe(24)
            path=Path(os.environ.get('BOOTSTRAP_FILE','/secrets/bootstrap-admin.txt'))
            path.parent.mkdir(parents=True,exist_ok=True)
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
            with os.fdopen(fd,'w') as f:f.write('用户名：admin\n密码：'+password+'\n仅保存在本机；登录后可在设置中修改密码。\n')
            db.add(User(username='admin',password_hash=hasher.hash(password),role='admin'))
            db.add(Audit(actor='system',action='BOOTSTRAP_ADMIN',detail={'credential':'local file only'}));db.commit()
        # Resume queue publication after Redis outage, and enqueue seed imports once.
        from app.services.jobs import enqueue, queue, execute
        from app.domains.models import Task, MarketData
        from rq import Retry
        for task in db.scalars(select(Task).where(Task.status.in_(['QUEUED','QUEUE_ERROR']))):
            if queue().fetch_job(task.id) is None:
                queue().enqueue(execute,task.id,job_id=task.id,retry=Retry(max=2,interval=[5,30]));task.status='QUEUED'
        db.commit()
        for symbol,*_ in PRIORITY:
            aid='binance_usdm:'+symbol
            kinds=['IMPORT_LOCAL','FETCH_HISTORY']
            from app.core.config import RESEARCH_ROOT
            if db.scalar(select(MarketData.id).where(MarketData.asset_id==aid)) is None and not any(t.parameters.get('asset_id')==aid for t in db.scalars(select(Task).where(Task.kind.in_(kinds)))):
                if (RESEARCH_ROOT/'data/priority_analysis_latest.json').is_file():
                    enqueue(db,'IMPORT_LOCAL',{'asset_id':aid})
                else:
                    enqueue(db,'FETCH_HISTORY',{'asset_id':aid,'interval':'15m','days':90})
        enqueue(db,'VERIFY_ASSETS',{})

if __name__=='__main__':main()
