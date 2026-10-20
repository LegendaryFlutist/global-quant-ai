"""Authenticated system-page full-research task and report tests."""

import csv
import hashlib
import json

from app.api import research_backtests
from app.core.db import SessionLocal
from app.domains.models import MarketData, Task
from app.storage.parquet_files import sha256_file


def _queue_task(client, monkeypatch):
    """Create one authorized test job while avoiding access to personal datasets.

    Args:
        client: Logged-in FastAPI test client using the isolated quant_test database.
        monkeypatch: Pytest fixture that replaces local-data preflight for this test.

    Returns:
        The newly queued task UUID from the system research API.
    """
    monkeypatch.setattr(research_backtests, "_has_local_source", lambda symbol: True)
    response = client.post("/api/research/backtests", json={
        "symbols": ["BTCUSDT"], "models": list(research_backtests.RESEARCH_MODELS), "timeframes": ["15m"],
    })
    assert response.status_code == 202
    return response.json()["id"]


def test_research_task_is_validated_and_owner_scoped(logged_in, monkeypatch):
    """Queue only allowlisted inputs and hide private runtime path parameters."""
    monkeypatch.setattr(research_backtests, "_has_local_source", lambda symbol: True)
    invalid = logged_in.post("/api/research/backtests", json={
        "symbols": ["NOT-A-SYMBOL"], "models": ["analysis_dow123"], "timeframes": ["15m"],
    })
    assert invalid.status_code == 422
    ml_rejected = logged_in.post("/api/research/backtests", json={
        "symbols": ["BTCUSDT"], "models": ["logistic"], "timeframes": ["15m"],
    })
    assert ml_rejected.status_code == 422
    task_id = _queue_task(logged_in, monkeypatch)
    visible = logged_in.get("/api/research/backtests").json()
    task = next(row for row in visible if row["id"] == task_id)
    assert task["status"] == "QUEUED"
    assert "owner_user_id" not in task["parameters"]
    assert "output_id" not in task["parameters"]


def test_backtest_coverage_lists_verified_assets_and_registered_algorithms(logged_in, monkeypatch):
    """Expose verified product coverage and all four registered rule/deep algorithms."""
    monkeypatch.setattr(research_backtests, "_research_input_status", lambda symbol: {
        "ready": symbol == "BTCUSDT", "status": "READY" if symbol == "BTCUSDT" else "MISSING_INPUTS",
        "reason": "完整" if symbol == "BTCUSDT" else "缺少标记价", "requirements": [] if symbol == "BTCUSDT" else ["标记价K线"],
    })
    body = logged_in.get("/api/research/backtests/coverage").json()
    assert len(body["assets"]) == 13
    assert {row["symbol"] for row in body["assets"]} == set(research_backtests.RESEARCH_SYMBOLS)
    assert [row["symbol"] for row in body["assets"] if row["ready"]] == ["BTCUSDT"]
    assert body["models"] == ["analysis_dow123", "patchtst", "tft_attention", "tcn"]
    bitcoin = next(row for row in body["assets"] if row["symbol"] == "BTCUSDT")
    assert bitcoin["cached_15m"] == {"available": False, "verified": False, "rows": 0, "last_close_ms": None}
    assert next(row for row in body["assets"] if row["symbol"] == "KORUUSDT")["requirements"] == ["标记价K线"]


def test_research_input_status_reports_missing_and_hash_mismatch(logged_in, monkeypatch, tmp_path):
    """Explain missing local components and reject an evaluation plan for other bytes."""
    monkeypatch.setattr(research_backtests, "RESEARCH_ROOT", tmp_path)
    assert research_backtests._research_input_status("BTCUSDT")["requirements"] == [
        "交易价K线", "资金费率", "标记价K线", "行情来源证明", "冻结评估区间",
    ]
    data = tmp_path / "data" / "btcusdt_90d_connector"
    data.mkdir(parents=True)
    bars = b"open_time,close\n1,100\n"
    funding = b"funding_time,rate\n2,0.001\n"
    (data / "bars.csv").write_bytes(bars)
    (data / "funding.csv").write_bytes(funding)
    (data / "mark_bars.csv").write_text("open_time,close\n1,100\n", encoding="utf-8")
    (data / "source.json").write_text(json.dumps({"symbol": "BTCUSDT", "synthetic": False}), encoding="utf-8")
    digest = hashlib.sha256(bars + funding).hexdigest()
    (data / "evaluation_plan.json").write_text(json.dumps({
        "dataset_sha256": "0" * 64, "holdout_period_ms": [10, 20],
    }), encoding="utf-8")
    assert research_backtests._research_input_status("BTCUSDT")["status"] == "FROZEN_PLAN_MISMATCH"
    (data / "evaluation_plan.json").write_text(json.dumps({
        "dataset_sha256": digest, "holdout_period_ms": [10, 20],
    }), encoding="utf-8")
    assert research_backtests._research_input_status("BTCUSDT")["ready"] is True


def test_market_cache_coverage_checks_file_digest(logged_in, monkeypatch, tmp_path):
    """Expose cached candle rows only when the file matches its metadata digest."""
    monkeypatch.setattr(research_backtests, "DATA_ROOT", tmp_path)
    path = tmp_path / "market.parquet"
    path.write_bytes(b"test-only-parquet-bytes")
    with SessionLocal() as db:
        db.add(MarketData(
            asset_id="binance_usdm:BTCUSDT", interval="15m", path=path.name,
            row_count=8640, first_open_ms=1000, last_close_ms=9999,
            sha256=sha256_file(path), source={},
        ))
        db.commit()
    body = logged_in.get("/api/research/backtests/coverage").json()
    bitcoin = next(row for row in body["assets"] if row["symbol"] == "BTCUSDT")
    assert bitcoin["cached_15m"] == {"available": True, "verified": True, "rows": 8640, "last_close_ms": 9999}


def test_completed_task_serves_scoped_curve_and_operations(logged_in, monkeypatch, tmp_path):
    """Serve the selected model report from a fixed UUID path and no other scope."""
    task_id = _queue_task(logged_in, monkeypatch)
    monkeypatch.setattr(research_backtests, "RESEARCH_RUNS_ROOT", tmp_path / "reports")
    report_root = tmp_path / "reports" / task_id / "models" / "analysis_dow123" / "symbols" / "BTCUSDT"
    report_root.mkdir(parents=True)
    key = "15m|analysis_dow123|swing_percent"
    (report_root / "report.json").write_text(json.dumps({
        "status": "NO_CANDIDATE", "dataset_sha256": "a" * 64, "ranking": [{
            "timeframe": "15m", "model": "analysis_dow123", "exit_mode": "swing_percent",
            "net_return": 0.03, "max_drawdown": 0.02, "entries": 4,
            "liquidations": 0, "validation_score": 0.01, "eligible": False,
            "rejection_reasons": [], "holding_hours_mean": 48,
        }],
    }))
    (report_root / "operation_summary.json").write_text(json.dumps({"accounts": {key: {
        "timeframe": "15m", "exit_mode": "swing_percent", "round_trips": 1,
        "net_return": 0.03, "max_drawdown": 0.02, "holding_hours_mean": 48,
    }}}))
    (report_root / "validation_curves.json").write_text(json.dumps({key: {"1000": 1.0, "2000": 1.03}}))
    with (report_root / "trades.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["config", "position_id", "direction", "entry_time", "exit_time", "net_pnl"])
        writer.writeheader()
        writer.writerow({"config": key, "position_id": "p1", "direction": 1, "entry_time": 1000, "exit_time": 2000, "net_pnl": "3.00"})
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        task.status = "SUCCEEDED"
        task.result = {"progress": {"percent": 100, "phase": "completed"}}
        db.commit()
    report = logged_in.get(f"/api/research/backtests/{task_id}/assets/BTCUSDT/models/analysis_dow123?timeframe=15m&exit_mode=swing_percent")
    assert report.status_code == 200
    body = report.json()
    assert body["curve"] == [[1000, 1.0], [2000, 1.03]]
    assert body["operations"][0]["position_id"] == "p1"
    assert body["long_operations"] == 1 and body["short_operations"] == 0
    hidden = logged_in.get(f"/api/research/backtests/{task_id}/assets/NVDAUSDT/models/analysis_dow123")
    assert hidden.status_code == 404


def test_archived_report_is_read_only_and_path_scoped(logged_in, monkeypatch, tmp_path):
    """List and read an existing full batch without exposing filesystem paths."""
    research_root = tmp_path / "research"
    archive = research_root / "runs" / "prior_run"
    archive.mkdir(parents=True)
    (archive / "batch_summary.json").write_text(json.dumps({
        "models": {"logistic": "models/logistic/index.html"}, "symbols": ["BTCUSDT"],
        "configurations": 7, "sources": [{"symbol": "BTCUSDT", "status": "NO_CANDIDATE", "source_dir": "/private/path"}],
    }))
    monkeypatch.setattr(research_backtests, "RESEARCH_ROOT", research_root)
    rows = logged_in.get("/api/research/backtests").json()
    assert any(row["id"] == "archive:prior_run" for row in rows)
    summary = logged_in.get("/api/research/archives/prior_run/summary")
    assert summary.status_code == 200
    assert "source_dir" not in summary.json()["sources"][0]
    traversal = logged_in.get("/api/research/archives/%2e%2e/summary")
    assert traversal.status_code == 404
