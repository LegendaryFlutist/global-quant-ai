"""按需下载并不可变保存USDM合约标记价格K线及资金费率。"""

import fcntl
import json
import re
import time
from decimal import Decimal, InvalidOperation
from contextlib import contextmanager
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from app.adapters.public_binance import INTERVALS, BinancePublic
from app.core.config import DATA_ROOT
from app.domains.models import Asset
from app.services.long_history import _validate_identity, three_year_start
from app.services.market_data import bucket_bounds
from app.storage.parquet_files import (
    sha256_file,
    write_content_addressed_parquet,
    write_json_atomic,
)

SCHEMA_VERSION = 1
PAGE_LIMIT = 1000
MAX_PAGES = 2000
MARK_SCHEMA = pa.schema([
    pa.field("open_time", pa.int64()),
    pa.field("open", pa.decimal128(38, 18)),
    pa.field("high", pa.decimal128(38, 18)),
    pa.field("low", pa.decimal128(38, 18)),
    pa.field("close", pa.decimal128(38, 18)),
    pa.field("close_time", pa.int64()),
])
FUNDING_SCHEMA = pa.schema([
    pa.field("funding_time", pa.int64()),
    pa.field("rate", pa.decimal128(38, 18)),
    pa.field("mark_price", pa.decimal128(38, 18)),
])


def _root_directory(root, symbol, interval, kind):
    """构造并限制到数据根目录内的衍生品数据目录。

    Args:
        root: 持久本地行情根目录。
        symbol: 已通过注册表身份校验的合约代码。
        interval: 已核验的Binance原生周期。
        kind: `mark`标记K线或`funding`资金费率。

    Returns:
        资产/周期/数据类型隔离的目录绝对路径。
    """
    if kind not in {"mark", "funding"} or interval not in INTERVALS:
        raise ValueError("衍生品数据类型或周期无效")
    base = Path(root).resolve()
    target = (base / "derivatives" / symbol / interval / kind).resolve()
    if not target.is_relative_to(base):
        raise ValueError("衍生品归档目录越界")
    return target


@contextmanager
def _archive_lock(directory):
    """在单机worker间串行化一个资产周期的双数据流清单修改。

    Args:
        directory: `derivatives/<symbol>/<interval>`数据流父目录。

    Yields:
        无值；文件锁持有期间允许读取/追加本地manifest与分段。

    Notes:
        仅针对当前本地Linux Docker主机；跨主机网络文件系统不属于支持范围。
    """
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".fetch.lock").open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_manifest(directory, asset_id, symbol, interval, kind):
    """读取并验证一个衍生品数据流的清单和每个不可变Parquet分段。

    Args:
        directory: 单一资产、周期、数据类型专属目录。
        asset_id: 市场命名空间资产ID。
        symbol: 注册表精确合约代码。
        interval: Binance原生K线周期。
        kind: `mark`或`funding`数据类型。

    Returns:
        已验证的清单；未采集时返回None。

    Raises:
        ValueError: 身份、文件路径、分段哈希或文件内容无效。
    """
    path = directory / "manifest.json"
    if not path.exists():
        return None
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if (manifest.get("schema_version") != SCHEMA_VERSION
            or manifest.get("asset_id") != asset_id
            or manifest.get("symbol") != symbol
            or manifest.get("interval") != interval
            or manifest.get("kind") != kind
            or not isinstance(manifest.get("segments"), list)):
        raise ValueError("衍生品归档清单身份或格式无效")
    for segment in manifest["segments"]:
        name = segment.get("file", "")
        if not name or Path(name).name != name or not name.endswith(".parquet"):
            raise ValueError("衍生品分段路径无效")
        file_path = directory / name
        if not file_path.is_file() or sha256_file(file_path) != segment.get("sha256"):
            raise ValueError("衍生品分段缺失或SHA256校验失败")
    return manifest


def _atomic_manifest(directory, manifest):
    """原子替换JSON清单，避免中断时暴露半写入的索引文件。

    Args:
        directory: 已创建的单一数据流目录。
        manifest: 可JSON序列化且已验证的数据集索引。

    Returns:
        无返回值；清单通过同目录临时文件替换。
    """
    write_json_atomic(directory / "manifest.json", manifest, compact=True)


def _persist_page(directory, rows, kind):
    """把单页行情写为内容寻址的不可变Parquet分段。

    Args:
        directory: 单一数据流的本地持久目录。
        rows: 已验证的标记K线或资金费率字典。
        kind: 决定Parquet列类型的`mark`或`funding`。

    Returns:
        含时间边界、记录数和SHA256的分段清单项。
    """
    schema = MARK_SCHEMA if kind == "mark" else FUNDING_SCHEMA
    table = pa.Table.from_pylist(rows, schema=schema)
    first_key = "open_time" if kind == "mark" else "funding_time"
    last_key = "close_time" if kind == "mark" else "funding_time"
    start, end = rows[0][first_key], rows[-1][last_key]
    target, digest = write_content_addressed_parquet(
        table,
        directory,
        prefix=f"{start}-{end}",
    )
    return {
        "file": target.name,
        "rows": len(rows),
        "first_time_ms": start,
        "last_time_ms": end,
        "sha256": digest,
    }


def _manifest_value(asset_id, symbol, interval, kind, segments, start_ms, end_ms, updated_ms):
    """生成不含用户凭据的版本化衍生品数据清单。

    Args:
        asset_id: 精确市场命名空间资产ID。
        symbol: Binance永续合约代码。
        interval: mark数据使用的原生周期。
        kind: 清单所描述的衍生品数据类型。
        segments: 不可变Parquet分段索引。
        start_ms: 用户选择的左边界UTC毫秒。
        end_ms: 用户选择的右边界UTC毫秒。
        updated_ms: 本次成功分页检查时刻UTC毫秒。

    Returns:
        可原子写入磁盘的schema版本化清单字典。
    """
    first = min((item["first_time_ms"] for item in segments), default=None)
    last = max((item["last_time_ms"] for item in segments), default=None)
    return {
        "schema_version": SCHEMA_VERSION,
        "asset_id": asset_id,
        "symbol": symbol,
        "interval": interval,
        "kind": kind,
        "requested_start_ms": start_ms,
        "requested_end_ms": end_ms,
        "first_time_ms": first,
        "last_time_ms": last,
        "row_count": sum(item["rows"] for item in segments),
        "segments": segments,
        "source": f"binance_usdm_public_{'mark_price_klines' if kind == 'mark' else 'funding_rate'}",
        "updated_at_ms": updated_ms,
    }


def _parse_mark_row(raw, interval, end_ms):
    """校验Binance标记价格数组并排除未收盘K线。

    Args:
        raw: Binance markPriceKlines返回的一行数组。
        interval: 请求使用的原生周期。
        end_ms: 用户选择的右边界UTC毫秒。

    Returns:
        精确Decimal价格和毫秒时间戳字典；未收盘或越界行返回None。

    Raises:
        ValueError: 数组结构、时间桶或OHLC边界无效。
    """
    if not isinstance(raw, (list, tuple)) or len(raw) < 7:
        raise ValueError("Binance标记价格K线结构无效")
    try:
        open_ms = int(raw[0])
        close_ms = int(raw[6])
        values = {key: Decimal(str(value)) for key, value in zip(("open", "high", "low", "close"), raw[1:5])}
    except (ValueError, TypeError, InvalidOperation):
        raise ValueError("标记价格K线时间或Decimal价格无效") from None
    if not all(value.is_finite() and value > 0 for value in values.values()):
        raise ValueError("标记价格K线含非有限或非正价格")
    if values["high"] < max(values["open"], values["low"], values["close"]):
        raise ValueError("标记价格K线最高价边界无效")
    if values["low"] > min(values["open"], values["high"], values["close"]):
        raise ValueError("标记价格K线最低价边界无效")
    bucket_start, bucket_end = bucket_bounds(open_ms, interval)
    if open_ms != bucket_start or close_ms != bucket_end - 1:
        raise ValueError("标记价格K线与原生周期边界不一致")
    if close_ms >= end_ms:
        return None
    return {"open_time": open_ms, **values, "close_time": close_ms}


def _parse_funding_row(raw, start_ms, end_ms):
    """校验公开资金结算记录的UTC时间、费率和交易所标记价格。

    Args:
        raw: Binance fundingRate端点返回的JSON字典。
        start_ms: 本次用户请求左边界UTC毫秒。
        end_ms: 本次用户请求右边界UTC毫秒。

    Returns:
        精确资金费率记录；超出左闭右开查询窗的行返回None。

    Raises:
        ValueError: 时间、资金费率或标记价格格式无效。
    """
    if not isinstance(raw, dict):
        raise ValueError("Binance资金费率记录结构无效")
    try:
        funding_time = int(raw["fundingTime"])
        rate = Decimal(str(raw["fundingRate"]))
        mark_price = Decimal(str(raw["markPrice"]))
    except (KeyError, ValueError, TypeError, InvalidOperation):
        raise ValueError("资金费率时间或Decimal字段无效") from None
    if not rate.is_finite() or not mark_price.is_finite() or mark_price <= 0:
        raise ValueError("资金费率或标记价格含非有限数值")
    if not start_ms <= funding_time < end_ms:
        return None
    return {"funding_time": funding_time, "rate": rate, "mark_price": mark_price}


def _fetch_stream(directory, asset_id, symbol, interval, kind, start_ms, end_ms, adapter, now_ms, pause):
    """分页读取一个衍生品公开数据流并在每页后保存断点。

    Args:
        directory: 当前mark或funding数据流目录。
        asset_id: 精确市场命名空间资产ID。
        symbol: 注册表核验的合约代码。
        interval: mark K线使用的原生周期。
        kind: `mark`或`funding`。
        start_ms: 请求左边界UTC毫秒。
        end_ms: 请求右边界UTC毫秒。
        adapter: 仅GET公开接口客户端。
        now_ms: 固定本轮采集时刻，用于过滤未收盘mark K线。
        pause: 非空页之间的限速等待秒数。

    Returns:
        清单数据和本次分页数；每页已写入不可变分段，可在异常后续传。

    Raises:
        ValueError: 清单或供应商数据无效；RuntimeError: 请求页数超资源上限。
    """
    directory.mkdir(parents=True, exist_ok=True)
    manifest = _read_manifest(directory, asset_id, symbol, interval, kind)
    if manifest:
        old_start, old_end = manifest["requested_start_ms"], manifest["requested_end_ms"]
        if start_ms < old_start or start_ms > old_end or end_ms < old_start:
            raise ValueError("新请求必须与本地衍生品数据窗重叠且不能向更早时间扩展")
        start_ms, end_ms = old_start, max(old_end, end_ms)
    segments = list(manifest["segments"]) if manifest else []
    key = "open_time" if kind == "mark" else "funding_time"
    last = max((item["last_time_ms"] for item in segments), default=None)
    cursor = start_ms if last is None else max(
        start_ms,
        last + 1 if kind == "funding" else bucket_bounds(last, interval)[1],
    )
    pages = 0
    while cursor < end_ms:
        if kind == "mark":
            batch = adapter.mark_price_klines(symbol, interval, cursor, end_ms)
        else:
            batch = adapter.funding_rates(symbol, cursor, end_ms)
        pages += 1
        if pages > MAX_PAGES:
            raise RuntimeError("衍生品历史请求超过分页资源上限；已保存页面可续传")
        if not batch:
            break
        parser = _parse_mark_row if kind == "mark" else _parse_funding_row
        parsed = []
        for raw in batch:
            row = parser(raw, interval, min(end_ms, now_ms)) if kind == "mark" else parser(raw, start_ms, end_ms)
            if row is not None and (last is None or row[key] > last):
                parsed.append(row)
        parsed.sort(key=lambda row: row[key])
        if len({row[key] for row in parsed}) != len(parsed):
            raise ValueError("衍生品公开响应含重复时间戳")
        if parsed:
            segment = _persist_page(directory, parsed, kind)
            segments.append(segment)
            last = segment["last_time_ms"]
            manifest_value = _manifest_value(asset_id, symbol, interval, kind, segments, start_ms, end_ms, now_ms)
            _atomic_manifest(directory, manifest_value)
        following = int(batch[-1][0] if kind == "mark" else batch[-1]["fundingTime"])
        if following < cursor:
            raise ValueError("衍生品历史分页游标倒退")
        cursor = bucket_bounds(following, interval)[1] if kind == "mark" else following + 1
        if len(batch) < PAGE_LIMIT:
            break
        if pause:
            time.sleep(pause)
    result = _manifest_value(asset_id, symbol, interval, kind, segments, start_ms, end_ms, now_ms)
    _atomic_manifest(directory, result)
    result["dataset_sha256"] = _manifest_hash(directory)
    return result, pages


def _manifest_hash(directory):
    """计算已经落盘manifest.json的SHA256数据集指纹。

    Args:
        directory: 含已原子写入manifest.json的数据流目录。

    Returns:
        64位小写SHA256十六进制字符串。
    """
    return sha256_file(directory / "manifest.json")


def fetch_derivative_history(db, asset_id, interval, start_ms, end_ms, now_ms=None, adapter=None, pause=0.25):
    """按用户选定时间范围读取标记价格K线和结算资金费率并保存本地。

    Args:
        db: 当前持久任务使用的SQLAlchemy会话。
        asset_id: 已核验Binance USDM精确资产ID。
        interval: 标记价格K线原生周期。
        start_ms: 用户选定的左边界UTC毫秒；最多三年历史。
        end_ms: 用户选定的右边界UTC毫秒且必须晚于起点。
        now_ms: 冻结本轮时刻，主要用于合成数据测试。
        adapter: 可注入的仅公开GET适配器。
        pause: 分页请求间隔秒数。

    Returns:
        两个本地数据流的范围、SHA256、行数和来源，以及research_only标记。

    Raises:
        ValueError: 资产、时间范围、周期或供应商数据不合法。
    """
    current_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    if interval not in INTERVALS or not isinstance(start_ms, int) or not isinstance(end_ms, int):
        raise ValueError("标记K线周期或UTC毫秒范围无效")
    if start_ms < three_year_start(current_ms) or end_ms <= start_ms or end_ms > current_ms:
        raise ValueError("只允许采集最近三年内已发生的数据")
    if pause < 0:
        raise ValueError("分页间隔不能为负数")
    asset = db.get(Asset, asset_id)
    symbol, _ = _validate_identity(asset, interval)
    if not re.fullmatch(r"[A-Z0-9_]{1,40}", symbol):
        raise ValueError("资产代码不符合归档路径要求")
    client = adapter or BinancePublic()
    root = DATA_ROOT
    mark_dir = _root_directory(root, symbol, interval, "mark")
    funding_dir = _root_directory(root, symbol, interval, "funding")
    with _archive_lock(mark_dir.parent):
        mark, mark_pages = _fetch_stream(mark_dir, asset_id, symbol, interval, "mark", start_ms, end_ms, client, current_ms, pause)
        funding, funding_pages = _fetch_stream(funding_dir, asset_id, symbol, interval, "funding", start_ms, end_ms, client, current_ms, pause)
    return {
        "asset_id": asset_id,
        "symbol": symbol,
        "interval": interval,
        "mark_price": mark,
        "funding": funding,
        "pages": {"mark": mark_pages, "funding": funding_pages},
        "research_only": True,
    }


def read_derivative_history(asset_id, interval, start_ms=None, end_ms=None, root=None):
    """校验并读取本地衍生品归档，不触发外部网络请求。

    Args:
        asset_id: 归档中固定的市场命名空间资产ID。
        interval: Binance原生标记K线周期。
        start_ms: 可选的左边界UTC毫秒。
        end_ms: 可选的右边界UTC毫秒，闭合价格K线包含收盘时刻。
        root: 隔离测试使用的可选本地数据根目录。

    Returns:
        按UTC时间排序的标记K线/资金费率记录、各自数据hash和来源；缺档流为None。

    Raises:
        ValueError: 目录、身份、数据清单或SHA256校验失败。
    """
    if not asset_id.startswith("binance_usdm:"):
        raise ValueError("资产ID必须明确为Binance USDM产品")
    symbol = asset_id.removeprefix("binance_usdm:")
    if not re.fullmatch(r"[A-Z0-9_]{1,40}", symbol) or interval not in INTERVALS:
        raise ValueError("本地衍生品归档身份无效")
    base = DATA_ROOT if root is None else Path(root)
    output = {}
    for kind, schema in (("mark", MARK_SCHEMA), ("funding", FUNDING_SCHEMA)):
        directory = _root_directory(base, symbol, interval, kind)
        manifest = _read_manifest(directory, asset_id, symbol, interval, kind)
        if manifest is None:
            output[kind] = None
            continue
        rows = []
        for segment in manifest["segments"]:
            rows.extend(pq.read_table(directory / segment["file"], schema=schema).to_pylist())
        time_key = "open_time" if kind == "mark" else "funding_time"
        rows.sort(key=lambda row: row[time_key])
        if start_ms is not None:
            rows = [row for row in rows if row[time_key] >= start_ms]
        if end_ms is not None:
            end_key = "close_time" if kind == "mark" else "funding_time"
            rows = [row for row in rows if row[end_key] <= end_ms]
        output[kind] = {
            "rows": [{key: str(value) if isinstance(value, Decimal) else value for key, value in row.items()} for row in rows],
            "manifest_sha256": _manifest_hash(directory),
            "segment_sha256": [item["sha256"] for item in manifest["segments"]],
            "source": manifest["source"],
            "available_count": len(rows),
        }
    return {"asset_id": asset_id, "interval": interval, **output, "research_only": True}
