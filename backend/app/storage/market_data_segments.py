"""Append-only Parquet segments for source-native underlying candle histories."""

import hashlib
import json
from pathlib import Path

import pyarrow as pa
from sqlalchemy import select, text

from app.core.config import DATA_ROOT
from app.domains.models import MarketData, utcnow
from app.storage.parquet_files import sha256_file, write_content_addressed_parquet, write_json_atomic

SEGMENT_MANIFEST_VERSION = 1
SEGMENT_ROW_LIMIT = 1000


def _underlying_partition(asset_id, interval, root=None):
    """Return a private partition path derived from a registered source identity.

    Args:
        asset_id: Exact database asset identity, not a provider URL or user path.
        interval: Validated native source interval.
        root: Optional isolated storage root for tests.

    Returns:
        Resolved directory path below the configured data root.
    """
    base = Path(root) if root is not None else DATA_ROOT
    base = base.resolve()
    identity = hashlib.sha256(asset_id.encode("utf-8")).hexdigest()[:24]
    target = (base / "underlying_history" / identity / interval).resolve()
    if not target.is_relative_to(base):
        raise ValueError("底层行情归档路径越界")
    return target


def _read_manifest(path, asset_id, interval, expected_sha=None):
    """Validate a content-addressed segment manifest and every referenced file.

    Args:
        path: Manifest JSON path resolved from PostgreSQL metadata.
        asset_id: Expected exact source asset ID.
        interval: Expected provider-native candle interval.
        expected_sha: Optional manifest digest stored in PostgreSQL.

    Returns:
        Validated manifest and its ordered segment paths.

    Raises:
        ValueError: Identity, path, file digest, bounds, or manifest schema is invalid.
    """
    manifest_path = Path(path).resolve()
    base = DATA_ROOT.resolve()
    if not manifest_path.is_relative_to(base) or manifest_path.suffix != ".json" or not manifest_path.is_file():
        raise ValueError("底层行情清单缺失或路径越界")
    digest = sha256_file(manifest_path)
    if expected_sha and digest != expected_sha:
        raise ValueError("底层行情清单SHA256校验失败")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("底层行情清单JSON无效") from None
    if (manifest.get("schema_version") != SEGMENT_MANIFEST_VERSION
            or manifest.get("asset_id") != asset_id
            or manifest.get("interval") != interval
            or not isinstance(manifest.get("segments"), list)
            or not manifest["segments"]):
        raise ValueError("底层行情清单身份或格式不匹配")
    partition = manifest_path.parent.resolve()
    if not partition.is_relative_to(base):
        raise ValueError("底层行情分段目录越界")
    paths = []
    count = 0
    previous_last = None
    for segment in manifest["segments"]:
        name = segment.get("file", "")
        if not name or Path(name).name != name or not name.endswith(".parquet"):
            raise ValueError("底层行情分段文件名无效")
        segment_path = (partition / name).resolve()
        if segment_path.parent != partition or not segment_path.is_file():
            raise ValueError("底层行情分段文件缺失或越界")
        if sha256_file(segment_path) != segment.get("sha256"):
            raise ValueError("底层行情分段SHA256校验失败")
        first_open = int(segment.get("first_open_ms", -1))
        last_open = int(segment.get("last_open_ms", -1))
        if first_open < 0 or last_open < first_open or (previous_last is not None and first_open <= previous_last):
            raise ValueError("底层行情分段时间范围重叠或倒序")
        previous_last = last_open
        count += int(segment.get("rows", 0))
        paths.append(segment_path)
    if count != int(manifest.get("row_count", -1)):
        raise ValueError("底层行情分段行数与清单不一致")
    return manifest, paths


def read_underlying_segments(metadata):
    """Read a registered segmented underlying history after validating all hashes.

    Args:
        metadata: PostgreSQL pointer to one asset/interval source-native manifest.

    Returns:
        Ascending candles with Arrow Decimal values preserved.

    Raises:
        ValueError: Manifest or any immutable Parquet segment fails validation.
    """
    from app.storage.duckdb_query import query_parquet_rows

    manifest_path = DATA_ROOT / metadata.path
    manifest, paths = _read_manifest(manifest_path, metadata.asset_id, metadata.interval, metadata.sha256)
    rows = query_parquet_rows(paths, order_column="open_time")
    if len(rows) != metadata.row_count or len(rows) != manifest["row_count"]:
        raise ValueError("底层行情清单与数据库行数不一致")
    return rows


def save_underlying_segments(db, asset, interval, rows, source, now_ms, *, max_age_days):
    """Append new bars or compact revisions into immutable content-addressed Parquet.

    Args:
        db: Current SQLAlchemy session for task worker or authenticated chart refresh.
        asset: Exact registered underlying Asset row.
        interval: Native provider interval validated by the source contract.
        rows: Closed OHLCV bars already parsed into Decimal values.
        source: Safe provider provenance without credentials or user-submitted URLs.
        now_ms: Current UTC timestamp in milliseconds.
        max_age_days: Retention window; three-year source snapshots use about 1097 days.

    Returns:
        Updated MarketData metadata summary. The JSON manifest points only to
        verified immutable content-addressed Parquet segments.

    Raises:
        ValueError: No valid closed bars, row conflicts, or persisted files are corrupt.
    """
    from app.services.market_data import SCHEMA, normalize, read_dataset

    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"{asset.id}:{interval}"})
    closed = normalize(
        rows, now_ms, interval, max_age_days=max_age_days,
        provider_native=source.get("timestamp_alignment") == "exchange_session",
        allow_nonpositive_prices=source.get("provider") == "yahoo_energy" and asset.id in {"yahoo_energy:CL", "yahoo_energy:NG"},
    )
    existing = db.scalar(select(MarketData).where(MarketData.asset_id == asset.id, MarketData.interval == interval))
    prior = read_dataset(existing) if existing is not None else []
    prior_by_open = {int(row["open_time"]): row for row in prior}
    incoming = {int(row["open_time"]): row for row in closed}
    merged_by_open = dict(prior_by_open)
    merged_by_open.update(incoming)
    merged_rows = [merged_by_open[timestamp] for timestamp in sorted(merged_by_open)]
    if not merged_rows:
        raise ValueError("没有可保存的底层闭合K线")
    latest_prior_open = max(prior_by_open, default=None)
    revision = any(
        (timestamp in prior_by_open and prior_by_open[timestamp] != row)
        or (timestamp not in prior_by_open and latest_prior_open is not None and timestamp < latest_prior_open)
        for timestamp, row in incoming.items()
    )
    partition = _underlying_partition(asset.id, interval)
    segments = []
    if existing is not None and existing.path.endswith(".json") and not revision:
        prior_manifest, _paths = _read_manifest(DATA_ROOT / existing.path, asset.id, interval, existing.sha256)
        segments = list(prior_manifest["segments"])
        prior_rows_by_open = prior_by_open
        append_rows = [row for timestamp, row in sorted(incoming.items()) if timestamp not in prior_rows_by_open]
    else:
        append_rows = merged_rows
    if revision:
        segments = []
    if existing is not None and existing.path.endswith(".json") and not revision and not append_rows:
        return {"asset_id": asset.id, "interval": interval, "rows": existing.row_count,
                "first_open_ms": existing.first_open_ms, "last_close_ms": existing.last_close_ms,
                "sha256": existing.sha256, "new_segment_count": 0}
    if append_rows:
        partition.mkdir(parents=True, exist_ok=True)
        table_schema = SCHEMA
        if source.get("provider") == "yahoo_energy":
            # Preserve Yahoo's native decimal payload, which may exceed 12 places.
            table_schema = pa.schema([
                field.with_type(pa.decimal128(38, 18)) if pa.types.is_decimal(field.type) else field
                for field in SCHEMA
            ])
        for offset in range(0, len(append_rows), SEGMENT_ROW_LIMIT):
            page = append_rows[offset:offset + SEGMENT_ROW_LIMIT]
            target, digest = write_content_addressed_parquet(
                pa.Table.from_pylist(page, schema=table_schema), partition,
                prefix=f"{page[0]['open_time']}-{page[-1]['open_time']}",
            )
            segments.append({
                "file": target.name, "first_open_ms": page[0]["open_time"],
                "last_open_ms": page[-1]["open_time"],
                "last_close_ms": page[-1]["close_time"], "rows": len(page), "sha256": digest,
            })
    segments.sort(key=lambda item: item["first_open_ms"])
    manifest = {
        "schema_version": SEGMENT_MANIFEST_VERSION,
        "asset_id": asset.id,
        "symbol": asset.symbol,
        "interval": interval,
        "row_count": len(merged_rows),
        "first_open_ms": merged_rows[0]["open_time"],
        "last_open_ms": merged_rows[-1]["open_time"],
        "last_close_ms": merged_rows[-1]["close_time"],
        "updated_at_ms": int(now_ms),
        "source": source,
        "segments": segments,
    }
    canonical = json.dumps(manifest, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    manifest_digest = hashlib.sha256(canonical).hexdigest()
    manifest_name = f"manifest-{manifest_digest[:20]}.json"
    manifest_path = write_json_atomic(partition / manifest_name, manifest, compact=True)
    if sha256_file(manifest_path) != manifest_digest:
        raise ValueError("底层行情清单写入后SHA256不匹配")
    path_value = manifest_path.relative_to(DATA_ROOT.resolve()).as_posix()
    values = {
        "path": path_value,
        "row_count": len(merged_rows),
        "first_open_ms": merged_rows[0]["open_time"],
        "last_close_ms": merged_rows[-1]["close_time"],
        "sha256": manifest_digest,
        "source": source,
        "updated_at": utcnow(),
    }
    if existing is None:
        db.add(MarketData(asset_id=asset.id, interval=interval, **values))
    else:
        for key, value in values.items():
            setattr(existing, key, value)
    db.commit()
    return {"asset_id": asset.id, "interval": interval, "rows": len(merged_rows),
            "first_open_ms": values["first_open_ms"], "last_close_ms": values["last_close_ms"],
            "sha256": manifest_digest, "new_segment_count": len(segments)}
