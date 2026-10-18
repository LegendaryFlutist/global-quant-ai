import csv
import json
import time
import uuid
from datetime import datetime, timezone
from redis import Redis
from rq import Queue, Retry
from sqlalchemy import select
from app.core.config import REDIS_URL, RESEARCH_ROOT
from app.core.db import SessionLocal
from app.domains.models import Task, Asset, Provider, MarketData, utcnow
from app.adapters.public_binance import BinancePublic
from app.services.market_data import save_dataset, read_dataset, FIELDS

def queue():
    return Queue('market-data', connection=Redis.from_url(REDIS_URL), default_timeout=900)

def enqueue(db,kind: str,parameters: dict, task_id: str | None = None):
    task_id=task_id or str(uuid.uuid4())
    task=Task(id=task_id,kind=kind,parameters=parameters,status='QUEUED')
    db.add(task); db.commit()
    try:
        queue().enqueue(execute,task_id,job_id=task_id,retry=Retry(max=2,interval=[5,30]),job_timeout=900,result_ttl=86400)
    except Exception:
        task.status='QUEUE_ERROR'; task.result={'error':'Redis 队列暂不可用，请重试'};db.commit()
    return task

def execute(task_id: str):
    with SessionLocal() as db:
        task=db.get(Task,task_id)
        if task is None: raise ValueError('任务不存在')
        if task.status=='SUCCEEDED': return task.result
        task.status='RUNNING';task.attempts+=1;task.updated_at=utcnow();db.commit()
        try:
            if task.kind=='IMPORT_LOCAL': result=import_local(db,task.parameters['asset_id'])
            elif task.kind=='VERIFY_ASSETS': result=verify_assets(db)
            elif task.kind=='FETCH_HISTORY': result=fetch_history(db,task.parameters)
            else: raise ValueError('未知任务类型')
            task.status='SUCCEEDED';task.result=result;task.updated_at=utcnow();db.commit()
            return result
        except Exception as exc:
            db.rollback();task=db.get(Task,task_id)
            task.status='RETRYING' if task.attempts<3 else 'FAILED'
            task.result={'error':str(exc)[:300]};task.updated_at=utcnow();db.commit()
            raise

def import_local(db,asset_id: str):
    asset=db.get(Asset,asset_id)
    manifest=json.loads((RESEARCH_ROOT/'data/priority_analysis_latest.json').read_text())
    relative=manifest['directories'].get(asset.symbol)
    if not relative: raise ValueError('该资产没有可导入的已验证本地行情')
    directory=(RESEARCH_ROOT/relative).resolve()
    if not directory.is_relative_to((RESEARCH_ROOT/'data').resolve()): raise ValueError('非法数据路径')
    with (directory/'bars.csv').open() as f: rows=list(csv.DictReader(f))
    provenance=json.loads((directory/'source.json').read_text())
    result=save_dataset(db,asset_id,'15m',rows,{'provider':'binance_connected_public','mode':'historical_snapshot','source_file':str(directory/'bars.csv'),'provenance':provenance})
    provider=db.get(Provider,'research_snapshot');provider.status='CONNECTED';provider.detail={'last_success_at':utcnow().isoformat(),'mode':'historical_snapshot','last_asset':asset.symbol};db.commit()
    return result

def verify_assets(db):
    provider=db.get(Provider,'binance_public')
    try:
        info=BinancePublic().exchange_info()
    except Exception as exc:
        provider.status='UNAVAILABLE';provider.detail={**provider.detail,'last_attempt_at':utcnow().isoformat(),'error':str(exc)};db.commit()
        raise
    indexed={s['symbol']:s for s in info['symbols']}
    verified=0
    for asset in db.scalars(select(Asset).where(Asset.venue=='BINANCE_USDM')):
        symbol=indexed.get(asset.symbol)
        asset.verification='VERIFIED' if symbol and symbol.get('status')=='TRADING' else 'UNAVAILABLE'
        asset.specification=symbol or {};asset.verified_at=utcnow();verified+=int(asset.verification=='VERIFIED')
    provider.status='CONNECTED';provider.detail={'last_success_at':utcnow().isoformat(),'server_time':info.get('serverTime'),'verified_assets':verified};db.commit()
    return {'verified_assets':verified}

def fetch_history(db,params):
    asset=db.get(Asset,params['asset_id'])
    if not asset or asset.venue!='BINANCE_USDM' or asset.verification not in ('VERIFIED','VERIFIED_SNAPSHOT'):
        raise ValueError('资产未经 Binance 精确代码核验，禁止请求推定合约')
    interval=params['interval'];now=int(time.time()*1000);cursor=now-params.get('days',90)*86400000
    existing=db.scalar(select(MarketData).where(MarketData.asset_id==asset.id,MarketData.interval==interval))
    rows=read_dataset(existing) if existing else []
    if rows:cursor=max(cursor,rows[-1]['open_time'])
    adapter=BinancePublic()
    # Each page is checkpointed. A restarted/retried job resumes from the last persisted candle.
    pages=0
    while cursor<now:
        batch=adapter.klines(asset.symbol,interval,cursor,now)
        if not batch:break
        parsed=[dict(zip(('open_time','open','high','low','close','volume','close_time'),r[:7])) for r in batch]
        closed=[r for r in parsed if int(r['close_time'])<now]
        merged={r['open_time']:r for r in rows}
        merged.update({int(r['open_time']):r for r in closed});rows=list(merged.values())
        if closed:save_dataset(db,asset.id,interval,rows,{'provider':'binance_public_rest','mode':'public_history','retrieved_at':utcnow().isoformat()},now)
        following=int(batch[-1][6])+1
        if following<=cursor:raise ValueError('分页游标未前进，已停止')
        cursor=following;pages+=1
        if len(batch)<1000:break
        if pages>140:raise ValueError('分页达到上限，保留已下载进度')
        time.sleep(.25)
    if not rows:raise ValueError('公开接口没有返回已收盘K线，未生成数据')
    return {'asset_id':asset.id,'interval':interval,'rows':len(rows),'pages':pages}


def reconcile_task_states(db):
    """Reflect RQ terminal failures, including worker termination, in durable UI status."""
    try:
        q=queue()
        for task in db.scalars(select(Task).where(Task.status.in_(['QUEUED','RUNNING','RETRYING']))):
            job=q.fetch_job(task.id)
            if job is not None:
                status=job.get_status(refresh=True)
                value=getattr(status,'value',status)
                if value in ('failed','stopped','canceled'):
                    task.status='FAILED';task.result={'error':'队列任务已终止或耗尽重试，请手动重试；已下载分页保留'};task.updated_at=utcnow()
            elif (utcnow()-task.updated_at.replace(tzinfo=timezone.utc)).total_seconds()>960:
                task.status='FAILED';task.result={'error':'任务队列记录缺失，可重新入队；已有行情保留'};task.updated_at=utcnow()
        db.commit()
    except Exception:
        db.rollback()  # DB remains the source of the last known state during Redis outage.
