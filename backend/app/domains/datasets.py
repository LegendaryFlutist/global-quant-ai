"""Immutable source-specific research dataset versions, separate from legacy pointers."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, event
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class DatasetVersion(Base):
    """A frozen research snapshot; a later source revision always creates a new row."""

    __tablename__ = "dataset_versions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.id"), index=True)
    interval: Mapped[str] = mapped_column(String(16))
    price_type: Mapped[str] = mapped_column(String(40))
    adjustment: Mapped[str] = mapped_column(String(40))
    schema_version: Mapped[str] = mapped_column(String(40))
    calendar_version: Mapped[str] = mapped_column(String(100))
    dataset_sha256: Mapped[str] = mapped_column(String(64))
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    manifest_path: Mapped[str] = mapped_column(String(500))
    row_count: Mapped[int] = mapped_column(Integer)
    first_open_ms: Mapped[int] = mapped_column(BigInteger)
    last_close_ms: Mapped[int] = mapped_column(BigInteger)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    provenance: Mapped[dict] = mapped_column(JSON)
    quality: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (
        CheckConstraint("row_count > 0", name="ck_dataset_versions_rows"),
        CheckConstraint("last_close_ms >= first_open_ms", name="ck_dataset_versions_times"),
    )


@event.listens_for(DatasetVersion, "before_update")
@event.listens_for(DatasetVersion, "before_delete")
def reject_dataset_mutation(mapper, connection, target):
    """Reject ORM update/delete of a frozen version; corrections publish a new ID."""
    raise ValueError("数据集版本不可修改或删除；请发布新版本")
