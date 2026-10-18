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
    def assess(self, proposal: ProposedOrder) -> dict: ...

class ResearchEngine(Protocol):
    def enqueue_training(self, specification: dict) -> str: ...

class LiveExecutionUnavailable:
    def submit(self, *args, **kwargs):
        raise PermissionError('Phase 1 不包含交易执行；任何模型都不能下单')
