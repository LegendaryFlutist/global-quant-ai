"""将选中合约的最新公开K线追加到长期缓存并保存未收盘快照。"""

import hashlib
import json
import os
import time
import uuid
from decimal import Decimal, InvalidOperation
from functools import wraps
from pathlib import Path
from threading import Lock

from app.adapters.public_binance import BinancePublic, INTERVALS
from app.core.config import DATA_ROOT
from app.domains.models import Asset
from app.services.long_history import (
    _archive_directory,
    _interval_end,
    _persist_page,
    _read_manifest,
    _validate_identity,
    _atomic_manifest,
)
from app.services.market_data import FIELDS, bucket_bounds

_REFRESH_LOCK = Lock()


def _single_refresh(function):
    """Serialize refresh calls inside the API process to prevent segment races.

    Args:
        function: Synchronous current-market refresh operation.

    Returns:
        A wrapped function that performs one local append at a time per API process.
    """
    @wraps(function)
    def guarded(*args, **kwargs):
        """Acquire the process lock before reading/writing the mutable manifest.

        Args:
            *args: Positional refresh operation parameters.
            **kwargs: Keyword refresh operation parameters.

        Returns:
            The wrapped public refresh result after the lock is released.
        """
        with _REFRESH_LOCK:
            return function(*args, **kwargs)

    return guarded


def _parse_live_row(raw, interval, now_ms):
    """校验Binance实时K线并区分未收盘快照和已收盘数据。

    Args:
        raw: Binance公开K线数组，不含用户、持仓或账户内容。
        interval: 已核验的原生K线周期。
        now_ms: 冻结本次读取的UTC毫秒时刻。

    Returns:
        K线字典，字段含Decimal价格、`is_closed`和真实UTC边界。

    Raises:
        ValueError: OHLCV、时间戳或原生周期边界不合法。
    """
    if not isinstance(raw, (list, tuple)) or len(raw) < 7:
        raise ValueError("公开K线响应结构无效")
    try:
        open_ms, close_ms = int(raw[0]), int(raw[6])
        prices = {key: Decimal(str(value)) for key, value in zip(FIELDS, raw[1:6])}
    except (TypeError, ValueError, InvalidOperation):
        raise ValueError("公开K线时间或数值无效") from None
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
    if expected_open != open_ms or close_ms != expected_end - 1 or open_ms > now_ms:
        raise ValueError("公开K线时间与原生周期不一致")
    return {"open_time": open_ms, **prices, "close_time": close_ms, "is_closed": close_ms < now_ms}


def _write_live_snapshot(directory, asset_id, symbol, interval, candle, fetched_at_ms):
    """原子保存最近一次未收盘报价，供断网时标为陈旧快照。

    Args:
        directory: 当前精确合约/周期的长期归档目录。
        asset_id: 已核验的数据库产品ID。
        symbol: 与目录一致的精确Binance合约代码。
        interval: 交易所原生K线周期。
        candle: 仅本地显示的实时快照；不纳入闭合结构信号。
        fetched_at_ms: 读取时间，UTC毫秒。

    Returns:
        已原子替换的live.json路径。
    """
    path = directory / "live.json"
    temporary = directory / ("live." + uuid.uuid4().hex + ".tmp")
    payload = {
        "schema_version": 1,
        "asset_id": asset_id,
        "symbol": symbol,
        "interval": interval,
        "fetched_at_ms": fetched_at_ms,
        "source": "binance_usdm_public_rest_klines",
        "candle": {
            **candle,
            **{field: str(candle[field]) for field in FIELDS},
        },
    }
    canonical = json.dumps(payload, sort_keys=True, allow_nan=False).encode("utf-8")
    payload["sha256"] = hashlib.sha256(canonical).hexdigest()
    temporary.write_text(json.dumps(payload, sort_keys=True, allow_nan=False), encoding="utf-8")
    with temporary.open("rb") as stream:
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    return path


def read_live_snapshot(asset_id, interval, root=None):
    """读取并校验单一周期的临时实时快照，不发起网络请求。

    Args:
        asset_id: 已核验的`binance_usdm:SYMBOL`产品ID。
        interval: 币安原生K线周期。
        root: 可选本地数据根目录，便于使用隔离测试路径。

    Returns:
        本地实时快照，或无快照时返回None；快照始终标为研究用途。

    Raises:
        ValueError: 产品标识、周期或快照格式不匹配。
    """
    if not isinstance(asset_id, str) or not asset_id.startswith("binance_usdm:") or interval not in INTERVALS:
        raise ValueError("不支持的实时行情资产或周期")
    symbol = asset_id.split(":", 1)[1]
    base = Path(root) if root is not None else DATA_ROOT
    path = _archive_directory(base, symbol, interval) / "live.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("本地实时快照损坏") from None
    if (
        payload.get("schema_version") != 1
        or payload.get("asset_id") != asset_id
        or payload.get("symbol") != symbol
        or payload.get("interval") != interval
    ):
        raise ValueError("本地实时快照产品身份不匹配")
    stored_digest = payload.pop("sha256", None)
    canonical = json.dumps(payload, sort_keys=True, allow_nan=False).encode("utf-8")
    if not stored_digest or hashlib.sha256(canonical).hexdigest() != stored_digest:
        raise ValueError("本地实时快照SHA256校验失败")
    payload["sha256"] = stored_digest
    payload["research_only"] = True
    payload["stale"] = True
    return payload


def _append_closed_rows(directory, manifest, rows, now_ms):
    """以新不可变分段追加已收盘K线并原子更新归档清单。

    Args:
        directory: 当前合约与周期的归档目录。
        manifest: 已身份校验的当前归档清单。
        rows: 按开盘UTC毫秒升序且晚于清单尾部的已闭合K线。
        now_ms: 本次更新时刻，UTC毫秒。

    Returns:
        新分段记录列表；没有新增闭合K线时为空。
    """
    if not rows:
        return []
    segments = list(manifest["segments"])
    additions = []
    for offset in range(0, len(rows), 1000):
        clean_rows = [
            {key: value for key, value in row.items() if key != "is_closed"}
            for row in rows[offset : offset + 1000]
        ]
        segment = _persist_page(directory, clean_rows)
        segments.append(segment)
        additions.append(segment)
    last = additions[-1]
    manifest.update(
        first_open_ms=min(manifest["first_open_ms"], additions[0]["first_open_ms"]),
        last_open_ms=last["last_open_ms"],
        last_close_ms=last["last_close_ms"],
        row_count=sum(segment["rows"] for segment in segments),
        segments=segments,
        updated_at_ms=now_ms,
        status=manifest.get("status", "PENDING"),
    )
    _atomic_manifest(directory, manifest)
    return additions


@_single_refresh
def refresh_latest_candle(db, asset_id, interval, now_ms=None, adapter=None, root=None):
    """拉取当前选中周期的最新公开K线并持久追加已收盘数据。

    Args:
        db: 当前认证请求的SQLAlchemy会话。
        asset_id: 资产目录中已精确核验的USDT永续产品ID。
        interval: 当前图表选中的Binance原生周期。
        now_ms: 可选本次观察的UTC毫秒时刻，供无网络确定性测试使用。
        adapter: 可选公开REST适配器；生产环境固定为无密钥GET客户端。
        root: 可选持久数据根目录，测试时指向隔离临时路径。

    Returns:
        新闭合K线、当前未闭合K线、持久归档指纹和采集时间；不生成信号。

    Raises:
        ValueError: 标的/周期未验证、长期归档未先建立或行情无效。

    Notes:
        仅未闭合快照会被live.json替换；闭合K线追加到不可变Parquet分段。
        未闭合K线不能进入技术结构或回测，也不会覆盖此前已闭合行情。
    """
    current_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    if interval not in INTERVALS:
        raise ValueError("不支持的币安原生K线周期")
    asset = db.get(Asset, asset_id)
    symbol, _ = _validate_identity(asset, interval)
    base = Path(root) if root is not None else DATA_ROOT
    directory = _archive_directory(base, symbol, interval)
    manifest = _read_manifest(directory, asset_id, symbol, interval, verify_segments=False)
    if manifest is None or not manifest.get("segments"):
        raise ValueError("请先按需建立本地长期归档后再开启实时更新")
    latest_open = max(segment["last_open_ms"] for segment in manifest["segments"])
    client = adapter or BinancePublic()
    raw_rows = client.klines(symbol, interval, latest_open, current_ms)
    parsed = [_parse_live_row(raw, interval, current_ms) for raw in raw_rows]
    parsed.sort(key=lambda row: row["open_time"])
    if len({row["open_time"] for row in parsed}) != len(parsed):
        raise ValueError("公开K线分页含重复开盘时间")
    closed = [row for row in parsed if row["is_closed"] and row["open_time"] > latest_open]
    live = next((row for row in reversed(parsed) if not row["is_closed"]), None)
    additions = _append_closed_rows(directory, manifest, closed, current_ms)
    if live is not None:
        _write_live_snapshot(directory, asset_id, symbol, interval, live, current_ms)
    directory_manifest_sha = hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest()
    return {
        "asset_id": asset_id,
        "symbol": symbol,
        "interval": interval,
        "closed_candles": [
            {**row, **{field: str(row[field]) for field in FIELDS}} for row in closed
        ],
        "live_candle": (
            {**live, **{field: str(live[field]) for field in FIELDS}} if live is not None else None
        ),
        "last_closed_at_ms": manifest.get("last_close_ms"),
        "updated_at_ms": current_ms,
        "archive_sha256": directory_manifest_sha,
        "new_segments": additions,
        "source": "binance_usdm_public_rest_klines",
        "research_only": True,
        "stale": False,
    }
