"""持久任务入队、重试和状态对账；行情业务委派给ingestion领域服务。"""
import uuid
from datetime import timezone
from redis import Redis
from rq import Queue, Retry
from sqlalchemy import select
from app.core.config import REDIS_URL
from app.core.db import SessionLocal
from app.domains.models import Asset, Task, utcnow

from app.services.ingestion import import_local, verify_assets, fetch_history
from app.services.long_history import fetch_three_year_archive, validate_archive_identity
from app.services.derivative_history import fetch_derivative_history
from app.services.simulation_backtest import run_simulation_backtest

def queue():
    """构造具有统一超时的market-data持久RQ队列连接。

    Returns:
        Queue对象；不在构造时执行具体采集。
    """
    return Queue('market-data', connection=Redis.from_url(REDIS_URL), default_timeout=900)

def enqueue(db,kind: str,parameters: dict, task_id: str | None = None):
    """先持久化任务，再用同一ID发布RQ并配置有限重试。

    Args:
        db: 本次调用的SQLAlchemy数据库会话。
        kind: 任务类型白名单中的名称。
        parameters: 经过命令/配置层组织的当前任务或执行参数字典。
        task_id: 稳定任务ID，对应数据库记录与RQ job_id。

    Returns:
        Task对象；Redis失败记录QUEUE_ERROR，便于之后补发。
    """
    task_id=task_id or str(uuid.uuid4())
    task=Task(id=task_id,kind=kind,parameters=parameters,status='QUEUED')
    db.add(task); db.commit()
    try:
        timeout=21600 if kind=='FETCH_BINANCE_ARCHIVES' else 900
        queue().enqueue(execute,task_id,job_id=task_id,retry=Retry(max=2,interval=[5,30]),job_timeout=timeout,result_ttl=86400)
    except Exception:
        task.status='QUEUE_ERROR'; task.result={'error':'Redis 队列暂不可用，请重试'};db.commit()
    return task

def execute(task_id: str):
    """按任务类型调度行情服务并持久记录运行、成功或重试状态。

    Args:
        task_id: 稳定任务ID，对应数据库记录与RQ job_id。

    Returns:
        执行结果；已成功任务直接返回原结果，异常继续交给RQ重试。
    """
    with SessionLocal() as db:
        task=db.get(Task,task_id)
        if task is None: raise ValueError('任务不存在')
        if task.status=='SUCCEEDED': return task.result
        task.status='RUNNING';task.attempts+=1;task.updated_at=utcnow();db.commit()
        try:
            if task.kind=='IMPORT_LOCAL': result=import_local(db,task.parameters['asset_id'])
            elif task.kind=='VERIFY_ASSETS': result=verify_assets(db)
            elif task.kind=='FETCH_HISTORY': result=fetch_history(db,task.parameters)
            elif task.kind=='FETCH_ARCHIVE': result=fetch_three_year_archive(db,task.parameters['asset_id'],task.parameters['interval'])
            elif task.kind=='FETCH_BINANCE_ARCHIVES':
                asset_ids=task.parameters.get('asset_ids')
                interval=task.parameters.get('interval')
                if not isinstance(asset_ids,list) or not asset_ids or interval!='15m':
                    raise ValueError('批量归档参数无效')
                saved_result=task.result if isinstance(task.result,dict) else {}
                progress=saved_result.get('progress',{})
                asset_results=dict(saved_result.get('assets',{}))
                completed_ids=set(progress.get('completed_asset_ids',asset_results.keys()))
                completed_symbols=list(progress.get('completed_symbols',[]))
                for asset_id in asset_ids:
                    if asset_id in completed_ids:
                        continue
                    asset=db.get(Asset,asset_id)
                    if asset is None or asset.venue!='BINANCE_USDM':
                        raise ValueError('批量归档任务包含非Binance永续资产')
                    validate_archive_identity(db,asset_id,interval)
                    progress={'interval':interval,'total_assets':len(asset_ids),'completed_count':len(completed_ids),
                              'completed_asset_ids':[item for item in asset_ids if item in completed_ids],'completed_symbols':completed_symbols,
                              'current_symbol':asset.symbol,'phase':'FETCHING'}
                    task.result={'progress':progress,'assets':asset_results};task.updated_at=utcnow();db.commit()
                    summary=fetch_three_year_archive(db,asset_id,interval)
                    asset_results[asset_id]={key:summary[key] for key in ('asset_id','interval','rows','first_open_ms','last_close_ms','status','dataset_sha256')}
                    completed_ids.add(asset_id);completed_symbols.append(asset.symbol)
                    progress={'interval':interval,'total_assets':len(asset_ids),'completed_count':len(completed_ids),
                              'completed_asset_ids':[item for item in asset_ids if item in completed_ids],'completed_symbols':completed_symbols,
                              'current_symbol':None,'phase':'FETCHING'}
                    task.result={'progress':progress,'assets':asset_results};task.updated_at=utcnow();db.commit()
                result={'interval':interval,'total_assets':len(asset_ids),'completed_count':len(completed_ids),
                        'completed_symbols':completed_symbols,'progress':{**progress,'current_symbol':None,'phase':'COMPLETE'},
                        'assets':asset_results,'research_only':True}
            elif task.kind=='FETCH_DERIVATIVE_DATA': result=fetch_derivative_history(db,task.parameters['asset_id'],task.parameters['interval'],task.parameters['start_ms'],task.parameters['end_ms'])
            elif task.kind=='RUN_SIMULATION_BACKTEST': result=run_simulation_backtest(db,task.id,task.parameters)
            else: raise ValueError('未知任务类型')
            task.status='SUCCEEDED';task.result=result;task.updated_at=utcnow();db.commit()
            return result
        except Exception as exc:
            db.rollback();task=db.get(Task,task_id)
            task.status='RETRYING' if task.attempts<3 else 'FAILED'
            task.result={**(task.result if isinstance(task.result,dict) else {}),'error':str(exc)[:300]};task.updated_at=utcnow();db.commit()
            raise


def reconcile_task_states(db):
    """把RQ终止/丢失记录反映到数据库任务状态供页面恢复。

    Args:
        db: 本次调用的SQLAlchemy数据库会话。

    Returns:
        无返回值；Redis异常时回滚，保留最近已知状态。

    Notes:
        原有细则保留如下：
        Reflect RQ terminal failures, including worker termination, in durable UI status.
    """
    try:
        q=queue()
        for task in db.scalars(select(Task).where(Task.status.in_(['QUEUED','RUNNING','RETRYING']))):
            if task.kind == 'RUN_FULL_RESEARCH':
                continue  # The NVIDIA runner owns this task lifecycle, not the RQ queue.
            job=q.fetch_job(task.id)
            if job is not None:
                status=job.get_status(refresh=True)
                value=getattr(status,'value',status)
                if value in ('failed','stopped','canceled'):
                    task.status='FAILED';task.result={**(task.result if isinstance(task.result,dict) else {}),'error':'队列任务已终止或耗尽重试，请手动重试；已下载分页保留'};task.updated_at=utcnow()
            elif (utcnow()-task.updated_at.replace(tzinfo=timezone.utc)).total_seconds()>960:
                task.status='FAILED';task.result={**(task.result if isinstance(task.result,dict) else {}),'error':'任务队列记录缺失，可重新入队；已有行情保留'};task.updated_at=utcnow()
        db.commit()
    except Exception:
        db.rollback()  # DB remains the source of the last known state during Redis outage.
