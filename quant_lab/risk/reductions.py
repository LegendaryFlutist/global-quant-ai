"""仅减仓的研究规则；决策基于已知开盘和此前已收盘信息。"""

import math
from quant_lab.risk.policy import RiskPolicy


def reduction_decision(
    current_r,
    peak_r,
    score,
    entry_score,
    volatility,
    entry_volatility,
    completed=(),
    policy=None,
):
    """按恶化风险优先、再分级锁定利润的顺序选择一次性减仓动作。

    Args:
        current_r: 当前可观察价格的方向收益/初始硬止损距离。
        peak_r: 此前已完整收盘K线观察到的最大有利R值。
        score: 最新已收盘决策的质量分。
        entry_score: 入场时质量分。
        volatility: 最新已收盘日尺度波动率。
        entry_volatility: 入场决策时日尺度波动率。
        completed: 已执行动作键，防止同一条件重复减仓。
        policy: 不可变风险策略。

    Returns:
        动作字典或None；fraction表示初始数量比例，remaining_fraction表示剩余比例。
    """
    policy = policy or RiskPolicy()
    completed = set(completed)
    if not all(
        math.isfinite(float(v))
        for v in (current_r, peak_r, score, entry_score, volatility, entry_volatility)
    ):
        raise ValueError("减仓输入必须有限")
    if (
        not 0 <= score <= 100
        or not 0 <= entry_score <= 100
        or min(volatility, entry_volatility) <= 0
    ):
        raise ValueError("减仓评分或波动率无效")
    if (
        "VOLATILITY_SPIKE" not in completed
        and entry_volatility > 0
        and volatility >= entry_volatility * policy.volatility_spike_ratio
    ):
        return {
            "key": "VOLATILITY_SPIKE",
            "remaining_fraction": policy.risk_reduce_fraction,
            "protect_stop": False,
        }
    if (
        "SCORE_DETERIORATION" not in completed
        and score <= entry_score - policy.score_drop_points
    ):
        return {
            "key": "SCORE_DETERIORATION",
            "remaining_fraction": policy.risk_reduce_fraction,
            "protect_stop": False,
        }
    if (
        "PEAK_RETRACE" not in completed
        and peak_r >= policy.second_profit_r
        and current_r <= peak_r - policy.peak_retrace_r
    ):
        return {
            "key": "PEAK_RETRACE",
            "remaining_fraction": policy.risk_reduce_fraction,
            "protect_stop": True,
        }
    if "PROFIT_2R" not in completed and current_r >= policy.second_profit_r:
        # Mark the first step consumed as well if price gaps straight to 2R.
        fraction = policy.second_reduce_fraction + (
            0 if "PROFIT_1R" in completed else policy.first_reduce_fraction
        )
        return {
            "key": "PROFIT_2R",
            "fraction": fraction,
            "protect_stop": True,
            "consume": ["PROFIT_1R"],
        }
    if "PROFIT_1R" not in completed and current_r >= policy.first_profit_r:
        return {
            "key": "PROFIT_1R",
            "fraction": policy.first_reduce_fraction,
            "protect_stop": True,
        }
    return None
