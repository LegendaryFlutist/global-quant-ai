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


router=APIRouter()

class TaskRequest(BaseModel):
    """排队执行公开行情导入、验证或本地归档的受限请求。"""
    model_config = {"extra": "forbid"}
    kind:Literal['IMPORT_LOCAL','VERIFY_ASSETS','FETCH_HISTORY','FETCH_ARCHIVE','FETCH_DERIVATIVE_DATA']
    asset_id:str|None=None
    interval:Literal['1m','3m','5m','15m','30m','1h','2h','4h','6h','12h','1d','1w','1M']='15m'
    days:int=Field(default=90,ge=1,le=90)
    start_ms:int|None=Field(default=None,ge=0)
    end_ms:int|None=Field(default=None,ge=0)

    @model_validator(mode='after')
    def validate_derivative_window(self):
        """要求衍生品采集显式给出闭合的UTC毫秒时间窗。"""
        if self.kind == 'FETCH_DERIVATIVE_DATA':
            if self.start_ms is None or self.end_ms is None or self.end_ms <= self.start_ms:
                raise ValueError('衍生品归档任务需要有效的start_ms和end_ms')
        elif self.start_ms is not None or self.end_ms is not None:
            raise ValueError('start_ms/end_ms只用于衍生品历史归档')
        return self

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
    if payload.kind=='FETCH_DERIVATIVE_DATA':
        now_ms=int(time.time()*1000)
        if payload.start_ms<three_year_start(now_ms) or payload.end_ms>now_ms:
            raise HTTPException(422,'衍生品归档只允许最近三年内已发生的时间范围')
    # Deduplicate active work for the same operation/asset/interval.
    params={} if payload.kind=='VERIFY_ASSETS' else {'asset_id':payload.asset_id}
    if payload.kind in ('FETCH_HISTORY','FETCH_ARCHIVE','FETCH_DERIVATIVE_DATA'):params.update(interval=payload.interval)
    if payload.kind=='FETCH_HISTORY':params.update(days=payload.days)
    if payload.kind=='FETCH_DERIVATIVE_DATA':params.update(start_ms=payload.start_ms,end_ms=payload.end_ms)
    for task in db.scalars(select(Task).where(Task.status.in_(['QUEUED','RUNNING','RETRYING']))):
        if task.kind==payload.kind and task.parameters==params:return {'id':task.id,'status':task.status,'deduplicated':True}
    task=enqueue(db,payload.kind,params)
    db.add(Audit(actor=user.username,action='TASK_CREATED',detail={'task_id':task.id,'kind':task.kind}));db.commit()
    return {'id':task.id,'status':task.status}

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
    return [{'id':t.id,'kind':t.kind,'status':t.status,'parameters':t.parameters,'result':t.result,'attempts':t.attempts,'created_at':t.created_at,'updated_at':t.updated_at} for t in db.scalars(select(Task).order_by(Task.created_at.desc()).limit(100))]
