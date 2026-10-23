"""Append-only instrument timelines; independent of legacy mutable asset specifications."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, JSON, Numeric, String, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class InstrumentVersion(Base):
    """One complete specification/alias timeline as actually recorded by this installation."""
    __tablename__ = "instrument_versions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    descriptor: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("asset_id", "recorded_at", name="uq_instrument_recording"),)


class InstrumentPeriod(Base):
    """Half-open UTC validity interval; monetary/quantity rules remain nullable NUMERIC."""
    __tablename__ = "instrument_spec_periods"
    version_id: Mapped[str] = mapped_column(ForeignKey("instrument_versions.id"), primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lot_size: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    tick_size: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    multiplier: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    min_notional: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    rules: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (
        CheckConstraint("effective_to IS NULL OR effective_to > effective_from", name="ck_instrument_period_bounds"),
        CheckConstraint("lot_size IS NULL OR lot_size > 0", name="ck_instrument_lot"),
        CheckConstraint("tick_size IS NULL OR tick_size > 0", name="ck_instrument_tick"),
        CheckConstraint("multiplier IS NULL OR multiplier > 0", name="ck_instrument_multiplier"),
        CheckConstraint("min_notional IS NULL OR min_notional >= 0", name="ck_instrument_notional"),
    )


class InstrumentAliasPeriod(Base):
    """Version-owned source code; strings preserve leading zeroes and market identity."""
    __tablename__ = "instrument_alias_periods"
    version_id: Mapped[str] = mapped_column(ForeignKey("instrument_versions.id"), primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.id"), index=True)
    alias: Mapped[str] = mapped_column(String(100), index=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("effective_to IS NULL OR effective_to > effective_from", name="ck_instrument_alias_bounds"),)


def reject_instrument_mutation(mapper, connection, target):
    """Reject ORM changes to published timelines; corrections must publish a complete new version."""
    raise ValueError("产品规格版本不可修改或删除；请发布新版本")


for model in (InstrumentVersion, InstrumentPeriod, InstrumentAliasPeriod):
    event.listen(model, "before_update", reject_instrument_mutation)
    event.listen(model, "before_delete", reject_instrument_mutation)
