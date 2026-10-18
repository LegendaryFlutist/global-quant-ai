"""将模型质量与标的波动换算为有限的逐仓研究预算。"""

import math
from quant_lab.risk.policy import RiskPolicy


def position_budget(
    score,
    daily_volatility,
    stop_fraction,
    direction=1,
    drawdown=0.0,
    fee=0.0005,
    slip=0.0002,
    policy=None,
):
    """计算开仓前的杠杆、保证金与名义暴露；高杠杆不自动提高风险预算。

    Args:
        score: 0至100质量分，不是胜率。
        daily_volatility: 只用过去已收盘收益计算的日尺度标准差比例。
        stop_fraction: 实际入场价到初始硬止损的相对距离。
        direction: 1/-1为候选方向，0时不得开仓。
        drawdown: 当前已知账户回撤比例，达到上限拒绝新仓。
        fee: 单边手续费比例。
        slip: 单边滑点比例。
        policy: 校验后的研究RiskPolicy；默认使用固定版本默认值。

    Returns:
        含允许状态、原因、风险预算、杠杆、保证金和名义暴露比例的字典。
        止损、成本及预留资金费只是估算；跳空/流动性风险可超过预算。
    """
    policy = policy or RiskPolicy()
    if not all(
        math.isfinite(float(v))
        for v in (score, daily_volatility, stop_fraction, drawdown, fee, slip)
    ):
        raise ValueError("仓位输入必须为有限数值")
    if (
        not 0 <= score <= 100
        or daily_volatility <= 0
        or not 0 < stop_fraction < 1
        or not 0 <= drawdown <= 1
        or min(fee, slip) < 0
    ):
        raise ValueError("仓位输入超出有效范围")
    if direction not in (-1, 0, 1):
        raise ValueError("方向必须是-1/0/1")
    reasons = []
    if direction == 0:
        reasons.append("模型/大周期条件未形成可用方向")
    if score < policy.min_score:
        reasons.append("入场质量低于门槛")
    if drawdown >= policy.max_drawdown_fraction:
        reasons.append("账户回撤达到暂停新仓上限")
    distance = max(stop_fraction, policy.min_stop_fraction)
    quality = 0.25 + 0.75 * max(
        0.0, (score - policy.min_score) / (100 - policy.min_score)
    )
    risk_fraction = (
        policy.max_risk_fraction
        * quality
        * max(0.0, 1 - drawdown / policy.max_drawdown_fraction)
    )
    # Cap leverage using both observed volatility and a deliberately conservative
    # stop-to-approximate-liquidation gap. Actual Binance brackets are unknown.
    volatility_cap = (
        policy.base_leverage
        * policy.reference_daily_volatility
        / daily_volatility
        * (0.75 + 0.5 * quality)
    )
    safety_cap = 0.5 / (
        distance
        + policy.maintenance_rate_assumption
        + 2 * (fee + slip)
        + policy.funding_reserve_fraction
    )
    leverage = math.floor(min(policy.max_leverage, volatility_cap, safety_cap))
    if leverage < 1:
        reasons.append("波动或止损距离超出近似逐仓安全余量")
    leverage = max(1, leverage)
    loss_per_nominal = distance + 2 * (fee + slip) + policy.funding_reserve_fraction
    nominal = min(
        policy.max_nominal_exposure,
        risk_fraction / loss_per_nominal,
        policy.target_daily_account_volatility / daily_volatility,
        policy.max_margin_fraction * leverage,
    )
    if reasons:
        nominal = 0.0
    return dict(
        allowed=not reasons,
        reasons=reasons,
        score=float(score),
        daily_volatility=float(daily_volatility),
        stop_fraction=distance,
        leverage=int(leverage),
        margin_fraction=float(nominal / leverage),
        nominal_exposure=float(nominal),
        risk_budget_fraction=float(risk_fraction if not reasons else 0),
        estimated_stop_loss_fraction=float(nominal * loss_per_nominal),
        estimated_daily_account_volatility=float(nominal * daily_volatility),
        policy_version=policy.version,
        margin_mode="ISOLATED_RESEARCH",
        exchange_brackets_verified=False,
        real_orders_enabled=False,
    )
