"""Phase 1 tables plus versioned research configs; no ledger or execution tables."""
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import String, Integer, BigInteger, Boolean, DateTime, JSON, ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base
from app.domains.datasets import DatasetVersion  # Register additive metadata in test/Alembic discovery.
from app.domains.instruments import InstrumentVersion, InstrumentPeriod, InstrumentAliasPeriod

def utcnow():
    """为持久记录生成带UTC时区的当前时间。

    Returns:
        timezone-aware datetime。
    """
    return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(20), default='admin')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class Session(Base):
    __tablename__ = 'sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'))
    csrf_token: Mapped[str] = mapped_column(String(80))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

class Audit(Base):
    __tablename__ = 'security_audit_logs'
    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(80))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class Provider(Base):
    __tablename__ = 'providers'
    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(40), default='UNKNOWN')
    detail: Mapped[dict] = mapped_column(JSON, default=dict)

class Asset(Base):
    __tablename__ = 'assets'
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(180))
    underlying_id: Mapped[str] = mapped_column(String(100), index=True)
    asset_type: Mapped[str] = mapped_column(String(50))
    venue: Mapped[str] = mapped_column(String(40))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    verification: Mapped[str] = mapped_column(String(40), default='UNVERIFIED')
    specification: Mapped[dict] = mapped_column(JSON, default=dict)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class Alias(Base):
    __tablename__ = 'asset_aliases'
    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey('assets.id'))
    provider_id: Mapped[str] = mapped_column(ForeignKey('providers.id'))
    alias: Mapped[str] = mapped_column(String(100))
    __table_args__ = (UniqueConstraint('provider_id', 'alias'),)

class Membership(Base):
    __tablename__ = 'sector_membership'
    asset_id: Mapped[str] = mapped_column(ForeignKey('assets.id'), primary_key=True)
    sector: Mapped[str] = mapped_column(String(60), primary_key=True)

class MarketData(Base):
    __tablename__ = 'market_data_metadata'
    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey('assets.id'), index=True)
    interval: Mapped[str] = mapped_column(String(10))
    path: Mapped[str] = mapped_column(String(300))
    row_count: Mapped[int] = mapped_column(Integer)
    first_open_ms: Mapped[int] = mapped_column(BigInteger)
    last_close_ms: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    source: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint('asset_id', 'interval'),)

class MarketObservation(Base):
    """Non-OHLC reference time series such as daily physical-energy spot prices."""
    __tablename__ = 'market_observation_metadata'
    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey('assets.id'), index=True)
    metric: Mapped[str] = mapped_column(String(60))
    path: Mapped[str] = mapped_column(String(300))
    row_count: Mapped[int] = mapped_column(Integer)
    first_observed_at_ms: Mapped[int] = mapped_column(BigInteger)
    last_observed_at_ms: Mapped[int] = mapped_column(BigInteger)
    unit: Mapped[str] = mapped_column(String(40))
    sha256: Mapped[str] = mapped_column(String(64))
    source: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint('asset_id', 'metric'),)

class Task(Base):
    __tablename__ = 'scheduled_tasks'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), default='QUEUED')
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class SimulationAccount(Base):
    """用户隔离的回测策略配置容器；不代表余额账本或可交易账户。"""
    __tablename__ = 'simulation_accounts'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    name: Mapped[str] = mapped_column(String(80))
    initial_equity: Mapped[Decimal] = mapped_column(Numeric(28, 8))
    base_currency: Mapped[str] = mapped_column(String(8), default='USDT')
    configuration: Mapped[dict] = mapped_column(JSON)
    configuration_sha256: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint('owner_user_id', 'name', name='uq_simulation_account_owner_name'),)

class Setting(Base):
    __tablename__ = 'system_settings'
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)

class SystemEvent(Base):
    __tablename__ = 'system_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(60))
    detail: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
