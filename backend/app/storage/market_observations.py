"""Immutable Parquet storage for point-in-time reference values that are not candles."""

import hashlib
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import pyarrow as pa
from sqlalchemy import select, text

from app.core.config import DATA_ROOT
from app.domains.models import MarketObservation, utcnow
from app.storage.duckdb_query import query_parquet_rows
from app.storage.parquet_files import sha256_file, write_content_addressed_parquet

OBSERVATION_SCHEMA = pa.schema([
    ("observed_at_ms", pa.int64()),
    ("value", pa.decimal128(34, 12)),
    ("unit", pa.string()),
])


def store_observations(db, asset_id, metric, rows, unit, source):
    """Merge provider observations into a content-addressed local Parquet file.

    Args:
        db: Current SQLAlchemy session; only a research metadata pointer is stored.
        asset_id: Exact registered asset ID for the source time series.
        metric: Server-allowlisted scalar metric identifier.
        rows: Provider observations with UTC-millisecond time and decimal value.
        unit: Provider-reported or registry-defined unit string.
        source: Sanitized provider name, query window and retrieval timestamp.

    Returns:
        Row count, first/last observation timestamps and exact SHA256.

    Raises:
        ValueError: Identity, unit, time, numeric value or dataset path is invalid.
    """
    if not metric or len(metric) > 60 or not unit or len(unit) > 40:
        raise ValueError("参考序列标识或单位无效")
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"observation:{asset_id}:{metric}"})
    cleaned = {}
    for row in rows:
        try:
            timestamp = int(row["observed_at_ms"])
            value = Decimal(str(row["value"]))
        except (KeyError, TypeError, ValueError, InvalidOperation):
            raise ValueError("参考序列时间或数值无效") from None
        if timestamp < 0 or not value.is_finite():
            raise ValueError("参考序列含非有限值或无效时间")
        cleaned[timestamp] = {"observed_at_ms": timestamp, "value": value, "unit": unit}
    if not cleaned:
        raise ValueError("没有可保存的真实参考值")
    existing = db.scalar(select(MarketObservation).where(
        MarketObservation.asset_id == asset_id,
        MarketObservation.metric == metric,
    ))
    if existing is not None:
        prior = read_observations(existing)
        cleaned = {int(row["observed_at_ms"]): row for row in prior} | cleaned
    ordered = [cleaned[timestamp] for timestamp in sorted(cleaned)]
    partition = hashlib.sha256(asset_id.encode("utf-8")).hexdigest()[:24]
    file_directory = DATA_ROOT / "observations" / partition
    target, digest = write_content_addressed_parquet(
        pa.Table.from_pylist(ordered, schema=OBSERVATION_SCHEMA),
        file_directory,
        prefix=f"{metric}-{ordered[0]['observed_at_ms']}-{ordered[-1]['observed_at_ms']}",
    )
    relative_path = target.relative_to(DATA_ROOT).as_posix()
    values = {
        "path": relative_path,
        "row_count": len(ordered),
        "first_observed_at_ms": ordered[0]["observed_at_ms"],
        "last_observed_at_ms": ordered[-1]["observed_at_ms"],
        "unit": unit,
        "sha256": digest,
        "source": source,
        "updated_at": utcnow(),
    }
    if existing is None:
        db.add(MarketObservation(asset_id=asset_id, metric=metric, **values))
    else:
        for name, value in values.items():
            setattr(existing, name, value)
    db.commit()
    return {"asset_id": asset_id, "metric": metric, "rows": len(ordered), "first_observed_at_ms": ordered[0]["observed_at_ms"], "last_observed_at_ms": ordered[-1]["observed_at_ms"], "sha256": digest, "unit": unit}


def read_observations(metadata):
    """Verify and read a scalar series in ascending event time.

    Args:
        metadata: PostgreSQL pointer to one immutable observation Parquet file.

    Returns:
        Provider-reported values with `Decimal` precision preserved.

    Raises:
        ValueError: The file path escapes the research root or its SHA256 differs.
    """
    path = (DATA_ROOT / metadata.path).resolve()
    base = DATA_ROOT.resolve()
    if not path.is_relative_to(base) or path.suffix != ".parquet" or not path.is_file():
        raise ValueError("参考序列文件不存在或路径越界")
    if sha256_file(path) != metadata.sha256:
        raise ValueError("参考序列SHA256校验失败")
    rows = query_parquet_rows([path], order_column="observed_at_ms")
    if len(rows) != metadata.row_count:
        raise ValueError("参考序列行数与数据库元数据不一致")
    return rows
