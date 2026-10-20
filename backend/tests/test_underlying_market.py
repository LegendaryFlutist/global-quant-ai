"""Research-only tests for separately registered stock, ETF and energy sources."""

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.domains.models import Asset, MarketData, MarketObservation
from app.services import market_data
from app.services.underlying_market import (
    fetch_underlying_history,
    underlying_series,
    validate_underlying_request,
)


class _MassiveBars:
    """Return deterministic source-native bars without opening a network connection."""

    def __init__(self, values):
        """Store pre-built aggregate rows for one isolated service test."""
        self.values = values
        self.calls = []

    def aggregates(self, symbol, interval, start_date, end_date):
        """Return copied provider bars and capture the bound source request."""
        self.calls.append((symbol, interval, start_date, end_date))
        return self.values


class _EIAObservations:
    """Return a deterministic scalar time series in the official API shape."""

    def __init__(self, rows):
        """Store source values for the current test only."""
        self.rows = rows
        self.calls = []

    def daily_observations(self, route, series, start_date, end_date):
        """Return values without synthesizing OHLC fields."""
        self.calls.append((route, series, start_date, end_date))
        return self.rows


def test_underlying_registry_keeps_source_and_perpetual_products_separate(logged_in):
    """Verify UI catalog identities and the missing-provider status are explicit.

    Args:
        logged_in: Authenticated test client using only the isolated quant_test DB.

    Returns:
        None; exact original-product IDs and research-only status are asserted.
    """
    assets = {asset["id"]: asset for asset in logged_in.get("/api/assets").json()}
    assert assets["us:NVDA"]["market_source"]["provider_id"] == "massive_stocks"
    assert assets["us:KORU"]["market_source"]["kind"] == "candles"
    assert assets["krx:000660"]["market_source"]["intervals"] == ["1d"]
    assert assets["eia:WTI_CUSHING_SPOT"]["market_source"]["kind"] == "observations"
    assert assets["eia:HENRY_HUB_SPOT"]["market_source"]["provider_id"] == "eia"
    assert assets["binance_usdm:NVDAUSDT"]["id"] != assets["us:NVDA"]["id"]
    assert all(not asset["live_tradable"] for asset in assets.values())


def test_underlying_history_saves_real_native_candles_and_preserves_three_year_retention(tmp_path, monkeypatch):
    """Store a real-shaped Massive bar at its native exchange-session boundary.

    Args:
        tmp_path: Isolated temporary Parquet directory.
        monkeypatch: Replaces only the market-data path and provider environment.

    Returns:
        None; metadata, source identity and exact Decimal values are checked.
    """
    monkeypatch.setattr(market_data, "DATA_ROOT", tmp_path)
    from app.storage import market_data_segments
    monkeypatch.setattr(market_data_segments, "DATA_ROOT", tmp_path)
    now_ms = int(datetime(2026, 10, 20, 12, tzinfo=timezone.utc).timestamp() * 1000)
    native_open = int(datetime(2026, 10, 19, 13, 30, tzinfo=timezone.utc).timestamp() * 1000)
    adapter = _MassiveBars([{"t": native_open, "o": "182.123456789012", "h": "183", "l": "181", "c": "182.5", "v": "1234"}])
    with SessionLocal() as db:
        result = fetch_underlying_history(db, "us:NVDA", "1h", days=30, now_ms=now_ms, adapter=adapter)
        metadata = db.scalar(select(MarketData).where(MarketData.asset_id == "us:NVDA", MarketData.interval == "1h"))
        stored = market_data.read_dataset(metadata)
        perpetual_data = db.scalar(select(MarketData.id).where(MarketData.asset_id == "binance_usdm:NVDAUSDT"))
    assert result["kind"] == "candles"
    assert result["rows_fetched"] == 1
    assert stored[0]["open_time"] == native_open
    assert stored[0]["open"] == Decimal("182.123456789012")
    assert metadata.source["not_a_related_perpetual"] is True
    assert metadata.sha256 == result["sha256"]
    assert perpetual_data is None


def test_eia_reference_prices_are_scalar_observations_not_fake_candles(tmp_path, monkeypatch):
    """Persist EIA daily values using a separate schema and preserve their units.

    Args:
        tmp_path: Isolated temporary Parquet directory.
        monkeypatch: Replaces the reference-series storage root.

    Returns:
        None; only scalar values appear in the API result and metadata table.
    """
    from app.storage import market_observations

    monkeypatch.setattr(market_observations, "DATA_ROOT", tmp_path)
    now_ms = int(datetime(2026, 10, 20, 12, tzinfo=timezone.utc).timestamp() * 1000)
    adapter = _EIAObservations([
        {"period": "2026-10-15", "value": "70.125"},
        {"period": "2026-10-19", "value": "71.50"},
    ])
    with SessionLocal() as db:
        result = fetch_underlying_history(db, "eia:WTI_CUSHING_SPOT", "1d", days=30, now_ms=now_ms, adapter=adapter)
        metadata = db.scalar(select(MarketObservation).where(MarketObservation.asset_id == "eia:WTI_CUSHING_SPOT"))
        series = underlying_series(db, "eia:WTI_CUSHING_SPOT", limit=20)
        candle_count = db.scalar(select(MarketData.id).where(MarketData.asset_id == "eia:WTI_CUSHING_SPOT"))
    assert result["kind"] == "observations"
    assert metadata.unit == "USD/barrel"
    assert series["observations"][-1]["value"] == "71.5"
    assert series["observations"][-1]["unit"] == "USD/barrel"
    assert "open" not in series["observations"][-1]
    assert candle_count is None


def test_provider_credentials_are_required_only_at_server_boundary(monkeypatch):
    """Reject missing provider keys without ever accepting them from a request body.

    Args:
        monkeypatch: Clears optional environment credentials for this test.

    Returns:
        None; missing configuration is explicit and the provider key is not a field.
    """
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    with SessionLocal() as db:
        asset = db.get(Asset, "us:NVDA")
        with pytest.raises(ValueError, match="MASSIVE_API_KEY"):
            validate_underlying_request(asset, "15m")


def test_startup_queues_only_configured_underlying_sources(monkeypatch):
    """Verify startup schedules KRX daily history only after local key configuration.

    Args:
        monkeypatch: Sets a non-secret placeholder to model local key presence.

    Returns:
        None; every queued task is an exact registered underlying history request.
    """
    from app.services.startup_market_data import queue_missing_underlying_history

    for key in ("MASSIVE_API_KEY", "TWELVE_DATA_API_KEY", "EIA_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    with SessionLocal() as db:
        assert queue_missing_underlying_history(db, enqueue_task=lambda *_: pytest.fail("unconfigured source queued"))["queued_count"] == 0
        monkeypatch.setenv("TWELVE_DATA_API_KEY", "configured-test-only")
        created = []

        def record_task(session, kind, parameters):
            """Collect safe startup task parameters without publishing to RQ."""
            task = SimpleNamespace(id=f"task-{len(created)}", status="QUEUED", parameters=parameters)
            created.append((kind, parameters))
            return task

        result = queue_missing_underlying_history(db, enqueue_task=record_task)
    assert result["queued_count"] == 2
    assert {parameters["asset_id"] for _, parameters in created} == {"krx:000660", "krx:005930"}
    assert all(kind == "FETCH_UNDERLYING_HISTORY" and parameters["days"] == 1095 for kind, parameters in created)


def test_underlying_task_api_rejects_unconfigured_source_and_accepts_configured_source(logged_in, monkeypatch):
    """Protect task creation with auth, exact source mapping and server-only credentials.

    Args:
        logged_in: Authenticated isolated test client.
        monkeypatch: Controls key presence without storing or transmitting a key value.

    Returns:
        None; missing credentials are rejected and a configured source only queues work.
    """
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    payload = {"kind": "FETCH_UNDERLYING_HISTORY", "asset_id": "us:NVDA", "interval": "15m", "days": 1095}
    assert logged_in.post("/api/tasks", json=payload).status_code == 409
    monkeypatch.setenv("MASSIVE_API_KEY", "configured-test-only")
    response = logged_in.post("/api/tasks", json=payload)
    assert response.status_code == 202
    task = logged_in.get("/api/tasks").json()[0]
    assert task["kind"] == "FETCH_UNDERLYING_HISTORY"
    assert "configured-test-only" not in str(task)


def test_scalar_market_series_requires_authentication(client):
    """Prevent unauthenticated reads of locally stored source observations.

    Args:
        client: Unauthenticated FastAPI test client.

    Returns:
        None; API must reject the request before the database is queried.
    """
    response = client.get("/api/market/series", params={"asset_id": "eia:WTI_CUSHING_SPOT"})
    assert response.status_code == 401


def test_provider_http_errors_never_return_or_log_the_api_key():
    """Ensure provider error text remains sanitized even when auth is rejected.

    Returns:
        None; the adapter carries a test-only key in the request but excludes it
        from public exceptions.
    """
    from app.adapters.underlying_public import MassiveStocksPublic, UnderlyingDataError

    seen = []

    def reject(request):
        """Capture that the key is sent on the server request and return HTTP 401."""
        seen.append(request.url.params.get("apiKey"))
        return httpx.Response(401, request=request)

    adapter = MassiveStocksPublic(api_key="mock-test-token", transport=httpx.MockTransport(reject))
    with pytest.raises(UnderlyingDataError) as error:
        adapter.aggregates("NVDA", "15m", "2026-10-01", "2026-10-02")
    assert seen == ["mock-test-token"]
    assert "mock-test-token" not in str(error.value)
