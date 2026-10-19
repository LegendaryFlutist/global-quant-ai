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
    """汇总资产、真实K线覆盖、任务数量与四个优先标的。

    Args:
        user: 认证依赖已经校验的当前用户，不从请求正文接受替代身份。
        db: 本次调用的SQLAlchemy数据库会话。

    Returns:
        工作台总览；未实现的全球风险保持UNKNOWN。
    """
    datasets=list(db.scalars(select(MarketData)))
    return {'asset_count':db.scalar(select(func.count()).select_from(Asset)),'datasets':len(datasets),'candle_count':sum(m.row_count for m in datasets),'successful_tasks':db.scalar(select(func.count()).select_from(Task).where(Task.status=='SUCCEEDED')),'market_regime':'UNKNOWN','regime_reason':'全球情绪与风险传导将在 Phase 3 实现','execution_mode':'RESEARCH_ONLY','live_enabled':False,'as_of_ms':max((m.last_close_ms for m in datasets),default=None),'priority_assets':[asset_dict(db,a) for a in db.scalars(select(Asset).where(Asset.priority<4).order_by(Asset.priority))]}

@router.get('/api/audit')
def audit(user:User=Depends(current_user),db:DBSession=Depends(get_db)):
    """读取最近100条不含凭据的安全审计记录。

    Args:
        user: 认证依赖已经校验的当前用户，不从请求正文接受替代身份。
        db: 本次调用的SQLAlchemy数据库会话。

    Returns:
        按时间倒序排列的审计列表。
    """
    return [{'actor':a.actor,'action':a.action,'detail':a.detail,'created_at':a.created_at} for a in db.scalars(select(Audit).order_by(Audit.id.desc()).limit(100))]

@router.get('/api/capabilities')
def capabilities(user:User=Depends(current_user)):
    """明确列出已实现与计划中的各阶段能力。

    Args:
        user: 认证依赖已经校验的当前用户，不从请求正文接受替代身份。

    Returns:
        阶段能力列表，不能将离线研究误标为Web训练/实盘完成。
    """
    definitions=[
        ('platform','基础 Web 平台',1,'IMPLEMENTED','登录、资产注册、历史行情、多周期图表、队列和设置'),
        ('technical','多周期技术结构与道氏 / 波浪',2,'PARTIAL','已实现三年按需归档、多周期趋势/破位、Dow 1-2-3、波浪候选、RSI背离、5秒选中周期刷新和优先资产结构周期摘要；浏览器运行验收待完成'),
        ('models','宏观情绪与模型中心',3,'PLANNED','结构方向摘要已提供；新闻/宏观与跨市场传导、Web模型训练及版本注册待开发'),
        ('backtests','组合策略与回测',4,'PARTIAL','模拟账号回测及整套离线多模型任务/报告已接入系统页面；模型注册、滚动样本外实验和账号间比较仍待开发'),
        ('paper','独立模拟账户',5,'PLANNED','持久账本与账户排名未实现'),
        ('notifications','通知与 Binance 只读账户',6,'PLANNED','Web凭据配置和邮件任务未实现'),
        ('approvals','逐笔审批交易',7,'PLANNED','2FA、独立风控、对账和实盘执行均未实现'),
        ('ideas','研究思想与持续实验',8,'PLANNED','思想注册与验证工作流未实现'),
    ]
    return [{'id':key,'name':name,'phase':phase,'status':status,'detail':detail} for key,name,phase,status,detail in definitions]

@router.post('/api/execution/orders')
def no_execution(user:User=Depends(current_user)):
    """在后端拒绝所有订单执行请求。

    Args:
        user: 认证依赖已经校验的当前用户，不从请求正文接受替代身份。

    Returns:
        不返回成功结果，始终抛出HTTP403。
    """
    raise HTTPException(403,'Phase 1 服务端不包含交易执行能力，不能通过配置启用实盘')
