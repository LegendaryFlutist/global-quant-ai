"""长期公开K线归档的隔离单元测试。"""

import hashlib
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.domains.models import Asset
from app.services import long_history


def _raw_candles(start_ms, count, interval_ms=86_400_000):
    """生成对齐周期边界的确定性Binance公开K线数组。

    Args:
        start_ms: 第一根开盘UTC毫秒，必须按所选周期边界对齐。
        count: 需要生成的完整K线数量。
        interval_ms: 周期长度，单位毫秒。

    Returns:
        含真实时间戳和Decimal字符串OHLCV字段的API形状数组。
    """
    return [
        [
            start_ms + index * interval_ms,
            "100.25",
            "102.00",
            "99.00",
            "101.50",
            "12.75",
            start_ms + (index + 1) * interval_ms - 1,
        ]
        for index in range(count)
    ]


class _PagedAdapter:
    """按游标返回测试K线，并可在指定请求后模拟网络中断。"""

    def __init__(self, candles, fail_on_call=None, page_size=2):
        """初始化分页结果和可选故障位置。

        Args:
            candles: 按开盘时间升序的公开K线数组列表。
            fail_on_call: 可选的1起始请求编号，用于注入网络故障。
            page_size: 测试响应最大行数，对应REST分页条数。
        """
        self.candles = candles
        self.fail_on_call = fail_on_call
        self.page_size = page_size
        self.calls = 0

    def klines(self, symbol, interval, start_ms, end_ms):
        """模拟Binance按起始游标与条数上限读取K线。

        Args:
            symbol: 精确测试合约代码。
            interval: 测试K线周期。
            start_ms: 请求起点UTC毫秒。
            end_ms: 请求终点UTC毫秒。

        Returns:
            不超过服务分页上限且时间范围内的原始数组。
        """
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise OSError("injected public API outage")
        return [row for row in self.candles if start_ms <= row[0] < end_ms][: self.page_size]


def test_three_year_start_uses_calendar_years_and_handles_leap_day():
    """验证三年起点采用UTC自然年且闰日安全映射至二月底。

    Returns:
        无返回值；起点误差或闰日异常会使测试失败。
    """
    now = int(datetime(2024, 2, 29, 12, tzinfo=timezone.utc).timestamp() * 1000)

    assert datetime.fromtimestamp(long_history.three_year_start(now) / 1000, timezone.utc) == datetime(
        2021, 2, 28, 12, tzinfo=timezone.utc
    )


def test_archive_pages_are_immutable_and_read_as_decimal_strings(tmp_path, monkeypatch):
    """验证分页归档落盘、完整三年状态和读取哈希来源。

    Args:
        tmp_path: pytest提供的隔离临时目录。
        monkeypatch: pytest提供的模块依赖替换器。

    Returns:
        无返回值；磁盘内容、状态或来源哈希错误会使测试失败。
    """
    now_ms = 1_800_000_000_000
    requested_start = long_history.three_year_start(now_ms)
    start_ms = requested_start - requested_start % 86_400_000
    count = (now_ms - start_ms) // 86_400_000
    candles = _raw_candles(start_ms, count)
    monkeypatch.setattr(long_history, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(long_history.time, "sleep", lambda _seconds: None)
    with SessionLocal() as db:
        asset = db.scalar(select(Asset).where(Asset.id == "binance_usdm:BTCUSDT"))
        asset.verification = "VERIFIED"
        db.commit()
        result = long_history.fetch_three_year_archive(
            db,
            asset.id,
            "1d",
            now_ms=now_ms,
            adapter=_PagedAdapter(candles, page_size=1000),
            page_limit=1000,
            pause=0,
        )

    manifest = long_history.archive_manifest("binance_usdm:BTCUSDT", "1d", tmp_path)
    loaded = long_history.read_archive("binance_usdm:BTCUSDT", "1d", root=tmp_path)
    assert result["rows"] == count - 1
    assert result["status"] == "THREE_YEARS"
    assert result["segments"] == 2
    assert manifest["source"] == "binance_usdm_public_rest_klines"
    assert loaded["available_count"] == count - 1
    assert loaded["candles"][0]["open"] == "100.250000000000"
    assert loaded["research_only"] is True
    assert len(loaded["segment_sha256"]) == 2 * 64


def test_archive_retry_resumes_after_persisted_page(tmp_path, monkeypatch):
    """验证网络中断后复用已提交页面且不重复追加已存K线。

    Args:
        tmp_path: pytest提供的隔离临时目录。
        monkeypatch: pytest提供的模块依赖替换器。

    Returns:
        无返回值；断点游标或最终记录数错误会使测试失败。
    """
    start_ms = 1_800_000_000_000
    start_ms = ((start_ms + 899_999) // 900_000) * 900_000
    candles = _raw_candles(start_ms, 5, 900_000)
    now_ms = candles[-1][6] + 1
    monkeypatch.setattr(long_history, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(long_history.time, "sleep", lambda _seconds: None)
    with SessionLocal() as db:
        asset = db.scalar(select(Asset).where(Asset.id == "binance_usdm:BTCUSDT"))
        asset.verification = "VERIFIED"
        db.commit()
        with pytest.raises(OSError, match="injected public API outage"):
            long_history.fetch_three_year_archive(
                db,
                asset.id,
                "15m",
                now_ms=now_ms,
                adapter=_PagedAdapter(candles, fail_on_call=2),
                page_limit=2,
                pause=0,
            )
        first_manifest = long_history.archive_manifest(asset.id, "15m", tmp_path)
        assert first_manifest["row_count"] == 2

        class ResumeAdapter(_PagedAdapter):
            """记录断点请求从第三根K线开始恢复。"""

            def klines(self, symbol, interval, cursor, end):
                """在模拟续传时记录实际请求游标。

                Args:
                    symbol: 精确测试合约代码。
                    interval: 测试K线周期。
                    cursor: 请求起点UTC毫秒。
                    end: 请求终点UTC毫秒。

                Returns:
                    由父适配器筛选的下一页公开K线。
                """
                self.cursors = getattr(self, "cursors", []) + [cursor]
                return super().klines(symbol, interval, cursor, end)

        adapter = ResumeAdapter(candles)
        result = long_history.fetch_three_year_archive(
            db,
            asset.id,
            "15m",
            now_ms=now_ms,
            adapter=adapter,
            page_limit=2,
            pause=0,
        )

    assert adapter.cursors[0] == candles[2][0]
    assert result["rows"] == 5
    assert long_history.archive_manifest("binance_usdm:BTCUSDT", "15m", tmp_path)["row_count"] == 5


def test_archive_rejects_unverified_asset_and_tampered_segment(tmp_path, monkeypatch):
    """验证精确合约核验和分段SHA256阻止越界或静默损坏。

    Args:
        tmp_path: pytest提供的隔离临时目录。
        monkeypatch: pytest提供的模块依赖替换器。

    Returns:
        无返回值；不安全归档被接纳或损坏未检测时测试失败。
    """
    start_ms = 1_800_000_000_000
    start_ms = ((start_ms + 899_999) // 900_000) * 900_000
    monkeypatch.setattr(long_history, "DATA_ROOT", tmp_path)
    with SessionLocal() as db:
        asset = db.scalar(select(Asset).where(Asset.id == "binance_usdm:BTCUSDT"))
        asset.verification = "UNVERIFIED"
        db.commit()
        with pytest.raises(ValueError, match="已核验"):
            long_history.fetch_three_year_archive(
                db, asset.id, "15m", now_ms=start_ms + 900_000, adapter=_PagedAdapter([]), pause=0
            )

    directory = long_history._archive_directory(tmp_path, "BTCUSDT", "15m")
    directory.mkdir(parents=True)
    segment = directory / "tampered.parquet"
    segment.write_bytes(b"untrusted")
    manifest = {
        "schema_version": long_history.ARCHIVE_SCHEMA_VERSION,
        "asset_id": "binance_usdm:BTCUSDT",
        "symbol": "BTCUSDT",
        "interval": "15m",
        "segments": [{"file": segment.name, "sha256": hashlib.sha256(b"original").hexdigest()}],
    }
    long_history._atomic_manifest(directory, manifest)

    with pytest.raises(ValueError, match="SHA256"):
        long_history.archive_manifest("binance_usdm:BTCUSDT", "15m", tmp_path)


def test_authenticated_archive_task_is_deduplicated_and_cache_is_read_only(logged_in, monkeypatch):
    """验证归档点击需CSRF、同标的任务去重且缓存读取不触发下载。

    Args:
        logged_in: 已通过会话认证并带CSRF请求头的测试客户端。
        monkeypatch: pytest提供的模块依赖替换器。

    Returns:
        无返回值；认证、去重或只读语义错误会使测试失败。
    """
    created = []

    def fake_enqueue(db, kind, parameters):
        """在隔离数据库创建可查询的归档任务记录。

        Args:
            db: 当前测试请求持有的SQLAlchemy会话。
            kind: 预期为FETCH_ARCHIVE的任务白名单名称。
            parameters: 精确资产ID和原生周期。

        Returns:
            已提交且可供API序列化的任务对象。
        """
        from app.domains.models import Task

        task = Task(id="archive-test-task", kind=kind, parameters=parameters, status="QUEUED")
        db.add(task)
        db.commit()
        created.append((kind, parameters))
        return task

    monkeypatch.setattr("app.api.jobs.enqueue", fake_enqueue)
    with SessionLocal() as db:
        asset = db.scalar(select(Asset).where(Asset.id == "binance_usdm:BTCUSDT"))
        asset.verification = "VERIFIED"
        db.commit()

    payload = {"kind": "FETCH_ARCHIVE", "asset_id": "binance_usdm:BTCUSDT", "interval": "4h"}
    assert logged_in.post("/api/tasks", json=payload).status_code == 202
    repeated = logged_in.post("/api/tasks", json=payload)
    assert repeated.status_code == 202
    assert repeated.json()["deduplicated"] is True
    assert created == [("FETCH_ARCHIVE", {"asset_id": "binance_usdm:BTCUSDT", "interval": "4h"})]
    assert logged_in.get("/api/market/archive?asset_id=binance_usdt:BTCUSDT&interval=4h").status_code == 422
    cached = logged_in.get("/api/market/archive?asset_id=binance_usdm:BTCUSDT&interval=4h")
    assert cached.status_code == 200
    assert cached.json()["available"] is False


def test_archive_task_rejects_unverified_asset_and_missing_csrf(logged_in):
    """验证长期采集写请求遵守标的核验与CSRF安全边界。

    Args:
        logged_in: 已通过会话认证并带CSRF请求头的测试客户端。

    Returns:
        无返回值；越权请求若被接受则测试失败。
    """
    with SessionLocal() as db:
        asset = db.scalar(select(Asset).where(Asset.id == "binance_usdm:BTCUSDT"))
        asset.verification = "UNVERIFIED"
        db.commit()
    payload = {"kind": "FETCH_ARCHIVE", "asset_id": "binance_usdm:BTCUSDT", "interval": "1h"}
    assert logged_in.post("/api/tasks", json=payload).status_code == 422
    assert logged_in.post("/api/tasks", json=payload, headers={"X-CSRF-Token": "invalid"}).status_code == 403


def test_structure_api_reports_missing_local_periods_without_network(logged_in, tmp_path, monkeypatch):
    """验证多周期分析API只报告本地缺档状态且拒绝无效周期。

    Args:
        logged_in: 已通过会话认证并带CSRF请求头的测试客户端。
        tmp_path: pytest提供的隔离临时目录。
        monkeypatch: pytest提供的模块依赖替换器。

    Returns:
        无返回值；若未缓存周期触发网络或错误周期被接纳则测试失败。
    """
    monkeypatch.setattr(long_history, "DATA_ROOT", tmp_path)
    with SessionLocal() as db:
        asset = db.scalar(select(Asset).where(Asset.id == "binance_usdm:BTCUSDT"))
        asset.verification = "VERIFIED"
        db.commit()

    response = logged_in.get(
        "/api/market/structure?asset_id=binance_usdm:BTCUSDT&intervals=15m%2C1h%2C4h"
    )
    assert response.status_code == 200
    assert response.json()["missing_archives"] == ["15m", "1h", "4h"]
    assert response.json()["events"] == []
    assert response.json()["research_only"] is True
    assert logged_in.get(
        "/api/market/structure?asset_id=binance_usdm:BTCUSDT&intervals=15s"
    ).status_code == 422
