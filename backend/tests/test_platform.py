from datetime import timedelta
from decimal import Decimal
import pytest
from sqlalchemy import select
from app.core.db import SessionLocal
from app.core.security import digest
from app.domains.models import Session, MarketData, Asset, utcnow
from app.services.market_data import normalize, aggregate, save_dataset, candles, read_dataset
from app.adapters.public_binance import BinancePublic, PublicDataError

AID='binance_usdm:BTCUSDT'
START=1784592000000  # 2026-07-21 00:00 UTC

def rows(count=32):
    return [dict(open_time=START+i*900000,close_time=START+(i+1)*900000-1,open='100.100000000001',high='104',low='98',close=str(101+i/100),volume='12.123456789012') for i in range(count)]

def test_unauthenticated_data_and_settings_denied(client):
    for url in ['/api/assets','/api/dashboard','/api/settings','/api/tasks','/api/audit','/api/market/candles?asset_id='+AID]:assert client.get(url).status_code==401

def test_session_cookie_csrf_and_logout(logged_in):
    cookie=next(c for c in logged_in.cookies.jar if c.name=='gq_session')
    assert 'HttpOnly' in cookie._rest and cookie._rest.get('SameSite')=='strict'
    assert logged_in.get('/api/auth/me').json()['username']=='test_admin'
    logged_in.headers.pop('X-CSRF-Token')
    assert logged_in.post('/api/auth/logout').status_code==403
    csrf=logged_in.get('/api/auth/me').json()['csrf_token']
    assert logged_in.post('/api/auth/logout',headers={'X-CSRF-Token':csrf}).status_code==200
    assert logged_in.get('/api/auth/me').status_code==401

def test_login_origin_csrf_and_rate_limits(client):
    payload={'username':'missing','password':'bad'}
    assert client.post('/api/auth/login',json=payload).status_code==403
    assert client.post('/api/auth/login',json=payload,headers={'Origin':'https://evil.invalid'}).status_code==403
    csrf=client.get('/api/auth/status').json()['csrf_token']
    for _ in range(10):assert client.post('/api/auth/login',json=payload,headers={'X-CSRF-Token':csrf}).status_code==401
    assert client.post('/api/auth/login',json=payload,headers={'X-CSRF-Token':csrf}).status_code==429

def test_expired_session_is_rejected(logged_in):
    with SessionLocal() as db:
        session=db.get(Session,digest(logged_in.cookies.get('gq_session')));session.expires_at=utcnow()-timedelta(seconds=1);db.commit()
    assert logged_in.get('/api/assets').status_code==401

def test_password_change_revokes_sessions(logged_in):
    assert logged_in.post('/api/auth/password',json={'current_password':'Testing-only-password-123','new_password':'New-test-password-456'}).status_code==200
    assert logged_in.get('/api/auth/me').status_code==401

def test_asset_priority_identity_and_truthful_capabilities(logged_in):
    assets=logged_in.get('/api/assets').json()
    assert [a['symbol'] for a in assets[:4]]==['KORUUSDT','CLUSDT','NVDAUSDT','BTCUSDT']
    assert all(not a['live_tradable'] for a in assets)
    assert next(a for a in assets if a['id']=='us:NVDA')['underlying_id']==next(a for a in assets if a['symbol']=='NVDAUSDT')['underlying_id']
    caps=logged_in.get('/api/capabilities').json()
    assert all(c['status']=='PLANNED' for c in caps if c['phase']>1)
    assert logged_in.post('/api/execution/orders',json={'side':'BUY'}).status_code==403

def test_settings_validation_persistence_and_csrf(logged_in):
    payload={'timezone':'UTC','default_interval':'1h','default_asset':AID}
    assert logged_in.put('/api/settings/display',json=payload).status_code==200
    assert logged_in.get('/api/settings').json()['display']==payload
    assert logged_in.put('/api/settings/display',json={**payload,'timezone':'invalid'}).status_code==422
    assert logged_in.put('/api/settings/display',json={**payload,'default_asset':'NONEXISTENT'}).status_code==400

@pytest.mark.parametrize('case',['ohlc','conflict','nan','wrong_interval'])
def test_reject_invalid_or_conflicting_data(case):
    dataset=rows(2)
    if case=='ohlc':dataset[0]['high']='1'
    if case=='conflict':dataset.append({**dataset[0],'close':'102'})
    if case=='nan':dataset[0]['open']='NaN'
    if case=='wrong_interval':dataset[0]['close_time']+=100
    with pytest.raises(ValueError):normalize(dataset,START+3600000)

def test_no_future_candle_and_no_upsampling():
    clean=normalize(rows(8),START+4*900000)
    assert len(clean)==4
    with pytest.raises(ValueError):aggregate(clean,'15m','5m')

def test_aggregate_only_complete_causal_buckets():
    clean=normalize(rows(32),START+33*900000)
    grouped,excluded=aggregate(clean,'15m','4h')
    assert len(grouped)==2 and excluded==0
    assert grouped[0]['open']==Decimal('100.100000000001')
    grouped,excluded=aggregate(clean[:-1],'15m','4h')
    assert len(grouped)==1 and excluded==1
    partial=clean.copy();partial.pop(2)
    grouped,excluded=aggregate(partial,'15m','4h')
    assert len(grouped)==1 and excluded==1

def test_parquet_roundtrip_hash_idempotency_and_chart_api(logged_in):
    with SessionLocal() as db:
        result=save_dataset(db,AID,'15m',rows(32),{'provider':'test_real_schema'},START+33*900000)
        first=db.scalar(select(MarketData).where(MarketData.asset_id==AID))
        assert read_dataset(first)[0]['volume']==Decimal('12.123456789012')
        save_dataset(db,AID,'15m',rows(32),{'provider':'test_real_schema'},START+33*900000)
        assert len(list(db.scalars(select(MarketData))))==1
        cut=candles(db,AID,'4h',end_ms=START+16*900000-1)
        assert len(cut['candles'])==1
    response=logged_in.get('/api/market/candles',params={'asset_id':AID,'interval':'4h'})
    assert response.status_code==200 and len(response.json()['candles'])==2
    assert logged_in.get('/api/market/candles',params={'asset_id':AID,'interval':'1m'}).status_code==422

def test_hash_corruption_stops_reading():
    from app.core.config import DATA_ROOT
    with SessionLocal() as db:
        save_dataset(db,AID,'15m',rows(4),{'provider':'test'},START+5*900000)
        item=db.scalar(select(MarketData));(DATA_ROOT/item.path).write_bytes(b'corrupt')
        with pytest.raises(ValueError,match='校验失败'):read_dataset(item)

def test_public_adapter_451_is_not_fabricated(monkeypatch):
    import httpx
    def fake_get(self,*args,**kwargs):return httpx.Response(451,request=httpx.Request('GET','https://fapi.binance.com'))
    monkeypatch.setattr(httpx.Client,'get',fake_get)
    with pytest.raises(PublicDataError,match='HTTP 451'):BinancePublic().exchange_info()

def test_task_dedup_and_real_redis_queue(logged_in):
    one=logged_in.post('/api/tasks',json={'kind':'VERIFY_ASSETS'});two=logged_in.post('/api/tasks',json={'kind':'VERIFY_ASSETS'})
    assert one.status_code==202 and one.json()['id']==two.json()['id']
    from app.services.jobs import queue
    assert queue().fetch_job(one.json()['id']) is not None
    assert logged_in.post('/api/tasks',json={'kind':'FETCH_HISTORY','asset_id':'FAKEUSDT'}).status_code==400
    assert logged_in.post('/api/tasks',json={'kind':'FETCH_HISTORY','asset_id':AID,'days':100}).status_code==422

def test_failed_queue_job_can_be_recovered_in_ui(logged_in):
    from rq.job import JobStatus
    from app.services.jobs import queue
    created=logged_in.post('/api/tasks',json={'kind':'VERIFY_ASSETS'}).json()
    queue().fetch_job(created['id']).set_status(JobStatus.FAILED)
    tasks=logged_in.get('/api/tasks').json()
    assert tasks[0]['status']=='FAILED'
    retried=logged_in.post('/api/tasks',json={'kind':'VERIFY_ASSETS'}).json()
    assert retried['id']!=created['id']

def test_fetch_history_resumes_from_checkpoint_and_is_idempotent(monkeypatch):
    from app.services.jobs import fetch_history
    from app.services import jobs
    end=START+33*900000
    monkeypatch.setattr(jobs.time,'time',lambda:end/1000)
    raw=rows(32)
    first=raw[:16]
    with SessionLocal() as db:
        save_dataset(db,AID,'15m',first,{'provider':'test'},end)
    observed=[]
    def batch(self,symbol,interval,start,finish):
        observed.append(start)
        return [[r[k] for k in ('open_time','open','high','low','close','volume','close_time')] for r in raw if r['open_time']>=start]
    monkeypatch.setattr(BinancePublic,'klines',batch)
    with SessionLocal() as db:
        result=fetch_history(db,{'asset_id':AID,'interval':'15m','days':90})
        assert result['rows']==32 and observed[0]==raw[15]['open_time']
        again=fetch_history(db,{'asset_id':AID,'interval':'15m','days':90})
        assert again['rows']==32
        metadata=db.scalar(select(MarketData).where(MarketData.asset_id==AID))
        assert metadata.row_count==32

def test_public_adapter_reports_dns_failure(monkeypatch):
    import httpx
    def fail(self,*args,**kwargs):raise httpx.ConnectError('[Errno -2] Name or service not known')
    monkeypatch.setattr(httpx.Client,'get',fail)
    with pytest.raises(PublicDataError,match='DNS 解析失败'):BinancePublic().exchange_info()

def test_fresh_clone_uses_public_history_jobs_without_private_data(monkeypatch,tmp_path):
    import app.bootstrap as bootstrap
    import app.core.config as config
    import app.services.jobs as jobs
    created=[]
    monkeypatch.setattr(config,'RESEARCH_ROOT',tmp_path)
    monkeypatch.setattr(jobs,'enqueue',lambda db,kind,parameters:created.append((kind,parameters)))
    bootstrap.main()
    histories=[params for kind,params in created if kind=='FETCH_HISTORY']
    assert [params['asset_id'] for params in histories]==['binance_usdm:'+symbol for symbol in ['KORUUSDT','CLUSDT','NVDAUSDT','BTCUSDT']]
    assert all(params['interval']=='15m' and params['days']==90 for params in histories)
    assert all(kind!='IMPORT_LOCAL' for kind,_ in created)
