"""Phase 1 settings API."""
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DBSession
from app.core.db import get_db
from app.core.security import current_user
from app.domains.models import User, Audit, Asset, Setting


router=APIRouter()

class DisplaySettings(BaseModel):
    timezone:Literal['Asia/Shanghai','UTC']='Asia/Shanghai'
    default_interval:Literal['15m','30m','1h','2h','4h','6h','12h','1d','1w','1M']='4h'
    default_asset:str=Field(max_length=100)

@router.get('/api/settings')
def settings(user:User=Depends(current_user),db:DBSession=Depends(get_db)):
    return {'display':db.get(Setting,'display').value,'execution':{'mode':'RESEARCH_ONLY','live_enabled':False,'implemented':False},'notifications':{'mode':'SIMULATED_LEGACY_REPORTS','web_configuration_implemented':False},'credentials':{'accepted':False,'reason':'API 凭据管理在 Phase 6 实现，当前只连接公开行情'}}

@router.put('/api/settings/display')
def update_settings(payload:DisplaySettings,user:User=Depends(current_user),db:DBSession=Depends(get_db)):
    if db.get(Asset,payload.default_asset) is None:raise HTTPException(400,'资产不存在')
    db.get(Setting,'display').value=payload.model_dump();db.add(Audit(actor=user.username,action='SETTINGS_UPDATED',detail=payload.model_dump()));db.commit();return payload
