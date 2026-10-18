"""隔离验证理论组合回测的因果对齐、来源绑定和报告完整性。"""

import uuid

import numpy as np
import pandas as pd
from sqlalchemy import select

from app.core.db import SessionLocal
from app.domains.models import Asset, SimulationAccount, User
from app.services import simulation_backtest


def _bars(interval, count, start_ms):
    """构造指定原生周期的确定性合成OHLCV供回测单测使用。

    Args:
        interval: 15m、1h或4h原生周期。
        count: 合成闭合K线根数。
        start_ms: 首根K线开盘UTC毫秒。

    Returns:
        含真实格式时间边界及有限OHLCV值的升序DataFrame。
    """
    duration = simulation_backtest.INTERVAL_MS[interval]
    rows = []
    for index in range(count):
        open_time = start_ms + index * duration
        center = 100 + index * 0.03 + 4 * np.sin(index / 8)
        rows.append({
            "open_time": open_time,
            "close_time": open_time + duration - 1,
            "open": center - 0.2,
            "high": center + 1.0,
            "low": center - 1.0,
            "close": center + 0.2,
            "volume": 10.0 + index % 5,
        })
    return pd.DataFrame(rows)


def _weights():
    """返回覆盖五项已实现规则且合计为1的确定性测试权重。"""
    return {
        "trend": "0.35", "dow123": "0.25", "wave": "0.10",
        "divergence": "0.15", "reversal_patterns": "0.15", "deep_model": "0",
    }


def _configuration():
    """构造只用于隔离测试的本地多周期模拟账号策略字典。"""
    return {
        "asset_ids": ["binance_usdm:BTCUSDT"],
        "intervals": ["15m", "1h", "4h"],
        "weights": _weights(),
        "entry_threshold": "0.35",
        "max_leverage": "2",
        "max_margin_fraction": "0.10",
        "max_holding_hours": 168,
        "exit_mode": "three_bar_trailing",
        "deep_model_version": None,
    }


def test_multi_theory_signals_are_prefix_invariant_and_htf_aligned():
    """验证追加未来K线不改写过去信号，且高周期收盘后才进入分数。

    Returns:
        无返回值；前缀结果变化或非法方向会使测试失败。
    """
    start_ms = 1_760_000_000_000
    complete = {
        interval: _bars(interval, count, start_ms)
        for interval, count in (("15m", 220), ("1h", 90), ("4h", 35))
    }
    cutoff = int(complete["15m"].close_time.iloc[159])
    prefix = {
        interval: frame.loc[frame.close_time <= cutoff].reset_index(drop=True)
        for interval, frame in complete.items()
    }
    full_signals, _, _, _ = simulation_backtest.build_multi_theory_signals(
        complete, _weights(), 0.35,
    )
    prefix_signals, _, _, _ = simulation_backtest.build_multi_theory_signals(
        prefix, _weights(), 0.35,
    )
    assert full_signals.iloc[: len(prefix["15m"])].direction.tolist() == prefix_signals.direction.tolist()
    assert set(full_signals.direction.unique()).issubset({-1, 0, 1})


def test_backtest_uses_real_source_hashes_and_writes_immutable_artifacts(tmp_path, monkeypatch):
    """验证同一账号修订使用交易/标记价/费率源hash生成净值与成交文件。

    Args:
        tmp_path: pytest提供的隔离回测报告根目录。
        monkeypatch: pytest提供的替换器，注入合成归档读取而非网络请求。

    Returns:
        无返回值；来源、USDT资金精度、文件SHA或回测日期错误时测试失败。
    """
    start_ms = 1_760_000_000_000
    market_frames = {
        interval: _bars(interval, count, start_ms)
        for interval, count in (("15m", 220), ("1h", 90), ("4h", 35))
    }
    archives = {
        interval: [{**row, **{key: str(row[key]) for key in ("open", "high", "low", "close", "volume")}}
                   for row in frame.to_dict(orient="records")]
        for interval, frame in market_frames.items()
    }
    hashes = {interval: ("a" if interval == "15m" else "b" if interval == "1h" else "c") * 64
              for interval in market_frames}

    def fake_read_archive(asset_id, interval, limit, end_ms):
        """返回单测固定K线manifest，保持生产服务的JSON字段契约。"""
        rows = [row for row in archives[interval] if row["close_time"] <= end_ms]
        return {"candles": rows, "dataset_sha256": hashes[interval]}

    base = market_frames["15m"]
    mark_rows = [{
        "open_time": int(row["open_time"]),
        "close_time": int(row["close_time"]),
        "open": str(row["open"]), "high": str(row["high"]),
        "low": str(row["low"]), "close": str(row["close"]),
    } for row in base.to_dict(orient="records")]
    mark_manifest = "d" * 64
    funding_manifest = "e" * 64
    funding_rows = [{
        "funding_time": int(base.open_time.iloc[100]),
        "rate": "0.000100000000000000",
        "mark_price": "103.000000000000000000",
    }]

    def fake_read_derivative(asset_id, interval, start_ms, end_ms):
        """返回与15m成交周期完全对齐的合成mark/funding归档。"""
        return {
            "mark": {
                "rows": [row for row in mark_rows if start_ms <= row["open_time"] and row["close_time"] <= end_ms],
                "manifest_sha256": mark_manifest,
                "segment_sha256": ["f" * 64],
            },
            "funding": {
                "rows": [row for row in funding_rows if start_ms <= row["funding_time"] <= end_ms],
                "manifest_sha256": funding_manifest,
                "segment_sha256": ["1" * 64],
            },
        }

    monkeypatch.setattr(simulation_backtest, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(simulation_backtest, "read_archive", fake_read_archive)
    monkeypatch.setattr(simulation_backtest, "read_derivative_history", fake_read_derivative)
    run_id = str(uuid.uuid4())
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "test_admin"))
        asset = db.scalar(select(Asset).where(Asset.id == "binance_usdm:BTCUSDT"))
        asset.verification = "VERIFIED"
        config = _configuration()
        account = SimulationAccount(
            id="simulation-test-account",
            owner_user_id=user.id,
            name="test portfolio",
            initial_equity="10000.12345678",
            base_currency="USDT",
            configuration=config,
            configuration_sha256="2" * 64,
            revision=1,
        )
        db.add(account)
        db.commit()
        start = int(base.close_time.iloc[80])
        end = int(base.close_time.iloc[-1])
        result = simulation_backtest.run_simulation_backtest(
            db,
            run_id,
            {
                "account_id": account.id,
                "owner_user_id": user.id,
                "account_revision": 1,
                "configuration_sha256": "2" * 64,
                "start_ms": start,
                "end_ms": end,
            },
        )

    equity = simulation_backtest.read_backtest_artifact(run_id, result["equity_artifact"], root=tmp_path)
    trades = simulation_backtest.read_backtest_artifact(run_id, result["trades_artifact"], root=tmp_path)
    assert result["status"] == "COMPLETED", result["skipped_assets"]
    assert result["configuration_sha256"] == "2" * 64
    assert result["per_asset"][0]["dataset_hashes"]["mark_manifest_sha256"] == mark_manifest
    assert result["per_asset"][0]["dataset_hashes"]["funding_manifest_sha256"] == funding_manifest
    assert result["initial_equity"] == "10000.12345678"
    assert equity[0]["close_time"] == start
    assert trades == [] or all(item["asset_id"] == "binance_usdm:BTCUSDT" for item in trades)
