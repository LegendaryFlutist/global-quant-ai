"""Phase 1 settings API."""
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session as DBSession
from app.core.db import get_db
from app.core.security import current_user
from app.domains.models import User, Audit, Asset, Setting


router=APIRouter()

DEFAULT_CHART_INDICATORS = {
    'show_ma': True,
    'ma_periods': [5, 10, 20],
    'show_bollinger': False,
    'show_macd': False,
    'show_td': False,
    'show_trendlines': False,
    'show_dow123': False,
    'show_divergence': False,
    'show_wave': False,
}

class DisplaySettings(BaseModel):
    timezone:Literal['Asia/Shanghai','UTC']='Asia/Shanghai'
    default_interval:Literal['15m','30m','1h','2h','4h','6h','12h','1d','1w','1M']='4h'
    default_asset:str=Field(max_length=100)

class ChartIndicatorSettings(BaseModel):
    """Authenticated chart overlay preferences; no trading behavior is configured."""
    model_config = ConfigDict(extra='forbid')
    show_ma: bool = True
    ma_periods: list[int] = Field(default_factory=lambda: [5, 10, 20], min_length=1, max_length=8)
    show_bollinger: bool = False
    show_macd: bool = False
    show_td: bool = False
    show_trendlines: bool = False
    show_dow123: bool = False
    show_divergence: bool = False
    show_wave: bool = False

    @field_validator('ma_periods')
    @classmethod
    def validate_ma_periods(cls, periods: list[int]) -> list[int]:
        """Require unique MA lengths from 2 to 500 chart bars/daily closes.

        Args:
            periods: Ordered MA sample lengths, in chart bars for intraday data
                or UTC daily closes for the daily MA overlay.

        Returns:
            The supplied order-preserving lengths, after range and uniqueness checks.
        """
        if len(set(periods)) != len(periods) or any(period < 2 or period > 500 for period in periods):
            raise ValueError('均线周期必须是2到500之间且不能重复')
        return periods

@router.get('/api/settings')
def settings(user:User=Depends(current_user),db:DBSession=Depends(get_db)):
    """返回显示偏好及当前研究/通知/凭据功能边界。

    Args:
        user: 认证依赖已经校验的当前用户，不从请求正文接受替代身份。
        db: 本次调用的SQLAlchemy数据库会话。

    Returns:
        不含任何密码或密钥的设置字典。
    """
    indicators = db.get(Setting, 'chart_indicators')
    return {'display':db.get(Setting,'display').value,'chart_indicators':indicators.value if indicators else DEFAULT_CHART_INDICATORS,'execution':{'mode':'RESEARCH_ONLY','live_enabled':False,'implemented':False},'notifications':{'mode':'SIMULATED_LEGACY_REPORTS','web_configuration_implemented':False},'credentials':{'accepted':False,'reason':'API 凭据管理在 Phase 6 实现，当前只连接公开行情'}}

@router.put('/api/settings/display')
def update_settings(payload:DisplaySettings,user:User=Depends(current_user),db:DBSession=Depends(get_db)):
    """校验默认资产后保存时区、周期和标的偏好并写审计。

    Args:
        payload: 通过API schema校验的请求数据或报告绘图数据。
        user: 认证依赖已经校验的当前用户，不从请求正文接受替代身份。
        db: 本次调用的SQLAlchemy数据库会话。

    Returns:
        经过Pydantic校验的显示设置。
    """
    if db.get(Asset,payload.default_asset) is None:raise HTTPException(400,'资产不存在')
    db.get(Setting,'display').value=payload.model_dump();db.add(Audit(actor=user.username,action='SETTINGS_UPDATED',detail=payload.model_dump()));db.commit();return payload

@router.put('/api/settings/chart-indicators')
def update_chart_indicators(payload: ChartIndicatorSettings, user: User = Depends(current_user), db: DBSession = Depends(get_db)):
    """Persist display-only chart indicator choices in PostgreSQL with an audit event.

    Args:
        payload: Validated indicator visibility and moving-average lengths.
        user: Authenticated session identity; identity is never accepted in the body.
        db: Current SQLAlchemy session for the setting and audit transaction.

    Returns:
        The saved indicator settings; no strategy, order, or market-data state changes.
    """
    row = db.get(Setting, 'chart_indicators')
    values = payload.model_dump()
    if row is None:
        db.add(Setting(key='chart_indicators', value=values))
    else:
        row.value = values
    db.add(Audit(actor=user.username, action='CHART_INDICATORS_UPDATED', detail=values))
    db.commit()
    return values
