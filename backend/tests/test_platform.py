from datetime import timedelta
from decimal import Decimal
import pytest
from sqlalchemy import select
from app.core.db import SessionLocal
from app.core.security import digest
from app.domains.models import Session, MarketData, Asset, Task, utcnow
from app.services.market_data import normalize, aggregate, save_dataset, candles, read_dataset
from app.adapters.public_binance import BinancePublic, PublicDataError

AID='binance_usdm:BTCUSDT'
START=1784592000000  # 2026-07-21 00:00 UTC

def rows(count=32):
    """构造具有精确Decimal和时间戳的最小K线记录列表。

    Args:
        count: 当前测试/接口参数，含义见该函数的具体场景。

    Returns:
        测试夹具、构造结果或断言辅助返回值；不影响真实数据。
    """
    return [dict(open_time=START+i*900000,close_time=START+(i+1)*900000-1,open='100.100000000001',high='104',low='98',close=str(101+i/100),volume='12.123456789012') for i in range(count)]

def test_unauthenticated_data_and_settings_denied(client):
    """验证未登录不能读取业务行情、设置或审计。

    Args:
        client: 当前测试/接口参数，含义见该函数的具体场景。

    Returns:
        无返回值；任一断言失败会使测试失败。
    """
    for url in ['/api/assets','/api/dashboard','/api/settings','/api/tasks','/api/audit','/api/market/candles?asset_id='+AID]:assert client.get(url).status_code==401

def test_session_cookie_csrf_and_logout(logged_in):
    """验证严格Cookie、写请求CSRF和退出后失效。

    Args:
        logged_in: 当前测试/接口参数，含义见该函数的具体场景。

    Returns:
        无返回值；任一断言失败会使测试失败。
    """
    cookie=next(c for c in logged_in.cookies.jar if c.name=='gq_session')
    assert 'HttpOnly' in cookie._rest and cookie._rest.get('SameSite')=='strict'
    assert logged_in.get('/api/auth/me').json()['username']=='test_admin'
    logged_in.headers.pop('X-CSRF-Token')
    assert logged_in.post('/api/auth/logout').status_code==403
    csrf=logged_in.get('/api/auth/me').json()['csrf_token']
    assert logged_in.post('/api/auth/logout',headers={'X-CSRF-Token':csrf}).status_code==200
    assert logged_in.get('/api/auth/me').status_code==401

def test_login_origin_csrf_and_rate_limits(client):
    """验证登录来源、一次性CSRF和Redis限流。

    Args:
        client: 当前测试/接口参数，含义见该函数的具体场景。

    Returns:
        无返回值；任一断言失败会使测试失败。
    """
    payload={'username':'missing','password':'bad'}
    assert client.post('/api/auth/login',json=payload).status_code==403
    assert client.post('/api/auth/login',json=payload,headers={'Origin':'https://evil.invalid'}).status_code==403
    csrf=client.get('/api/auth/status').json()['csrf_token']
    for _ in range(10):assert client.post('/api/auth/login',json=payload,headers={'X-CSRF-Token':csrf}).status_code==401
    assert client.post('/api/auth/login',json=payload,headers={'X-CSRF-Token':csrf}).status_code==429

def test_expired_session_is_rejected(logged_in):
    """验证过期会话不能读取资产。

    Args:
        logged_in: 当前测试/接口参数，含义见该函数的具体场景。

    Returns:
        无返回值；任一断言失败会使测试失败。
    """
    with SessionLocal() as db:
        session=db.get(Session,digest(logged_in.cookies.get('gq_session')));session.expires_at=utcnow()-timedelta(seconds=1);db.commit()
    assert logged_in.get('/api/assets').status_code==401

def test_password_change_revokes_sessions(logged_in):
    """验证改密后全部会话失效。

    Args:
        logged_in: 当前测试/接口参数，含义见该函数的具体场景。

    Returns:
        无返回值；任一断言失败会使测试失败。
    """
    assert logged_in.post('/api/auth/password',json={'current_password':'Testing-only-password-123','new_password':'New-test-password-456'}).status_code==200
    assert logged_in.get('/api/auth/me').status_code==401

def test_asset_priority_identity_and_truthful_capabilities(logged_in):
    """验证四标的优先级、底层映射和真实阶段能力边界。

    Args:
        logged_in: 当前测试/接口参数，含义见该函数的具体场景。

    Returns:
        无返回值；任一断言失败会使测试失败。
    """
    assets=logged_in.get('/api/assets').json()
    assert [a['symbol'] for a in assets[:4]]==['KORUUSDT','CLUSDT','NVDAUSDT','BTCUSDT']
    assert all(not a['live_tradable'] for a in assets)
    assert next(a for a in assets if a['id']=='us:NVDA')['underlying_id']==next(a for a in assets if a['symbol']=='NVDAUSDT')['underlying_id']
    caps=logged_in.get('/api/capabilities').json()
    statuses={c['id']:c['status'] for c in caps}
    assert statuses['platform']=='IMPLEMENTED'
    assert statuses['technical']=='PARTIAL'
    assert statuses['backtests']=='PARTIAL'
    assert all(status=='PLANNED' for key,status in statuses.items() if key not in ('platform','technical','backtests'))
    assert logged_in.post('/api/execution/orders',json={'side':'BUY'}).status_code==403

def test_settings_validation_persistence_and_csrf(logged_in):
    """验证显示设置校验、持久化及CSRF保护。

    Args:
        logged_in: 当前测试/接口参数，含义见该函数的具体场景。

    Returns:
        无返回值；任一断言失败会使测试失败。
    """
    payload={'timezone':'UTC','default_interval':'1h','default_asset':AID}
    assert logged_in.put('/api/settings/display',json=payload).status_code==200
    assert logged_in.get('/api/settings').json()['display']==payload
    assert logged_in.put('/api/settings/display',json={**payload,'timezone':'invalid'}).status_code==422
    assert logged_in.put('/api/settings/display',json={**payload,'default_asset':'NONEXISTENT'}).status_code==400

@pytest.mark.parametrize('case',['ohlc','conflict','nan','wrong_interval'])
def test_reject_invalid_or_conflicting_data(case):
    """验证非法OHLC、冲突重复和时间不匹配数据被拒绝。

    Args:
        case: 当前测试/接口参数，含义见该函数的具体场景。

    Returns:
        无返回值；任一断言失败会使测试失败。
    """
    dataset=rows(2)
    if case=='ohlc':dataset[0]['high']='1'
    if case=='conflict':dataset.append({**dataset[0],'close':'102'})
    if case=='nan':dataset[0]['open']='NaN'
    if case=='wrong_interval':dataset[0]['close_time']+=100
    with pytest.raises(ValueError):normalize(dataset,START+3600000)

def test_no_future_candle_and_no_upsampling():
    """验证未收盘K线不入库且不能由大周期生成小周期。

    Returns:
        无返回值；任一断言失败会使测试失败。
    """
    clean=normalize(rows(8),START+4*900000)
    assert len(clean)==4
    with pytest.raises(ValueError):aggregate(clean,'15m','5m')

def test_aggregate_only_complete_causal_buckets():
    """验证聚合仅使用完整且可知的真实K线桶。

    Returns:
        无返回值；任一断言失败会使测试失败。
    """
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
    """验证Decimal Parquet、指纹、幂等写入及图表API。

    Args:
        logged_in: 当前测试/接口参数，含义见该函数的具体场景。

    Returns:
        无返回值；任一断言失败会使测试失败。
    """
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
    """验证文件损坏后SHA256校验中止读取。

    Returns:
        无返回值；任一断言失败会使测试失败。
    """
    from app.core.config import DATA_ROOT
    with SessionLocal() as db:
        save_dataset(db,AID,'15m',rows(4),{'provider':'test'},START+5*900000)
        item=db.scalar(select(MarketData));(DATA_ROOT/item.path).write_bytes(b'corrupt')
        with pytest.raises(ValueError,match='校验失败'):read_dataset(item)

def test_public_adapter_451_is_not_fabricated(monkeypatch):
    """验证上游451明确失败而不替造行情。

    Args:
        monkeypatch: 当前测试/接口参数，含义见该函数的具体场景。

    Returns:
        无返回值；任一断言失败会使测试失败。
    """
    import httpx
    def fake_get(self,*args,**kwargs):
        """返回受控HTTP451响应以验证公开适配器失败行为。

        Args:
            *args: 透传的额外位置参数，按当前接口契约解释。
            **kwargs: 透传的命名参数；不能绕过当前调用的校验。

        Returns:
            测试夹具、构造结果或断言辅助返回值；不影响真实数据。
        """
        return httpx.Response(451,request=httpx.Request('GET','https://fapi.binance.com'))
    monkeypatch.setattr(httpx.Client,'get',fake_get)
    with pytest.raises(PublicDataError,match='HTTP 451'):BinancePublic().exchange_info()

def test_verify_assets_retains_verified_snapshot_when_public_dns_fails(monkeypatch):
    """保留已核验快照，且把在线解析失败报告为降级结果而非伪造实时核验。

    Args:
        monkeypatch: 临时替换公开exchangeInfo调用，禁止外部网络请求。

    Returns:
        无返回值；验证本机快照状态不被DNS故障覆盖。
    """
    from app.core.db import SessionLocal
    from app.domains.models import Provider
    from app.services.ingestion import verify_assets

    def failed_exchange_info(self):
        """模拟公开域名不可解析，不访问网络或改写行情数据。

        Args:
            self: 当前公开Binance适配器实例。

        Returns:
            不返回行情；抛出可识别的公开连接错误。
        """
        raise PublicDataError('Binance 公开连接失败：DNS 解析失败')

    monkeypatch.setattr(BinancePublic, 'exchange_info', failed_exchange_info)
    with SessionLocal() as db:
        result = verify_assets(db)
        assert result['verification_mode'] == 'VERIFIED_LOCAL_SNAPSHOT'
        assert result['live_refresh'] is False
        assert result['verified_assets'] > 0
        assert db.get(Provider, 'binance_public').status == 'UNAVAILABLE'
        assert db.get(Asset, AID).verification in ('VERIFIED_SNAPSHOT', 'VERIFIED')

def test_task_dedup_and_real_redis_queue(logged_in):
    """验证真实Redis入队与相同进行中任务去重。

    Args:
        logged_in: 当前测试/接口参数，含义见该函数的具体场景。

    Returns:
        无返回值；任一断言失败会使测试失败。
    """
    one=logged_in.post('/api/tasks',json={'kind':'VERIFY_ASSETS'});two=logged_in.post('/api/tasks',json={'kind':'VERIFY_ASSETS'})
    assert one.status_code==202 and one.json()['id']==two.json()['id']
    from app.services.jobs import queue
    assert queue().fetch_job(one.json()['id']) is not None
    assert logged_in.post('/api/tasks',json={'kind':'FETCH_HISTORY','asset_id':'FAKEUSDT'}).status_code==400
    assert logged_in.post('/api/tasks',json={'kind':'FETCH_HISTORY','asset_id':AID,'days':100}).status_code==422

def test_failed_queue_job_can_be_recovered_in_ui(logged_in):
    """验证终止队列任务在页面可识别并重新入队。

    Args:
        logged_in: 当前测试/接口参数，含义见该函数的具体场景。

    Returns:
        无返回值；任一断言失败会使测试失败。
    """
    from rq.job import JobStatus
    from app.services.jobs import queue
    created=logged_in.post('/api/tasks',json={'kind':'VERIFY_ASSETS'}).json()
    queue().fetch_job(created['id']).set_status(JobStatus.FAILED)
    tasks=logged_in.get('/api/tasks').json()
    assert tasks[0]['status']=='FAILED'
    retried=logged_in.post('/api/tasks',json={'kind':'VERIFY_ASSETS'}).json()
    assert retried['id']!=created['id']

def test_fetch_history_resumes_from_checkpoint_and_is_idempotent(monkeypatch):
    """验证采集从持久分页断点恢复且不重复追加。

    Args:
        monkeypatch: 当前测试/接口参数，含义见该函数的具体场景。

    Returns:
        无返回值；任一断言失败会使测试失败。
    """
    from app.services.ingestion import fetch_history
    from app.services import ingestion as jobs
    end=START+33*900000
    monkeypatch.setattr(jobs.time,'time',lambda:end/1000)
    raw=rows(32)
    first=raw[:16]
    with SessionLocal() as db:
        save_dataset(db,AID,'15m',first,{'provider':'test'},end)
    observed=[]
    def batch(self,symbol,interval,start,finish):
        """按起点分页返回确定性原始K线，供断点与幂等测试使用。

        Args:
            symbol: 精确产品代码；股票、ETF与相关永续不能混用。
            interval: 精确K线周期名称，月线1M区别于分钟。
            start: 查询/分页起点的UTC毫秒时间。
            finish: 当前测试/接口参数，含义见该函数的具体场景。

        Returns:
            测试夹具、构造结果或断言辅助返回值；不影响真实数据。
        """
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


def test_binance_archive_batch_saves_progress_and_resumes_after_failure(monkeypatch):
    """验证批量三年归档逐合约持久进度，并在重试时跳过已完成合约。

    Args:
        monkeypatch: pytest提供的依赖替换器，用于注入一次性公开接口故障。

    Returns:
        无返回值；任务若丢失检查点、重复采集已完成合约或错误标成功则失败。
    """
    from app.services import jobs
    task_id='batch-resume-test-0000000000000001'
    asset_ids=['binance_usdm:BTCUSDT','binance_usdm:KORUUSDT']
    with SessionLocal() as db:
        for asset_id in asset_ids:
            db.get(Asset,asset_id).verification='VERIFIED'
        db.add(Task(id=task_id,kind='FETCH_BINANCE_ARCHIVES',parameters={'asset_ids':asset_ids,'interval':'15m'},status='QUEUED'))
        db.commit()
    calls=[]
    injected={'pending':True}

    def fake_fetch(db, asset_id, interval):
        """记录模拟合约下载并在第二合约首次请求时中断。

        Args:
            db: 批量任务持有的隔离测试数据库会话。
            asset_id: 当前精确测试USDM资产ID。
            interval: 当前公开K线周期。

        Returns:
            可序列化的最小归档摘要；指定的首次失败返回异常。
        """
        calls.append(asset_id)
        if asset_id.endswith('KORUUSDT') and injected['pending']:
            injected['pending']=False
            raise RuntimeError('temporary public API outage')
        return {'asset_id':asset_id,'interval':interval,'rows':12,'first_open_ms':1,
                'last_close_ms':2,'status':'THREE_YEARS','dataset_sha256':'a'*64}

    monkeypatch.setattr(jobs,'fetch_three_year_archive',fake_fetch)
    with pytest.raises(RuntimeError,match='temporary public API outage'):
        jobs.execute(task_id)
    with SessionLocal() as db:
        task=db.get(Task,task_id)
        assert task.status=='RETRYING'
        assert task.result['progress']['completed_asset_ids']==[asset_ids[0]]
        assert task.result['progress']['current_symbol']=='KORUUSDT'
    result=jobs.execute(task_id)
    assert calls==[asset_ids[0],asset_ids[1],asset_ids[1]]
    assert result['progress']['phase']=='COMPLETE'
    assert result['completed_count']==2
    with SessionLocal() as db:
        assert db.get(Task,task_id).status=='SUCCEEDED'

def test_public_adapter_reports_dns_failure(monkeypatch):
    """验证DNS解析错误以明确脱敏原因报告。

    Args:
        monkeypatch: 当前测试/接口参数，含义见该函数的具体场景。

    Returns:
        无返回值；任一断言失败会使测试失败。
    """
    import httpx
    def fail(self,*args,**kwargs):
        """注入受控DNS解析异常以验证脱敏错误分类。

        Args:
            *args: 透传的额外位置参数，按当前接口契约解释。
            **kwargs: 透传的命名参数；不能绕过当前调用的校验。

        Returns:
            测试夹具、构造结果或断言辅助返回值；不影响真实数据。
        """
        raise httpx.ConnectError('[Errno -2] Name or service not known')
    monkeypatch.setattr(httpx.Client,'get',fail)
    with pytest.raises(PublicDataError,match='DNS 解析失败'):BinancePublic().exchange_info()

def test_fresh_clone_uses_public_history_jobs_without_private_data(monkeypatch,tmp_path):
    """验证新克隆无私人目录时选择真实公开采集路径。

    Args:
        monkeypatch: 当前测试/接口参数，含义见该函数的具体场景。
        tmp_path: 当前测试/接口参数，含义见该函数的具体场景。

    Returns:
        无返回值；任一断言失败会使测试失败。
    """
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
