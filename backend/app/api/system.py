"""Phase 1 system API."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.orm import Session as DBSession
from app.core.db import get_db
from app.core.security import current_user
from app.domains.models import User, Audit, Asset, MarketData, Task

from app.api.assets import asset_dict

router=APIRouter()

@router.get('/api/dashboard')
def dashboard(user:User=Depends(current_user),db:DBSession=Depends(get_db)):
    datasets=list(db.scalars(select(MarketData)))
    return {'asset_count':db.scalar(select(func.count()).select_from(Asset)),'datasets':len(datasets),'candle_count':sum(m.row_count for m in datasets),'successful_tasks':db.scalar(select(func.count()).select_from(Task).where(Task.status=='SUCCEEDED')),'market_regime':'UNKNOWN','regime_reason':'全球情绪与风险传导将在 Phase 3 实现','execution_mode':'RESEARCH_ONLY','live_enabled':False,'as_of_ms':max((m.last_close_ms for m in datasets),default=None),'priority_assets':[asset_dict(db,a) for a in db.scalars(select(Asset).where(Asset.priority<4).order_by(Asset.priority))]}

@router.get('/api/audit')
def audit(user:User=Depends(current_user),db:DBSession=Depends(get_db)):
    return [{'actor':a.actor,'action':a.action,'detail':a.detail,'created_at':a.created_at} for a in db.scalars(select(Audit).order_by(Audit.id.desc()).limit(100))]

@router.get('/api/capabilities')
def capabilities(user:User=Depends(current_user)):
    return [{'id':key,'name':name,'phase':phase,'status':'IMPLEMENTED' if phase==1 else 'PLANNED','detail':detail} for key,name,phase,detail in [('platform','基础 Web 平台',1,'登录、资产注册、历史行情、多周期图表、队列和设置'),('technical','技术结构与道氏 / 波浪',2,'已有离线研究实现；Web 标注、回放与验证待开发'),('models','情绪与模型中心',3,'已有离线 GRU 等模型；Web 训练与版本注册待开发'),('backtests','组合策略与回测',4,'已有离线报告；Web 实验管理待开发'),('paper','独立模拟账户',5,'持久账本与账户排名未实现'),('notifications','通知与 Binance 只读账户',6,'Web 凭据配置和邮件任务未实现'),('approvals','逐笔审批交易',7,'2FA、独立风控、对账和实盘执行均未实现'),('ideas','研究思想与持续实验',8,'思想注册与验证工作流未实现')]]

@router.post('/api/execution/orders')
def no_execution(user:User=Depends(current_user)):
    raise HTTPException(403,'Phase 1 服务端不包含交易执行能力，不能通过配置启用实盘')
