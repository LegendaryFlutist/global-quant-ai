"""验证标记价格与资金费率数据隔离归档和断点恢复。"""

import hashlib
import json

import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.domains.models import Asset
from app.services import derivative_history


class _PublicDerivativeAdapter:
    """提供可分页的合成Binance公开衍生品响应，不访问网络。"""

    def __init__(self, marks, funding, fail_funding=False):
        """保存合成响应及可选的资金端点中断位置。"""
        self.marks = marks
        self.funding = funding
        self.fail_funding = fail_funding
        self.mark_calls = 0
        self.funding_calls = 0

    def mark_price_klines(self, symbol, interval, start, end):
        """返回指定UTC范围中的合成标记价格K线。"""
        self.mark_calls += 1
        return [row for row in self.marks if start <= row[0] < end]

    def funding_rates(self, symbol, start, end):
        """返回指定UTC范围中的合成资金费率记录。"""
        self.funding_calls += 1
        if self.fail_funding:
            raise OSError("injected funding endpoint outage")
        return [row for row in self.funding if start <= row["fundingTime"] < end]


def _fixture_rows(start_ms, count=3):
    """构造对齐的15分钟标记K线与每八小时资金结算样例。

    Args:
        start_ms: 首根K线UTC毫秒开盘时刻，必须对齐15分钟桶。
        count: 合成标记K线根数。

    Returns:
        Binance公开REST结构形状的K线数组和资金费率字典列表。
    """
    step = 15 * 60_000
    marks = []
    for index in range(count):
        open_time = start_ms + index * step
        close_time = open_time + step - 1
        price = str(100 + index)
        marks.append([open_time, price, str(102 + index), str(99 + index), price, "0", close_time])
    funding = [{
        "symbol": "BTCUSDT",
        "fundingTime": start_ms + step,
        "fundingRate": "0.00010000",
        "markPrice": "101.25000000",
    }]
    return marks, funding


def test_derivative_history_persists_decimal_mark_and_funding_with_hash(tmp_path, monkeypatch):
    """验证两个公开数据流分开持久化、读取精确Decimal字符串并留存哈希。

    Args:
        tmp_path: pytest提供的隔离临时归档目录。
        monkeypatch: pytest提供的依赖替换器，避免写用户行情路径。

    Returns:
        无返回值；价格/费率精度、来源、行数或文件SHA256错误会失败。
    """
    now_ms = 1_800_000_000_000
    step = 15 * 60_000
    start_ms = (now_ms // step - 3) * step
    end_ms = now_ms
    marks, funding = _fixture_rows(start_ms)
    client = _PublicDerivativeAdapter(marks, funding)
    monkeypatch.setattr(derivative_history, "DATA_ROOT", tmp_path)
    with SessionLocal() as db:
        asset = db.scalar(select(Asset).where(Asset.id == "binance_usdm:BTCUSDT"))
        asset.verification = "VERIFIED"
        db.commit()
        result = derivative_history.fetch_derivative_history(
            db, asset.id, "15m", start_ms, end_ms, now_ms=now_ms, adapter=client, pause=0,
        )

    loaded = derivative_history.read_derivative_history(asset.id, "15m", root=tmp_path)
    assert result["research_only"] is True
    assert result["mark_price"]["source"] == "binance_usdm_public_mark_price_klines"
    assert result["funding"]["source"] == "binance_usdm_public_funding_rate"
    assert loaded["mark"]["available_count"] == 3
    assert loaded["mark"]["rows"][0]["close"] == "100.000000000000000000"
    assert loaded["funding"]["rows"][0]["rate"] == "0.000100000000000000"
    manifest_path = derivative_history._root_directory(tmp_path, "BTCUSDT", "15m", "mark") / "manifest.json"
    assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == loaded["mark"]["manifest_sha256"]

    added_marks, added_funding = _fixture_rows(end_ms, count=1)
    added_funding[0]["fundingTime"] = end_ms
    with SessionLocal() as db:
        derivative_history.fetch_derivative_history(
            db, "binance_usdm:BTCUSDT", "15m", start_ms + step, end_ms + step,
            now_ms=end_ms + step, adapter=_PublicDerivativeAdapter(added_marks, added_funding), pause=0,
        )
    extended = derivative_history.read_derivative_history(asset.id, "15m", root=tmp_path)
    assert extended["mark"]["available_count"] == 4
    assert extended["funding"]["available_count"] == 2


def test_derivative_history_retry_resumes_mark_pages_after_funding_failure(tmp_path, monkeypatch):
    """验证资金接口中断后保留已落盘标记页并从资金流单独续传。

    Args:
        tmp_path: pytest提供的隔离临时归档目录。
        monkeypatch: pytest提供的依赖替换器，避免接触用户持久行情。

    Returns:
        无返回值；断点丢失或重复写标记K线时测试失败。
    """
    now_ms = 1_800_000_000_000
    step = 15 * 60_000
    start_ms = (now_ms // step - 3) * step
    marks, funding = _fixture_rows(start_ms)
    monkeypatch.setattr(derivative_history, "DATA_ROOT", tmp_path)
    with SessionLocal() as db:
        asset = db.scalar(select(Asset).where(Asset.id == "binance_usdm:BTCUSDT"))
        asset.verification = "VERIFIED"
        db.commit()
        with pytest.raises(OSError, match="injected funding"):
            derivative_history.fetch_derivative_history(
                db, asset.id, "15m", start_ms, now_ms, now_ms=now_ms,
                adapter=_PublicDerivativeAdapter(marks, funding, fail_funding=True), pause=0,
            )
        first = derivative_history.read_derivative_history(asset.id, "15m", root=tmp_path)
        assert first["mark"]["available_count"] == 3
        assert first["funding"] is None
        resumed_client = _PublicDerivativeAdapter(marks, funding)
        derivative_history.fetch_derivative_history(
            db, asset.id, "15m", start_ms, now_ms, now_ms=now_ms,
            adapter=resumed_client, pause=0,
        )

    resumed = derivative_history.read_derivative_history(asset.id, "15m", root=tmp_path)
    assert resumed["mark"]["available_count"] == 3
    assert resumed["funding"]["available_count"] == 1
    assert resumed_client.mark_calls <= 1


def test_derivative_history_rejects_modified_segment_and_out_of_range_data(tmp_path, monkeypatch):
    """验证损坏的分段哈希和不在用户时间窗内的资金行不会被采信。

    Args:
        tmp_path: pytest提供的隔离临时归档目录。
        monkeypatch: pytest提供的依赖替换器，替换安全归档根目录。

    Returns:
        无返回值；损坏文件或非法范围被接受时测试失败。
    """
    now_ms = 1_800_000_000_000
    step = 15 * 60_000
    start_ms = (now_ms // step - 2) * step
    marks, funding = _fixture_rows(start_ms, 1)
    funding[0]["fundingTime"] = start_ms - 1
    monkeypatch.setattr(derivative_history, "DATA_ROOT", tmp_path)
    with SessionLocal() as db:
        asset = db.scalar(select(Asset).where(Asset.id == "binance_usdm:BTCUSDT"))
        asset.verification = "VERIFIED"
        db.commit()
        derivative_history.fetch_derivative_history(
            db, asset.id, "15m", start_ms, now_ms, now_ms=now_ms,
            adapter=_PublicDerivativeAdapter(marks, funding), pause=0,
        )
    loaded = derivative_history.read_derivative_history(asset.id, "15m", root=tmp_path)
    assert loaded["funding"]["available_count"] == 0
    segment = derivative_history._root_directory(tmp_path, "BTCUSDT", "15m", "mark") / "manifest.json"
    manifest = json.loads(segment.read_text(encoding="utf-8"))
    data_path = segment.parent / manifest["segments"][0]["file"]
    data_path.write_bytes(data_path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="SHA256"):
        derivative_history.read_derivative_history("binance_usdm:BTCUSDT", "15m", root=tmp_path)
    with SessionLocal() as db:
        with pytest.raises(ValueError, match="最近三年"):
            derivative_history.fetch_derivative_history(
                db, "binance_usdm:BTCUSDT", "15m", 0, now_ms,
                now_ms=now_ms, adapter=_PublicDerivativeAdapter([], []), pause=0,
            )
