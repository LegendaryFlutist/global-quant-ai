"""Phase 1 jobs API."""
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession
from app.core.db import get_db
from app.core.security import current_user
from app.domains.models import User, Audit, Asset, Task
from app.services.jobs import enqueue, reconcile_task_states


router=APIRouter()

class TaskRequest(BaseModel):
    kind:Literal['IMPORT_LOCAL','VERIFY_ASSETS','FETCH_HISTORY']
    asset_id:str|None=None
    interval:Literal['1m','3m','5m','15m','30m','1h','2h','4h','6h','12h','1d','1w','1M']='15m'
    days:int=Field(default=90,ge=1,le=90)

@router.post('/api/tasks',status_code=202)
def create_task(payload:TaskRequest,user:User=Depends(current_user),db:DBSession=Depends(get_db)):
    if payload.kind!='VERIFY_ASSETS' and (not payload.asset_id or not db.get(Asset,payload.asset_id)):raise HTTPException(400,'请选择已登记资产')
    # Deduplicate active work for the same operation/asset/interval.
    params={} if payload.kind=='VERIFY_ASSETS' else {'asset_id':payload.asset_id}
    if payload.kind=='FETCH_HISTORY':params.update(interval=payload.interval,days=payload.days)
    for task in db.scalars(select(Task).where(Task.status.in_(['QUEUED','RUNNING','RETRYING']))):
        if task.kind==payload.kind and task.parameters==params:return {'id':task.id,'status':task.status,'deduplicated':True}
    task=enqueue(db,payload.kind,params)
    db.add(Audit(actor=user.username,action='TASK_CREATED',detail={'task_id':task.id,'kind':task.kind}));db.commit()
    return {'id':task.id,'status':task.status}

@router.get('/api/tasks')
def tasks(user:User=Depends(current_user),db:DBSession=Depends(get_db)):
    reconcile_task_states(db)
    return [{'id':t.id,'kind':t.kind,'status':t.status,'parameters':t.parameters,'result':t.result,'attempts':t.attempts,'created_at':t.created_at,'updated_at':t.updated_at} for t in db.scalars(select(Task).order_by(Task.created_at.desc()).limit(100))]
