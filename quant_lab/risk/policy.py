"""已声明、可复现的研究风险参数；默认预算不等于承诺的最大实际损失。"""

from dataclasses import asdict, dataclass
import json
import math
from quant_lab.core.paths import PROJECT_ROOT


@dataclass(frozen=True)
class RiskPolicy:
    version: str = "entry_score_volatility_v1"
    min_score: float = 55.0
    min_probability: float = 0.45
    max_leverage: int = 10
    base_leverage: float = 5.0
    reference_daily_volatility: float = 0.02
    target_daily_account_volatility: float = 0.015
    max_margin_fraction: float = 0.2
    max_nominal_exposure: float = 1.5
    max_risk_fraction: float = 0.01
    max_drawdown_fraction: float = 0.08
    maintenance_rate_assumption: float = 0.01
    funding_reserve_fraction: float = 0.001
    min_stop_fraction: float = 0.005
    first_profit_r: float = 1.0
    second_profit_r: float = 2.0
    first_reduce_fraction: float = 0.25
    second_reduce_fraction: float = 0.25
    volatility_spike_ratio: float = 1.75
    score_drop_points: float = 20.0
    peak_retrace_r: float = 1.0
    risk_reduce_fraction: float = 0.5

    def __post_init__(self):
        """拒绝非有限、越界或会放宽逐仓安全边界的研究参数。

        Raises:
            ValueError: 杠杆大于20、单笔预算大于2%或其他门槛不合法。
        """
        values = asdict(self)
        if not isinstance(self.version, str) or not self.version:
            raise ValueError("风险策略必须声明版本")
        if any(
            not isinstance(v, (int, float))
            or isinstance(v, bool)
            or not math.isfinite(v)
            for k, v in values.items()
            if k != "version"
        ):
            raise ValueError("风险参数必须是有限数值")
        if (
            not 1 <= self.max_leverage <= 20
            or int(self.max_leverage) != self.max_leverage
        ):
            raise ValueError("研究杠杆上限必须是1至20的整数")
        if (
            not 0 < self.max_risk_fraction <= 0.02
            or not 0 < self.max_margin_fraction <= 0.3
            or not 0 < self.max_nominal_exposure <= 2
        ):
            raise ValueError("风险/保证金/名义暴露预算超限")
        if (
            not 0 < self.base_leverage <= 20
            or not 0 < self.min_stop_fraction < 0.5
            or not 0 <= self.maintenance_rate_assumption < 1 / self.max_leverage
        ):
            raise ValueError("逐仓保证金或止损参数无效")
        if (
            not 0 <= self.min_score < 100
            or not 0 < self.min_probability < 1
            or not 0 < self.max_drawdown_fraction <= 0.2
        ):
            raise ValueError("评分或回撤门槛无效")
        if (
            min(
                self.reference_daily_volatility,
                self.target_daily_account_volatility,
                self.peak_retrace_r,
            )
            <= 0
            or not 0 <= self.funding_reserve_fraction <= 0.02
        ):
            raise ValueError("波动或资金费预算无效")
        if (
            not 0 < self.first_profit_r < self.second_profit_r
            or not 0 < self.first_reduce_fraction < 1
            or not 0 < self.second_reduce_fraction < 1
            or self.first_reduce_fraction + self.second_reduce_fraction >= 1
        ):
            raise ValueError("分批止盈必须保留正的剩余仓位")
        if (
            not self.volatility_spike_ratio > 1
            or not 0 < self.score_drop_points <= 100
            or not 0 < self.risk_reduce_fraction < 1
        ):
            raise ValueError("风险减仓条件无效")

    def to_dict(self):
        """返回可写入研究产物的完整版本化参数副本，不含凭据。"""
        return asdict(self)


def load_risk_policy(overrides=None):
    """加载预声明的JSON策略并合并显式研究覆盖项。

    Args:
        overrides: 可选参数字典；未知字段由dataclass拒绝。

    Returns:
        校验过的不可变RiskPolicy；不会修改默认JSON文件。
    """
    values = json.loads((PROJECT_ROOT / "config/risk.json").read_text())
    values.update(overrides or {})
    return RiskPolicy(**values)
