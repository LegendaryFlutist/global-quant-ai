"""模拟回测账号配置API；本模块没有资金账本或订单能力。"""

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from app.core.db import get_db
from app.core.security import current_user
from app.domains.models import Asset, SimulationAccount, Task, User
from app.services.jobs import enqueue
from app.services.long_history import three_year_start, validate_archive_identity

router = APIRouter()
INTERVALS = {"15m", "30m", "1h", "2h", "4h", "1d"}


class StrategyWeights(BaseModel):
    """归一化的研究理论和模型权重，不表示各项具有独立胜率。"""
    model_config = ConfigDict(extra="forbid")
    trend: Decimal = Field(ge=0, le=1)
    dow123: Decimal = Field(ge=0, le=1)
    wave: Decimal = Field(ge=0, le=1)
    divergence: Decimal = Field(ge=0, le=1)
    reversal_patterns: Decimal = Field(ge=0, le=1)
    deep_model: Decimal = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_sum(self):
        """要求所选组件权重之和为1，保持不同模拟账号可比。

        Returns:
            已验证的权重对象；总和偏离1时返回422输入错误。
        """
        total = sum(self.model_dump().values(), Decimal("0"))
        if abs(total - Decimal("1")) > Decimal("0.000001"):
            raise ValueError("理论和模型权重之和必须为1")
        return self


class SimulationPolicy(BaseModel):
    """一个模拟账号绑定的不可越界周期、模型和风险配置。"""
    model_config = ConfigDict(extra="forbid")
    asset_ids: list[str] = Field(min_length=1, max_length=10)
    intervals: list[str] = Field(min_length=1, max_length=6)
    weights: StrategyWeights
    entry_threshold: Decimal = Field(ge=0, le=1)
    max_leverage: Decimal = Field(ge=1, le=20)
    max_margin_fraction: Decimal = Field(gt=0, le=Decimal("0.25"))
    max_holding_hours: int = Field(ge=24, le=504)
    exit_mode: Literal["three_bar_trailing", "atr", "fixed_rr", "time"]
    deep_model_version: str | None = Field(default=None, max_length=120)

    @field_validator("asset_ids")
    @classmethod
    def validate_asset_ids(cls, values):
        """拒绝重复或没有市场命名空间的交易所产品ID。

        Args:
            values: 资产注册表精确产品ID列表。

        Returns:
            不重复的币安USDM产品ID列表；实际核验状态由数据库复核。
        """
        if len(values) != len(set(values)) or any(
            not value.startswith("binance_usdm:") or len(value) > 100 for value in values
        ):
            raise ValueError("标的必须是唯一的精确Binance USDM产品ID")
        return values

    @field_validator("intervals")
    @classmethod
    def validate_intervals(cls, values):
        """限制周期为已有归档支持的原生周期且拒绝重复项。

        Args:
            values: 配置文件中的原生周期名称列表。

        Returns:
            顺序稳定且无重复的白名单周期列表。
        """
        if not values or len(set(values)) != len(values) or any(item not in INTERVALS for item in values):
            raise ValueError("周期必须从15m、30m、1h、2h、4h、1d中选择且不能重复")
        return values

    @model_validator(mode="after")
    def validate_model_binding(self):
        """禁止为深度模型分配权重却未绑定明确版本。

        Returns:
            已验证的策略配置；模型缺失时不会自动替换或训练。
        """
        if self.weights.deep_model > 0 and not self.deep_model_version:
            raise ValueError("深度模型权重大于0时必须指定已登记模型版本")
        if self.weights.deep_model == 0 and self.deep_model_version:
            raise ValueError("不使用深度模型时应清除模型版本绑定")
        return self


class SimulationAccountPayload(BaseModel):
    """新增或更新用户隔离模拟配置账号的请求体。"""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80, pattern=r".*\S.*")
    initial_equity: Decimal = Field(gt=0, le=Decimal("1000000000000"), max_digits=28, decimal_places=8)
    base_currency: Literal["USDT"] = "USDT"
    configuration: SimulationPolicy


class DatasetRequest(BaseModel):
    """请求一个账号配置内标的和周期的本地合约研究数据归档。"""
    model_config = ConfigDict(extra="forbid")
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_window(self):
        """在显式提供时间窗时要求开始和结束毫秒同时有效。"""
        if (self.start_ms is None) != (self.end_ms is None):
            raise ValueError("start_ms和end_ms必须同时提供")
        if self.start_ms is not None and self.end_ms <= self.start_ms:
            raise ValueError("end_ms必须晚于start_ms")
        return self


def _config_hash(configuration):
    """对标准化策略配置计算SHA256以固定本次账号版本。

    Args:
        configuration: 已通过Pydantic校验的策略配置。

    Returns:
        UTF-8规范JSON的64位小写SHA256；不包含初始资本或私人凭据。
    """
    canonical = json.dumps(configuration.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _account_response(account):
    """输出用户可见模拟配置，保证资本金额使用十进制字符串。

    Args:
        account: 当前用户拥有的SimulationAccount ORM记录。

    Returns:
        配置账号字典；没有余额、成交或交易执行状态字段。
    """
    return {
        "id": account.id,
        "name": account.name,
        "initial_equity": format(account.initial_equity, "f"),
        "base_currency": account.base_currency,
        "configuration": account.configuration,
        "configuration_sha256": account.configuration_sha256,
        "revision": account.revision,
        "created_at": account.created_at,
        "updated_at": account.updated_at,
        "research_only": True,
        "backtest_implemented": False,
    }


def _validate_assets(db, asset_ids):
    """确认账号配置中的每个标的是目录内精确核验的USDT永续产品。

    Args:
        db: 当前认证请求使用的SQLAlchemy数据库会话。
        asset_ids: 通过请求schema检查的市场命名空间产品ID列表。

    Returns:
        无返回值；未知、未核验或跨市场ID抛出422错误。
    """
    eligible = set(db.scalars(
        select(Asset.id).where(
            Asset.id.in_(asset_ids),
            Asset.venue == "BINANCE_USDM",
            Asset.verification.in_(("VERIFIED", "VERIFIED_SNAPSHOT")),
        )
    ))
    if eligible != set(asset_ids):
        raise HTTPException(422, "模拟回测标的必须是注册表中精确核验的USDT永续产品")


@router.get("/api/simulation/accounts")
def list_accounts(user: User = Depends(current_user), db: DBSession = Depends(get_db)):
    """列出当前用户的回测配置账号，不读取其他用户或交易账户。

    Args:
        user: 会话认证依赖返回的用户记录。
        db: 本次调用的SQLAlchemy数据库会话。

    Returns:
        按创建时序返回的用户自有模拟配置账号。
    """
    accounts = db.scalars(
        select(SimulationAccount)
        .where(SimulationAccount.owner_user_id == user.id)
        .order_by(SimulationAccount.created_at, SimulationAccount.id)
    )
    return [_account_response(account) for account in accounts]


@router.post("/api/simulation/accounts", status_code=201)
def create_account(
    payload: SimulationAccountPayload,
    user: User = Depends(current_user),
    db: DBSession = Depends(get_db),
):
    """创建具有独立初始资金和理论权重的持久回测配置账号。

    Args:
        payload: 名称、Decimal初始资金与经过限制的策略配置。
        user: 会话认证依赖返回的用户记录。
        db: 本次调用的SQLAlchemy数据库会话。

    Returns:
        持久账号及不可变配置摘要；不创建余额或模拟成交。
    """
    configuration = payload.configuration.model_dump(mode="json")
    _validate_assets(db, payload.configuration.asset_ids)
    account = SimulationAccount(
        id=str(uuid.uuid4()),
        owner_user_id=user.id,
        name=payload.name.strip(),
        initial_equity=payload.initial_equity,
        base_currency=payload.base_currency,
        configuration=configuration,
        configuration_sha256=_config_hash(payload.configuration),
        revision=1,
    )
    db.add(account)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "该模拟账号名称已存在") from None
    db.refresh(account)
    return _account_response(account)


@router.put("/api/simulation/accounts/{account_id}")
def update_account(
    account_id: str,
    payload: SimulationAccountPayload,
    user: User = Depends(current_user),
    db: DBSession = Depends(get_db),
):
    """更新当前用户的模拟配置并增加修订号保留变更历史线索。

    Args:
        account_id: 路径中的稳定配置账号UUID。
        payload: 完整的新名称、Decimal初始资金与策略配置。
        user: 会话认证依赖返回的用户记录。
        db: 本次调用的SQLAlchemy数据库会话。

    Returns:
        新修订号及对应SHA256的研究配置；永不触及其他用户账号。
    """
    account = db.scalar(
        select(SimulationAccount).where(
            SimulationAccount.id == account_id,
            SimulationAccount.owner_user_id == user.id,
        )
    )
    if account is None:
        raise HTTPException(404, "模拟账号不存在")
    _validate_assets(db, payload.configuration.asset_ids)
    account.name = payload.name.strip()
    account.initial_equity = payload.initial_equity
    account.base_currency = payload.base_currency
    account.configuration = payload.configuration.model_dump(mode="json")
    account.configuration_sha256 = _config_hash(payload.configuration)
    account.revision += 1
    account.updated_at = datetime.now(timezone.utc)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "该模拟账号名称已存在") from None
    db.refresh(account)
    return _account_response(account)


@router.post("/api/simulation/accounts/{account_id}/datasets", status_code=202)
def request_account_datasets(
    account_id: str,
    request: DatasetRequest,
    user: User = Depends(current_user),
    db: DBSession = Depends(get_db),
):
    """按用户点击排队采集该账号选择资产/周期的三年标记价和资金费率。

    Args:
        account_id: 用户自有的模拟配置账号稳定ID。
        request: 可选显式UTC毫秒范围；缺省请求最近三个自然年。
        user: 会话认证依赖返回的用户记录。
        db: 当前认证请求的SQLAlchemy数据库会话。

    Returns:
        已创建或去重的逐资产/周期公开数据任务，不直接访问交易所。

    Raises:
        HTTPException: 账号不存在、范围超出三年或产品身份不再核验。
    """
    account = db.scalar(select(SimulationAccount).where(
        SimulationAccount.id == account_id,
        SimulationAccount.owner_user_id == user.id,
    ))
    if account is None:
        raise HTTPException(404, "模拟账号不存在")
    now_ms = int(time.time() * 1000)
    start_ms = request.start_ms if request.start_ms is not None else three_year_start(now_ms)
    end_ms = request.end_ms if request.end_ms is not None else now_ms
    if start_ms < three_year_start(now_ms) or end_ms > now_ms:
        raise HTTPException(422, "公开合约数据只允许最近三年内的已发生时间范围")
    jobs = []
    pairs = [
        (asset_id, interval)
        for asset_id in account.configuration["asset_ids"]
        for interval in account.configuration["intervals"]
    ]
    try:
        for asset_id, interval in pairs:
            validate_archive_identity(db, asset_id, interval)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    for asset_id, interval in pairs:
        parameters = {
            "asset_id": asset_id,
            "interval": interval,
            "start_ms": start_ms,
            "end_ms": end_ms,
        }
        active = next((candidate for candidate in db.scalars(select(Task).where(
            Task.kind == "FETCH_DERIVATIVE_DATA",
            Task.status.in_(["QUEUED", "RUNNING", "RETRYING"]),
        )) if candidate.parameters == parameters), None)
        task = active or enqueue(db, "FETCH_DERIVATIVE_DATA", parameters)
        jobs.append({"id": task.id, "asset_id": asset_id, "interval": interval, "status": task.status})
    return {"tasks": jobs, "start_ms": start_ms, "end_ms": end_ms, "research_only": True}
