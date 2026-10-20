"""Phase 1 jobs API."""
import time
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession
from app.core.db import get_db
from app.core.security import current_user
from app.domains.models import User, Audit, Asset, Task
from app.services.jobs import enqueue, reconcile_task_states
from app.services.long_history import three_year_start, validate_archive_identity
from app.services.underlying_market import validate_underlying_request


router=APIRouter()

class TaskRequest(BaseModel):
    """排队执行公开行情导入、验证或本地归档的受限请求。"""
    model_config = {"extra": "forbid"}
    kind:Literal['IMPORT_LOCAL','VERIFY_ASSETS','FETCH_HISTORY','FETCH_ARCHIVE','FETCH_DERIVATIVE_DATA','FETCH_UNDERLYING_HISTORY']
    asset_id:str|None=None
    interval:Literal['1m','3m','5m','15m','30m','1h','2h','4h','6h','12h','1d','1w','1M']='15m'
    days:int|None=Field(default=None,ge=1,le=3650)
    start_ms:int|None=Field(default=None,ge=0)
    end_ms:int|None=Field(default=None,ge=0)

    @model_validator(mode='after')
    def validate_derivative_window(self):
        """Normalize history defaults and constrain derivative timestamps.

        Returns:
            The validated request; Binance short-history remains at most 90 days.
        """
        if self.kind == 'FETCH_DERIVATIVE_DATA':
            if self.start_ms is None or self.end_ms is None or self.end_ms <= self.start_ms:
                raise ValueError('衍生品归档任务需要有效的start_ms和end_ms')
        elif self.start_ms is not None or self.end_ms is not None:
            raise ValueError('start_ms/end_ms只用于衍生品历史归档')
        if self.kind == 'FETCH_HISTORY':
            self.days = self.days or 90
            if self.days > 90:
                raise ValueError('Binance短期行情采集最多90天；三年档案使用专用任务')
        elif self.kind == 'FETCH_UNDERLYING_HISTORY':
            self.days = self.days or 1095
        elif self.days is not None and self.kind not in {'FETCH_HISTORY', 'FETCH_UNDERLYING_HISTORY'}:
            raise ValueError('days只用于历史K线采集')
        return self

class BinanceArchiveBatchRequest(BaseModel):
    """Validated optional retry scope for the registered Binance USDM archive batch."""
    model_config = {"extra": "forbid"}
    asset_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)
    interval: Literal['15m'] = '15m'

@router.post('/api/tasks',status_code=202)
def create_task(payload:TaskRequest,user:User=Depends(current_user),db:DBSession=Depends(get_db)):
    """验证任务参数并对相同进行中任务去重后入队。

    Args:
        payload: 通过API schema校验的请求数据或报告绘图数据。
        user: 认证依赖已经校验的当前用户，不从请求正文接受替代身份。
        db: 本次调用的SQLAlchemy数据库会话。

    Returns:
        任务ID、状态和可选去重标记；不直接执行网络采集。
    """
    asset=db.get(Asset,payload.asset_id) if payload.asset_id else None
    if payload.kind!='VERIFY_ASSETS' and (not payload.asset_id or not asset):raise HTTPException(400,'请选择已登记资产')
    if payload.kind in ('FETCH_ARCHIVE','FETCH_DERIVATIVE_DATA'):
        try: validate_archive_identity(db,payload.asset_id,payload.interval)
        except ValueError as exc: raise HTTPException(422,str(exc)) from None
    if payload.kind=='FETCH_UNDERLYING_HISTORY':
        try: validate_underlying_request(asset,payload.interval)
        except ValueError as exc: raise HTTPException(409,str(exc)) from None
    if payload.kind=='FETCH_DERIVATIVE_DATA':
        now_ms=int(time.time()*1000)
        if payload.start_ms<three_year_start(now_ms) or payload.end_ms>now_ms:
            raise HTTPException(422,'衍生品归档只允许最近三年内已发生的时间范围')
    # Deduplicate active work for the same operation/asset/interval.
    params={} if payload.kind=='VERIFY_ASSETS' else {'asset_id':payload.asset_id}
    if payload.kind in ('FETCH_HISTORY','FETCH_ARCHIVE','FETCH_DERIVATIVE_DATA','FETCH_UNDERLYING_HISTORY'):params.update(interval=payload.interval)
    if payload.kind in ('FETCH_HISTORY','FETCH_UNDERLYING_HISTORY'):params.update(days=payload.days)
    if payload.kind=='FETCH_DERIVATIVE_DATA':params.update(start_ms=payload.start_ms,end_ms=payload.end_ms)
    for task in db.scalars(select(Task).where(Task.status.in_(['QUEUED','RUNNING','RETRYING']))):
        if task.kind==payload.kind and task.parameters==params:return {'id':task.id,'status':task.status,'deduplicated':True}
    task=enqueue(db,payload.kind,params)
    db.add(Audit(actor=user.username,action='TASK_CREATED',detail={'task_id':task.id,'kind':task.kind}));db.commit()
    return {'id':task.id,'status':task.status}

@router.post('/api/tasks/binance-archives',status_code=202)
def create_binance_archive_batch(payload:BinanceArchiveBatchRequest,user:User=Depends(current_user),db:DBSession=Depends(get_db)):
    """Queue a resumable three-year 15-minute archive for verified Binance USDM assets.

    Args:
        payload: Optional exact asset IDs for retry; an empty request selects the current verified registry.
        user: Authenticated session identity; no identity is accepted from request data.
        db: Current SQLAlchemy session used to validate and persist the task.

    Returns:
        Task ID, status, and exact public contract scope; no network request runs in the API process.

    Raises:
        HTTPException: The requested scope is empty, duplicated, or contains an unverified/non-USDM asset.
    """
    if payload.asset_ids is None:
        selected=list(db.scalars(select(Asset).where(
            Asset.venue=='BINANCE_USDM',
            Asset.verification.in_(['VERIFIED','VERIFIED_SNAPSHOT']),
        ).order_by(Asset.priority,Asset.symbol)))
    else:
        if len(set(payload.asset_ids)) != len(payload.asset_ids):
            raise HTTPException(422,'合约ID不能重复')
        selected=[]
        for asset_id in payload.asset_ids:
            asset=db.get(Asset,asset_id)
            if asset is None:
                raise HTTPException(422,'批量归档只允许已登记的Binance USDT永续合约')
            try:
                validate_archive_identity(db,asset.id,payload.interval)
            except ValueError as exc:
                raise HTTPException(422,str(exc)) from None
            selected.append(asset)
        selected.sort(key=lambda asset:(asset.priority,asset.symbol))
    if not selected:
        raise HTTPException(409,'当前没有已核验的Binance USDT永续合约')
    asset_ids=[asset.id for asset in selected]
    for asset_id in asset_ids:
        try:
            validate_archive_identity(db,asset_id,payload.interval)
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from None
    params={'asset_ids':asset_ids,'interval':payload.interval}
    for task in db.scalars(select(Task).where(Task.kind=='FETCH_BINANCE_ARCHIVES',Task.status.in_(['QUEUED','RUNNING','RETRYING']))):
        if task.parameters==params:
            return {'id':task.id,'status':task.status,'asset_count':len(asset_ids),'symbols':[asset.symbol for asset in selected],'deduplicated':True}
    task=enqueue(db,'FETCH_BINANCE_ARCHIVES',params)
    db.add(Audit(actor=user.username,action='TASK_CREATED',detail={'task_id':task.id,'kind':task.kind,'asset_count':len(asset_ids)}));db.commit()
    return {'id':task.id,'status':task.status,'asset_count':len(asset_ids),'symbols':[asset.symbol for asset in selected]}

@router.get('/api/tasks')
def tasks(user:User=Depends(current_user),db:DBSession=Depends(get_db)):
    """先对账队列终态，再返回最近100条持久任务记录。

    Args:
        user: 认证依赖已经校验的当前用户，不从请求正文接受替代身份。
        db: 本次调用的SQLAlchemy数据库会话。

    Returns:
        任务状态列表；不会把重试中或失败记录标为成功。
    """
    reconcile_task_states(db)
    rows = db.scalars(select(Task).order_by(Task.created_at.desc()).limit(100))
    visible = []
    for task in rows:
        if task.kind == 'RUN_FULL_RESEARCH' and task.parameters.get('owner_user_id') != user.id:
            continue
        parameters = {key: value for key, value in task.parameters.items()
                      if key not in {'owner_user_id', 'output_id'}}
        visible.append({'id':task.id,'kind':task.kind,'status':task.status,'parameters':parameters,
                        'result':task.result,'attempts':task.attempts,'created_at':task.created_at,
                        'updated_at':task.updated_at})
    return visible
