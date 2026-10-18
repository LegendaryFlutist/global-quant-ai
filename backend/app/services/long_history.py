"""按需归档已核验USDT永续的长期原生K线，数据仅写本机持久磁盘。"""

import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from app.adapters.public_binance import INTERVALS, BinancePublic
from app.core.config import DATA_ROOT
from app.domains.models import Asset
from app.services.market_data import FIELDS, MINUTES, SCHEMA, bucket_bounds

ARCHIVE_SCHEMA_VERSION = 1
MAX_PAGE_COUNT = 2000
PAGE_LIMIT = 1000


def three_year_start(now_ms):
    """计算now之前三个自然年的UTC起点，闰日映射到2月28日。

    Args:
        now_ms: 当前UTC时间戳，单位毫秒。

    Returns:
        含闭边界的UTC毫秒起始时间。
    """
    now = datetime.fromtimestamp(now_ms / 1000, timezone.utc)
    try:
        start = now.replace(year=now.year - 3)
    except ValueError:
        start = now.replace(year=now.year - 3, day=28)
    return int(start.timestamp() * 1000)


def _validate_identity(asset, interval):
    """限制归档只接受已登记且精确核验过的USDT永续产品。

    Args:
        asset: 当前用户选中的数据库资产记录。
        interval: 币安原生K线周期名称。

    Returns:
        经过检查的(产品代码,归档目录名)二元组。

    Raises:
        ValueError: 产品身份、核验状态或周期不满足公开行情范围。
    """
    if (
        asset is None
        or asset.venue != "BINANCE_USDM"
        or asset.verification not in ("VERIFIED", "VERIFIED_SNAPSHOT")
        or not re.fullmatch(r"[A-Z0-9_]{1,40}", asset.symbol or "")
        or asset.id != "binance_usdm:" + asset.symbol
    ):
        raise ValueError("只允许读取已核验的精确USDT永续产品代码")
    if interval not in INTERVALS:
        raise ValueError("不支持的币安原生K线周期")
    return asset.symbol, asset.symbol


def validate_archive_identity(db, asset_id, interval):
    """校验归档API请求的数据库产品身份及原生周期。

    Args:
        db: 本次请求或后台任务使用的SQLAlchemy会话。
        asset_id: 资产目录中的精确USDT永续ID。
        interval: 币安原生K线周期。

    Returns:
        已核验的精确供应商合约代码。

    Raises:
        ValueError: 资产缺失、未核验或周期超出支持范围。
    """
    asset = db.get(Asset, asset_id)
    symbol, _ = _validate_identity(asset, interval)
    return symbol


def _archive_directory(root, symbol, interval):
    """返回资产与周期隔离的受控归档目录。

    Args:
        root: 项目本地持久行情目录。
        symbol: 已验证的精确合约代码。
        interval: 已验证的原生周期。

    Returns:
        严格位于root目录树下的K线归档目录。
    """
    base = Path(root).resolve()
    target = (base / "long_history" / symbol / interval).resolve()
    if not target.is_relative_to(base):
        raise ValueError("归档目录越界")
    return target


def _interval_end(open_ms, interval):
    """取得单根周期K线的左闭右开结束时间。

    Args:
        open_ms: K线开盘UTC毫秒时间。
        interval: 币安原生周期，包括自然月1M。

    Returns:
        对应K线下一根开盘时间的UTC毫秒值。
    """
    if interval == "1M":
        return bucket_bounds(open_ms, interval)[1]
    return open_ms + MINUTES[interval] * 60_000


def _parse_closed_row(raw, interval, now_ms):
    """将公开REST数组校验为已收盘OHLCV Decimal记录。

    Args:
        raw: 币安K线响应数组；不含用户或账户字段。
        interval: 响应所用的原生K线周期。
        now_ms: 冻结本次采集判断的当前UTC毫秒时间。

    Returns:
        已收盘的类型化Parquet行；正在形成的K线返回None。

    Raises:
        ValueError: 时间边界、OHLC或精度不合法。
    """
    if not isinstance(raw, (list, tuple)) or len(raw) < 7:
        raise ValueError("公开K线响应结构无效")
    try:
        open_ms = int(raw[0])
        close_ms = int(raw[6])
        prices = {key: Decimal(str(value)) for key, value in zip(FIELDS, raw[1:6])}
    except (ValueError, TypeError, InvalidOperation):
        raise ValueError("公开K线时间或Decimal数值无效") from None
    if not all(value.is_finite() for value in prices.values()):
        raise ValueError("公开K线含非有限数值")
    if (
        min(prices[key] for key in FIELDS[:4]) <= 0
        or prices["volume"] < 0
        or prices["high"] < max(prices["open"], prices["close"], prices["low"])
        or prices["low"] > min(prices["open"], prices["close"], prices["high"])
    ):
        raise ValueError("公开K线OHLCV范围无效")
    expected_open, expected_end = bucket_bounds(open_ms, interval)
    if expected_open != open_ms or close_ms != expected_end - 1:
        raise ValueError("公开K线时间与原生周期不一致")
    if close_ms >= now_ms:
        return None
    return {
        "open_time": open_ms,
        **prices,
        "close_time": close_ms,
    }


def _read_manifest(directory, asset_id, symbol, interval):
    """验证归档清单的身份、版本和每个不可变分段的SHA256。

    Args:
        directory: 单一合约及周期专属目录。
        asset_id: 用户请求时的精确数据库资产ID。
        symbol: 已核验的产品代码。
        interval: 归档周期。

    Returns:
        清单字典；无清单时返回None。

    Raises:
        ValueError: 清单字段不匹配、文件路径越界或数据校验失败。
    """
    path = directory / "manifest.json"
    if not path.exists():
        return None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("长期行情清单损坏，已停止读取") from None
    if (
        manifest.get("schema_version") != ARCHIVE_SCHEMA_VERSION
        or manifest.get("asset_id") != asset_id
        or manifest.get("symbol") != symbol
        or manifest.get("interval") != interval
        or not isinstance(manifest.get("segments"), list)
    ):
        raise ValueError("长期行情清单身份或格式不匹配")
    root = directory.resolve()
    for segment in manifest["segments"]:
        name = segment.get("file", "")
        if not name or Path(name).name != name or not name.endswith(".parquet"):
            raise ValueError("长期行情分段路径无效")
        target = (root / name).resolve()
        if target.parent != root or not target.is_file():
            raise ValueError("长期行情分段文件缺失")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest != segment.get("sha256"):
            raise ValueError("长期行情分段SHA256不匹配")
    return manifest


def _atomic_manifest(directory, manifest):
    """以原子替换方式发布清单指针，避免中断暴露半个JSON。

    Args:
        directory: 已创建的长期行情专属目录。
        manifest: 仅含来源、时间范围、分段hash和采集统计的字典。

    Returns:
        写入后的manifest.json路径。
    """
    path = directory / "manifest.json"
    temporary = directory / ("manifest." + uuid.uuid4().hex + ".tmp")
    content = json.dumps(manifest, sort_keys=True, ensure_ascii=False, allow_nan=False)
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    return path


def _persist_page(directory, rows):
    """写只读Parquet页并返回含SHA256的不可变分段元数据。

    Args:
        directory: 单一合约及周期专属目录。
        rows: 已排序、去重且校验过的K线记录。

    Returns:
        分段文件名、首尾时间、行数和SHA256。
    """
    table = pa.Table.from_pylist(rows, schema=SCHEMA)
    temporary = directory / ("segment." + uuid.uuid4().hex + ".tmp")
    pq.write_table(table, temporary, compression="zstd")
    with temporary.open("rb") as stream:
        os.fsync(stream.fileno())
    digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
    name = f"{rows[0]['open_time']}-{rows[-1]['open_time']}-{digest[:16]}.parquet"
    target = directory / name
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            temporary.unlink(missing_ok=True)
            raise ValueError("长期行情文件名冲突")
        temporary.unlink()
    else:
        os.replace(temporary, target)
    return {
        "file": name,
        "first_open_ms": rows[0]["open_time"],
        "last_open_ms": rows[-1]["open_time"],
        "last_close_ms": rows[-1]["close_time"],
        "rows": len(rows),
        "sha256": digest,
    }


def archive_manifest(asset_id, interval, root=None):
    """读取并完整校验本机长期归档摘要，不触发网络请求。

    Args:
        asset_id: `binance_usdm:SYMBOL`精确产品ID。
        interval: 币安原生周期。
        root: 可选本地数据根目录，默认使用持久磁盘。

    Returns:
        清单摘要；归档不存在时返回None。
    """
    if not isinstance(asset_id, str) or not asset_id.startswith("binance_usdm:"):
        raise ValueError("不支持的归档资产ID")
    symbol = asset_id.split(":", 1)[1]
    if not re.fullmatch(r"[A-Z0-9_]{1,40}", symbol or "") or interval not in INTERVALS:
        raise ValueError("不支持的资产或周期")
    base = Path(root) if root is not None else DATA_ROOT
    directory = _archive_directory(base, symbol, interval)
    manifest = _read_manifest(directory, asset_id, symbol, interval)
    if manifest is None:
        return None
    return manifest


def read_archive(asset_id, interval, limit=10000, end_ms=None, root=None):
    """读取校验后的长期行情并输出可视化字符串价格和来源清单。

    Args:
        asset_id: `binance_usdm:SYMBOL`精确产品ID。
        interval: 归档的原生K线周期。
        limit: 返回最近最多K线条数，上限由API层控制。
        end_ms: 可选截止UTC毫秒时间，用于历史分页/回放。
        root: 可选本地数据根目录，默认使用持久磁盘。

    Returns:
        Decimal价格字符串K线、时间范围、SHA256及归档状态。
    """
    if not isinstance(limit, int) or not 1 <= limit <= 10000:
        raise ValueError("归档读取数量必须在1到10000之间")
    if end_ms is not None and (not isinstance(end_ms, int) or end_ms < 0):
        raise ValueError("归档截止时间必须为UTC毫秒时间戳")
    manifest = archive_manifest(asset_id, interval, root)
    if manifest is None:
        return None
    base = Path(root) if root is not None else DATA_ROOT
    directory = _archive_directory(base, manifest["symbol"], interval)
    rows = []
    for segment in manifest["segments"]:
        table = pq.read_table(directory / segment["file"])
        rows.extend(table.to_pylist())
    rows.sort(key=lambda row: row["open_time"])
    if end_ms is not None:
        rows = [row for row in rows if row["close_time"] <= end_ms]
    selected = rows[-limit:]
    digest_input = "".join(item["sha256"] for item in manifest["segments"])
    return {
        "asset_id": asset_id,
        "symbol": manifest["symbol"],
        "interval": interval,
        "candles": [
            {**row, **{field: str(row[field]) for field in FIELDS}}
            for row in selected
        ],
        "available_count": len(rows),
        "first_open_ms": rows[0]["open_time"] if rows else None,
        "last_close_ms": rows[-1]["close_time"] if rows else None,
        "requested_start_ms": manifest["requested_start_ms"],
        "updated_at_ms": manifest["updated_at_ms"],
        "status": manifest["status"],
        "source": manifest["source"],
        "segment_sha256": digest_input,
        "research_only": True,
    }


def fetch_three_year_archive(
    db, asset_id, interval, now_ms=None, adapter=None, page_limit=PAGE_LIMIT, pause=0.25
):
    """从公开REST逐页下载最多三年K线并断点续传到本机不可变分段。

    Args:
        db: 本次队列任务的SQLAlchemy数据库会话。
        asset_id: 经目录核验的精确USDT永续资产ID。
        interval: 币安原生周期名称，包括1M自然月线。
        now_ms: 可选固定UTC毫秒时间，用于确定已收盘边界和测试。
        adapter: 可选公开K线客户端；生产默认固定币安GET适配器。
        page_limit: 分页数上限，不可大于交易所1000根限制。
        pause: 两个非空REST分页间隔秒数，避免打满请求速率。

    Returns:
        数据条数、实际可用范围、分段数、SHA256和`THREE_YEARS`或
        `LISTING_SHORTER_THAN_THREE_YEARS`状态；不更新90天MarketData表。

    Raises:
        ValueError: 身份/周期/行情无效或恢复清单损坏。
        RuntimeError: 分页超过资源上限；已经持久化的分段仍可续传。
    """
    current_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    if not 1 <= page_limit <= PAGE_LIMIT or pause < 0:
        raise ValueError("归档分页或间隔设置无效")
    asset = db.get(Asset, asset_id)
    symbol, _ = _validate_identity(asset, interval)
    directory = _archive_directory(DATA_ROOT, symbol, interval)
    directory.mkdir(parents=True, exist_ok=True)
    manifest = _read_manifest(directory, asset_id, symbol, interval)
    requested_start = three_year_start(current_ms)
    if manifest:
        segments = list(manifest["segments"])
        last_open = max(item["last_open_ms"] for item in segments)
        latest_open = last_open
        first_open = min(item["first_open_ms"] for item in segments)
        cursor = max(requested_start, _interval_end(last_open, interval))
    else:
        segments = []
        latest_open = None
        first_open = None
        cursor = requested_start
    client = adapter or BinancePublic()
    pages = 0
    while cursor < current_ms:
        batch = client.klines(symbol, interval, cursor, current_ms)
        if not batch:
            break
        parsed = []
        for raw in batch:
            row = _parse_closed_row(raw, interval, current_ms)
            if row is not None and (latest_open is None or row["open_time"] > latest_open):
                parsed.append(row)
        if parsed:
            parsed.sort(key=lambda row: row["open_time"])
            if len({row["open_time"] for row in parsed}) != len(parsed):
                raise ValueError("公开K线分页含重复开盘时间")
            segment = _persist_page(directory, parsed)
            segments.append(segment)
            latest_open = segment["last_open_ms"]
            first_open = segment["first_open_ms"] if first_open is None else min(first_open, segment["first_open_ms"])
            row_count = sum(item["rows"] for item in segments)
            manifest = {
                "schema_version": ARCHIVE_SCHEMA_VERSION,
                "asset_id": asset_id,
                "symbol": symbol,
                "interval": interval,
                "requested_start_ms": manifest.get("requested_start_ms", requested_start) if manifest else requested_start,
                "first_open_ms": first_open,
                "last_open_ms": segment["last_open_ms"],
                "last_close_ms": segment["last_close_ms"],
                "row_count": row_count,
                "segments": segments,
                "source": "binance_usdm_public_rest_klines",
                "updated_at_ms": current_ms,
                "status": "PENDING",
            }
            _atomic_manifest(directory, manifest)
        following = int(batch[-1][0])
        if following < cursor:
            raise ValueError("公开K线分页游标倒退，已停止采集")
        cursor = max(_interval_end(following, interval), int(batch[-1][6]) + 1)
        pages += 1
        if pages > MAX_PAGE_COUNT:
            raise RuntimeError("归档分页达到资源上限，已保存进度，可稍后继续")
        if len(batch) < page_limit:
            break
        if pause:
            time.sleep(pause)
    manifest = _read_manifest(directory, asset_id, symbol, interval)
    if manifest is None:
        raise ValueError("币安没有返回可用已收盘历史K线；未生成行情文件")
    last_open = max(item["last_open_ms"] for item in manifest["segments"])
    first_open = min(item["first_open_ms"] for item in manifest["segments"])
    interval_ms = _interval_end(last_open, interval) - last_open
    complete_start = first_open <= _interval_end(requested_start, interval)
    complete_end = manifest["last_close_ms"] >= current_ms - interval_ms
    status = (
        "THREE_YEARS"
        if complete_start and complete_end
        else "LISTING_SHORTER_THAN_THREE_YEARS"
        if not complete_start
        else "PARTIAL"
    )
    manifest.update(
        first_open_ms=first_open,
        last_open_ms=last_open,
        last_close_ms=max(item["last_close_ms"] for item in manifest["segments"]),
        row_count=sum(item["rows"] for item in manifest["segments"]),
        updated_at_ms=current_ms,
        status=status,
    )
    manifest_path = _atomic_manifest(directory, manifest)
    dataset_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    return {
        "asset_id": asset_id,
        "interval": interval,
        "rows": manifest["row_count"],
        "first_open_ms": first_open,
        "last_close_ms": manifest["last_close_ms"],
        "segments": len(manifest["segments"]),
        "dataset_sha256": dataset_hash,
        "status": status,
        "pages_downloaded": pages,
        "source": manifest["source"],
    }
