"""Architectural contracts for subsequent phases. No executable broker is provided."""
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

@dataclass(frozen=True)
class ModelVersionRef:
    model_id: str
    version_id: str
    feature_schema_hash: str
    artifact_sha256: str

@dataclass(frozen=True)
class ProposedOrder:
    account_id: str
    asset_id: str
    model_version: ModelVersionRef
    side: str
    quantity: Decimal
    limit_price: Decimal

class RiskGate(Protocol):
    def assess(self, proposal: ProposedOrder) -> dict:
        """声明后续独立风控评估器的接口契约。

        Args:
            proposal: 未来执行域的不可变交易建议；当前只是接口契约。

        Returns:
            风控评估字典；Protocol本身没有执行实现。
        """
        ...

class ResearchEngine(Protocol):
    def enqueue_training(self, specification: dict) -> str:
        """声明后续版本化训练服务的任务提交契约。

        Args:
            specification: 未来训练服务的版本化模型规格字典。

        Returns:
            预期训练任务ID；本阶段没有实际训练实现。
        """
        ...

class LiveExecutionUnavailable:
    def submit(self, *args, **kwargs):
        """明确阻止未实现的实盘执行服务提交任何请求。

        Args:
            *args: 透传的额外位置参数，按当前接口契约解释。
            **kwargs: 透传的命名参数；不能绕过当前调用的校验。

        Returns:
            始终抛出PermissionError，不能通过配置绕过。
        """
        raise PermissionError('Phase 1 不包含交易执行；任何模型都不能下单')
