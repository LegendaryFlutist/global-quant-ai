"""Immutable, observed-at research snapshots with exact source and product identity."""

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pyarrow as pa
from sqlalchemy import text

from app.core.config import DATA_ROOT
from app.domains.datasets import DatasetVersion
from app.domains.models import Asset, Provider
from app.storage.duckdb_query import query_parquet_rows
from app.storage.parquet_files import sha256_file, write_parquet_immutable

SCHEMA_VERSION = "research_bars_v1"
SCHEMA = pa.schema([
    ("open_time", pa.int64()), ("close_time", pa.int64()),
    *[(name, pa.decimal128(38, 18)) for name in ("open", "high", "low", "close", "volume")],
    ("session_date", pa.string()), ("source_published_at_ms", pa.int64()),
    ("observed_at_ms", pa.int64()), ("available_at_ms", pa.int64()),
    ("is_final", pa.bool_()), ("volume_missing", pa.bool_()),
])
_PROVENANCE_FIELDS = {
    "price_type", "adjustment", "calendar_version", "parser_version", "license",
    "delay", "entitlement", "verification", "timestamp_alignment", "source_symbol",
    "timezone", "currency", "volume_unit", "session_close_policy", "calendar_scope",
    "origin_source", "source_sha256",
}
_PARAM_FIELDS = {"symbol", "ticker", "pair", "interval", "start", "end", "startTime",
                 "endTime", "limit", "from", "to", "period1", "period2", "events",
                 "adjusted", "sort", "i", "d1", "d2", "s", "since",
                 "includeAdjustedClose", "includePrePost", "granularity"}
_TOKEN = re.compile(r"[A-Za-z0-9_.:-]{1,100}\Z")


def _canonical(value):
    """Encode public metadata canonically; Decimals retain their exact string value."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"), default=_decimal_text).encode("utf-8")


def _decimal_text(value):
    """Serialize exact prices at the Arrow decimal scale without binary float coercion."""
    if not isinstance(value, Decimal):
        raise TypeError("Unsupported canonical metadata value")
    return format(value, ".18f")


def _public_metadata(value, allowed):
    """Accept only bounded scalar public fields; reject unknown credential parameters."""
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError("来源元数据或请求参数包含不允许的字段")
    result = {}
    for key, item in value.items():
        if not isinstance(item, (str, int, bool, type(None))) or len(str(item)) > 500:
            raise ValueError("来源元数据必须是有界公开标量")
        if isinstance(item, str) and ("://" in item or "\n" in item or "\r" in item):
            raise ValueError("来源元数据不接受URL或多行内容")
        result[key] = item
    return result


def _normalize_rows(rows, now_ms):
    """Validate closed bars, preserve zero volume, and conservatively timestamp reception."""
    if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
        raise ValueError("观测时刻必须是UTC毫秒整数")
    unique = {}
    for original in rows:
        row = {}
        for key in ("open_time", "close_time"):
            value = original.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("K线时间必须是非负UTC毫秒整数")
            row[key] = value
        if not row["open_time"] <= row["close_time"] < now_ms:
            raise ValueError("仅允许已经闭合的真实K线")
        for key in ("open", "high", "low", "close", "volume"):
            value = original.get(key)
            if key == "volume" and value is None:
                row[key] = None
                continue
            if isinstance(value, (float, bool)):
                raise ValueError("行情金额必须使用Decimal或十进制字符串")
            try:
                parsed = Decimal(value)
            except (TypeError, InvalidOperation):
                raise ValueError("行情包含无效十进制数") from None
            if not parsed.is_finite() or parsed < 0 or (key != "volume" and parsed == 0):
                raise ValueError("本阶段现货与证券K线必须具有正价格和非负成交量")
            row[key] = parsed
        if row["high"] < max(row["open"], row["close"], row["low"]) or row["low"] > min(row["open"], row["close"]):
            raise ValueError("OHLC高低范围无效")
        session = original.get("session_date")
        if session is not None:
            try:
                if datetime.strptime(session, "%Y-%m-%d").strftime("%Y-%m-%d") != session:
                    raise ValueError()
            except (TypeError, ValueError):
                raise ValueError("session_date必须为真实YYYY-MM-DD日期") from None
        published = original.get("source_published_at_ms")
        if published is not None and (isinstance(published, bool) or not isinstance(published, int) or published < 0 or published > now_ms):
            raise ValueError("来源发布时间必须为不晚于观测时刻的UTC毫秒")
        observed = original.get("observed_at_ms", now_ms)
        available = original.get("available_at_ms", observed)
        if any(isinstance(item, bool) or not isinstance(item, int) for item in (observed, available)):
            raise ValueError("可知时刻必须为UTC毫秒整数")
        if not row["close_time"] < observed <= available <= now_ms:
            raise ValueError("观测与可知时刻不能早于收盘或晚于本次发布")
        row.update(session_date=session, source_published_at_ms=published,
                   observed_at_ms=observed, available_at_ms=available, is_final=True,
                   volume_missing=row["volume"] is None)
        prior = unique.get(row["open_time"])
        if prior is not None:
            comparable = {key for key in row if key not in {"observed_at_ms", "available_at_ms"}}
            if any(prior[key] != row[key] for key in comparable):
                raise ValueError("同一开盘时间存在冲突K线；需明确来源修订后发布新版本")
            row = min((prior, row), key=lambda item: item["available_at_ms"])
        unique[row["open_time"]] = row
    result = [unique[key] for key in sorted(unique)]
    if not result:
        raise ValueError("不能发布空数据集")
    if any(right["open_time"] <= left["close_time"] for left, right in zip(result, result[1:])):
        raise ValueError("数据集K线时间范围重叠")
    # Arrow validates decimal precision without silently rounding any value.
    try:
        pa.Table.from_pylist(result, schema=SCHEMA)
    except (pa.ArrowInvalid, pa.ArrowTypeError):
        raise ValueError("行情超出Decimal(38,18)精度") from None
    return result


def _write_bytes(path, body):
    """Atomically publish immutable bytes, rejecting any existing conflicting file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if sha256_file(path) != hashlib.sha256(body).hexdigest():
                raise ValueError("不可变数据文件冲突") from None
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def publish_dataset(db, asset_id, provider_id, interval, rows, provenance, raw_pages, now_ms):
    """Publish one frozen source snapshot; caller commits the metadata transaction.

    Rows use UTC milliseconds and exact decimal OHLCV. Raw pages contain public
    bytes, a fixed endpoint_id and whitelisted params. Equal content returns the
    first observation, even when imported again later; changed source values make
    a new version. Historical availability is never inferred from the bar date.
    """
    if db.get(Asset, asset_id) is None or db.get(Provider, provider_id) is None:
        raise ValueError("发布前必须登记精确产品与数据来源")
    if not _TOKEN.fullmatch(provider_id) or not _TOKEN.fullmatch(interval):
        raise ValueError("来源或周期标识无效")
    source = _public_metadata(provenance, _PROVENANCE_FIELDS)
    if not all(source.get(key) for key in ("price_type", "adjustment", "calendar_version")):
        raise ValueError("需明确价格类型、复权方式与日历版本")
    normalized = _normalize_rows(rows, now_ms)
    raw = []
    for page in raw_pages:
        endpoint = page.get("endpoint_id", "")
        body = page.get("body")
        if not isinstance(endpoint, str) or not _TOKEN.fullmatch(endpoint) or not isinstance(body, bytes):
            raise ValueError("原始快照必须是公开响应字节和固定端点标识")
        raw.append({"endpoint_id": endpoint, "params": _public_metadata(page.get("params", {}), _PARAM_FIELDS),
                    "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body), "body": body,
                    "observed_at_ms": page.get("observed_at_ms", now_ms)})
        observation = raw[-1]["observed_at_ms"]
        if isinstance(observation, bool) or not isinstance(observation, int) or not 0 <= observation <= now_ms:
            raise ValueError("原始页面观测时间无效")
    if not raw:
        raise ValueError("数据集版本必须关联原始来源快照")
    content_rows = [{key: value for key, value in row.items() if key not in {"observed_at_ms", "available_at_ms"}}
                    for row in normalized]
    dataset_hash = hashlib.sha256(_canonical(content_rows)).hexdigest()
    raw_metadata = [{key: value for key, value in page.items() if key not in {"body", "observed_at_ms"}} for page in raw]
    identity = {"asset_id": asset_id, "provider_id": provider_id, "interval": interval,
                "schema_version": SCHEMA_VERSION, "source": source, "dataset_sha256": dataset_hash,
                "raw_pages": raw_metadata}
    version_id = hashlib.sha256(_canonical(identity)).hexdigest()
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"dataset:{version_id}"})
    previous = db.get(DatasetVersion, version_id)
    if previous is not None:
        read_version(previous)
        return previous
    root = DATA_ROOT.resolve()
    partition = (root / "dataset_versions" / provider_id / hashlib.sha256(asset_id.encode()).hexdigest() / version_id).resolve()
    if not partition.is_relative_to(root):
        raise ValueError("研究数据目录越界")
    manifest_path = partition / "manifest.json"
    if manifest_path.is_file():
        # A durable manifest may survive a worker crash before its DB transaction.
        existing_manifest = json.loads(manifest_path.read_bytes())
        if existing_manifest.get("version_id") != version_id:
            raise ValueError("未登记版本清单身份冲突")
        recovered = _version_metadata(existing_manifest, manifest_path)
        read_version(recovered)
        db.add(recovered)
        db.flush()
        return recovered
    shared = partition.parent / "blobs"
    partition.mkdir(parents=True, exist_ok=True)
    for page, metadata in zip(raw, raw_metadata):
        name = f"raw-{page['sha256']}.bin"
        _write_bytes(shared / name, page["body"])
        _link_blob(shared / name, partition / name)
        metadata["file"] = name
        metadata["observed_at_ms"] = page["observed_at_ms"]
    segments = []
    months = {}
    for row in normalized:
        month = datetime.fromtimestamp(row["open_time"] / 1000, timezone.utc).strftime("%Y-%m")
        months.setdefault(month, []).append(row)
    for month, page in months.items():
        content_hash = hashlib.sha256(_canonical(page)).hexdigest()
        blob = shared / f"bars-{month}-{content_hash}.parquet"
        if blob.exists():
            if hashlib.sha256(_canonical(query_parquet_rows([blob], order_column="open_time"))).hexdigest() != content_hash:
                raise ValueError("共享Parquet内容SHA256不匹配")
            digest = sha256_file(blob)
        else:
            blob, digest = write_parquet_immutable(pa.Table.from_pylist(page, schema=SCHEMA), blob)
        path = partition / blob.name
        _link_blob(blob, path)
        segments.append({"file": path.name, "sha256": digest, "rows": len(page)})
    quality = {"status": "VALIDATED", "known_at_basis": "OBSERVED_AT_INGESTION",
               "historical_point_in_time_verified": False, "missing_bars_filled": False,
               "corporate_actions_verified": False, "row_count": len(normalized)}
    quality["missing_volume_rows"] = sum(row["volume_missing"] for row in normalized)
    manifest = {**identity, "version_id": version_id, "observed_at_ms": now_ms,
                "row_count": len(normalized), "first_open_ms": normalized[0]["open_time"],
                "last_close_ms": normalized[-1]["close_time"], "segments": segments, "quality": quality}
    manifest_bytes = _canonical(manifest)
    _write_bytes(manifest_path, manifest_bytes)
    version = _version_metadata(manifest, manifest_path)
    db.add(version)
    db.flush()
    return version


def _link_blob(source, target):
    """Reference a content-addressed blob by hard link; unchanged months are never rewritten."""
    try:
        os.link(source, target)
    except FileExistsError:
        if sha256_file(source) != sha256_file(target):
            raise ValueError("不可变文件引用冲突") from None
    descriptor = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _version_metadata(manifest, manifest_path):
    """Build metadata from an already durable manifest, including crash recovery."""
    source = manifest["source"]
    return DatasetVersion(
        id=manifest["version_id"], asset_id=manifest["asset_id"], provider_id=manifest["provider_id"],
        interval=manifest["interval"], price_type=source["price_type"], adjustment=source["adjustment"],
        schema_version=manifest["schema_version"], calendar_version=source["calendar_version"],
        dataset_sha256=manifest["dataset_sha256"], manifest_sha256=sha256_file(manifest_path),
        manifest_path=manifest_path.relative_to(DATA_ROOT.resolve()).as_posix(), row_count=manifest["row_count"],
        first_open_ms=manifest["first_open_ms"], last_close_ms=manifest["last_close_ms"],
        observed_at=datetime.fromtimestamp(manifest["observed_at_ms"] / 1000, timezone.utc),
        provenance=source, quality=manifest["quality"],
    )


def read_version(version, as_of_ms=None):
    """Verify immutable files and read Decimal bars known by the optional UTC-ms cutoff."""
    if as_of_ms is not None and (isinstance(as_of_ms, bool) or not isinstance(as_of_ms, int) or as_of_ms < 0):
        raise ValueError("as_of必须是非负UTC毫秒整数")
    root = DATA_ROOT.resolve()
    path = (root / version.manifest_path).resolve()
    if not path.is_relative_to(root / "dataset_versions") or not path.is_file():
        raise ValueError("版本清单缺失或目录越界")
    if sha256_file(path) != version.manifest_sha256:
        raise ValueError("版本清单SHA256不匹配")
    manifest = json.loads(path.read_bytes())
    for key in ("version_id", "asset_id", "provider_id", "interval", "schema_version", "dataset_sha256", "row_count", "first_open_ms", "last_close_ms"):
        expected = version.id if key == "version_id" else getattr(version, key)
        if manifest.get(key) != expected:
            raise ValueError("版本清单与数据库身份不匹配")
    paths = []
    for kind in ("raw_pages", "segments"):
        references = manifest.get(kind)
        if not isinstance(references, list) or not references:
            raise ValueError("版本缺少原始快照或Parquet分段")
        for item in references:
            name = item.get("file", "")
            target = (path.parent / name).resolve()
            if Path(name).name != name or target.parent != path.parent or not target.is_file():
                raise ValueError("数据集引用文件缺失或越界")
            if sha256_file(target) != item.get("sha256"):
                raise ValueError("数据集引用文件SHA256不匹配")
            if kind == "segments":
                paths.append(target)
    rows = query_parquet_rows(paths, order_column="open_time")
    if len(rows) != version.row_count:
        raise ValueError("数据集实际行数与版本不一致")
    content = [{key: value for key, value in row.items() if key not in {"observed_at_ms", "available_at_ms"}} for row in rows]
    if hashlib.sha256(_canonical(content)).hexdigest() != version.dataset_sha256:
        raise ValueError("数据集规范内容SHA256不匹配")
    return rows if as_of_ms is None else [row for row in rows if row["available_at_ms"] <= as_of_ms]


def raw_pages_for_version(version):
    """Read verified public raw bytes for an incremental merge; never accept external paths."""
    read_version(version)
    manifest_path = DATA_ROOT.resolve() / version.manifest_path
    manifest = json.loads(manifest_path.read_bytes())
    return [{"body": (manifest_path.parent / item["file"]).read_bytes(),
             "endpoint_id": item["endpoint_id"], "params": item["params"],
             "observed_at_ms": item["observed_at_ms"]} for item in manifest["raw_pages"]]
