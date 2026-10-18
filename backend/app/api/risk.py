"""只读历史风控研究展示及无订单能力的预算计算。"""

import json
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from app.core.config import RESEARCH_ROOT
from app.core.security import current_user
from app.domains.models import User
from quant_lab.risk.policy import RiskPolicy
from quant_lab.risk.sizing import position_budget

router = APIRouter()


class BudgetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    score: float = Field(ge=0, le=100)
    daily_volatility: float = Field(gt=0, le=2)
    stop_fraction: float = Field(gt=0, lt=1)
    direction: int = Field(ge=-1, le=1)
    max_leverage: int = Field(default=10, ge=1, le=20, strict=True)
    risk_percent: float = Field(default=1, ge=0.25, le=2)
    drawdown: float = Field(default=0, ge=0, le=1)


@router.get("/api/risk/research")
def risk_research(user: User = Depends(current_user)):
    """读取固定本地产物路径，不接受用户文件名；不存在返回503，不编造评分。"""
    path = RESEARCH_ROOT / "runs/risk_latest/report.json"
    if not path.is_file():
        raise HTTPException(
            503,
            "尚未生成风险研究；在GPU容器运行 python -m quant_lab.services.risk_research",
        )
    try:
        report = json.loads(path.read_text())
        if report["schema_version"] != 1 or report["real_orders_enabled"] is not False:
            raise ValueError("contract")
    except (ValueError, KeyError):
        raise HTTPException(503, "风险研究产物格式无效，请重新生成")
    return report


@router.post("/api/risk/budget")
def budget(body: BudgetRequest, user: User = Depends(current_user)):
    """在认证与CSRF边界内计算假设预算；参数不写配置、不执行交易。"""
    policy = RiskPolicy(
        max_leverage=body.max_leverage, max_risk_fraction=body.risk_percent / 100
    )
    return position_budget(
        body.score,
        body.daily_volatility,
        body.stop_fraction,
        body.direction,
        body.drawdown,
        policy=policy,
    )
