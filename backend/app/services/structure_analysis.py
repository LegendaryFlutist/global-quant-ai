"""读取已落盘历史并调用统一因果多周期结构分析器。"""

import time
from decimal import Decimal

import pandas as pd

from app.adapters.public_binance import INTERVALS
from app.services.long_history import read_archive, validate_archive_identity
from app.services.market_data import candles
from app.domains.models import Asset
from app.services.underlying_market import _source_for_asset
from quant_lab.analysis.market_structure import multitimeframe_structure

STRUCTURE_INTERVALS = tuple(INTERVALS)


def _display_decimal(value):
    """把分析计算浮点价位转换为稳定十进制显示字符串。

    Args:
        value: 趋势线或K线价格浮点近似值，不作为账本金额。

    Returns:
        十进制纯字符串；非有限/非数值字段原样保留供上层处理。
    """
    if isinstance(value, float):
        return format(Decimal(str(value)).normalize(), "f")
    return value


def _serialize_structure(value, price_key=None):
    """递归清理结构报告类型并把价格字段序列化为Decimal字符串。

    Args:
        value: 已验证结构分析器生成的字典、列表或标量。
        price_key: 当前对象字段名，用来识别报价币价格和斜率。

    Returns:
        可由FastAPI编码的字典/列表/标量，不写数据库或改写归档。
    """
    money_keys = {"price", "close", "value", "invalidation", "invalidation_price", "slope_per_bar"}
    if isinstance(value, dict):
        return {key: _serialize_structure(item, key) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize_structure(item, price_key) for item in value]
    if price_key in money_keys and isinstance(value, (int, float)) and not isinstance(value, bool):
        return _display_decimal(float(value))
    return value


def analyze_archived_structure(db, asset_id, intervals, as_of_ms=None, limit=10000):
    """为一个已登记研究产品读取本地来源数据并分析多个已收盘周期。

    Args:
        db: 当前认证API请求使用的SQLAlchemy数据库会话。
        asset_id: 资产目录中的精确产品ID；永续与底层证券保留独立身份。
        intervals: 要共同观察且已归档的原生周期名称序列。
        as_of_ms: 可选的统一UTC毫秒回放截止时间。
        limit: 每周期读取的最近最多K线条数，单位为根数。

    Returns:
        因果结构事件、趋势线、波浪候选、背离与缺失周期；不生成订单。

    Raises:
        ValueError: 资产身份、周期、截止或读取上限不符合研究边界。

    Notes:
        只读本地长期归档/Parquet，不触发外部请求。时间截止之后和未收盘K线不参与结构事件。
    """
    if not 1 <= len(intervals) <= len(STRUCTURE_INTERVALS) or len(set(intervals)) != len(intervals):
        raise ValueError("请选择不重复的一个或多个原生周期")
    if not 1 <= limit <= 200000:
        raise ValueError("每周期读取条数必须在1到200000之间")
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise ValueError("研究资产未登记")
    is_binance = asset.venue == "BINANCE_USDM"
    if not is_binance and _source_for_asset(asset).get("kind") != "candles":
        raise ValueError("标量能源参考序列不生成K线结构信号")
    frames = {}
    source_hashes = {}
    missing = []
    for interval in intervals:
        archive = None
        if is_binance:
            validate_archive_identity(db, asset_id, interval)
            archive = read_archive(asset_id, interval, limit=limit, end_ms=as_of_ms)
        if archive is None:
            try:
                cached = candles(db, asset_id, interval, limit=limit, end_ms=as_of_ms)
            except ValueError:
                missing.append(interval)
                continue
            frames[interval] = pd.DataFrame(cached["candles"])
            source_hashes[interval] = cached["dataset_sha256"]
            continue
        frames[interval] = pd.DataFrame(archive["candles"])
        source_hashes[interval] = archive["dataset_sha256"]
    if not frames:
        return {
            "schema_version": 1,
            "algorithm": "confirmed_structure_divergence_v1",
            "as_of_ms": int(as_of_ms if as_of_ms is not None else time.time() * 1000),
            "timeframes": {},
            "events": [],
            "missing_archives": missing,
            "research_only": True,
        }
    effective_as_of = int(as_of_ms if as_of_ms is not None else time.time() * 1000)
    report = multitimeframe_structure(frames, effective_as_of, source_hashes)
    report["missing_archives"] = missing
    report["research_only"] = True
    return _serialize_structure(report)
