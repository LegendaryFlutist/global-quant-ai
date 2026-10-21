"""Research-only tests for separately registered stock, ETF and energy sources."""

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.domains.models import Asset, MarketData, MarketObservation, Provider
from app.services import market_data
from app.services.underlying_market import (
    fetch_underlying_history,
    _fetch_candle_source,
    underlying_series,
    validate_underlying_request,
)
from app.adapters.underlying_public import (
    SinaChinaIndicesPublic, TencentChinaIndicesPublic, UnderlyingDataError,
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


class _PublicIndexBars:
    """Return date-labelled daily index candles for a no-key adapter test."""

    def __init__(self, values):
        """Keep deterministic public-feed values and an empty call audit.

        Args:
            values: Daily OHLCV dictionaries with ISO session-date labels.

        Returns:
            None; this fake stores only test-local objects and performs no I/O.
        """
        self.values = values
        self.calls = []

    def daily_bars(self, symbol, start_date, end_date):
        """Return configured bars and capture the source mapping used by ingestion.

        Args:
            symbol: Registry-bound index code, without user-controlled URL content.
            start_date: Inclusive ISO session date.
            end_date: Inclusive ISO session date.

        Returns:
            The deterministic daily OHLCV rows configured for this test.
        """
        self.calls.append((symbol, start_date, end_date))
        return self.values


class _UnavailableIndexBars:
    """Simulate a rejected public source without making an external request."""

    def daily_bars(self, symbol, start_date, end_date):
        """Raise the sanitized upstream error for the primary provider attempt.

        Args:
            symbol: Registry-bound provider symbol.
            start_date: Requested inclusive ISO session date.
            end_date: Requested inclusive ISO session date.

        Raises:
            UnderlyingDataError: The deterministic fake represents an unavailable source.
        """
        raise UnderlyingDataError("mock public source unavailable")


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
    china_indices = {
        "cn_index:000001": "上证指数", "cn_index:399001": "深证成指", "cn_index:399006": "创业板指",
        "cn_index:000688": "科创50", "cn_index:000905": "中证500指数", "cn:000300": "沪深300指数",
    }
    assert {asset_id: assets[asset_id]["name"] for asset_id in china_indices} == china_indices
    assert all(assets[asset_id]["market_category"] == "indices" for asset_id in china_indices)
    assert all(assets[asset_id]["market_source"]["credential_required"] is False for asset_id in china_indices)
    assert all(assets[asset_id]["market_source"]["provider_id"] == "sina_cn_public" for asset_id in china_indices)
    assert assets["binance_usdm:NVDAUSDT"]["id"] != assets["us:NVDA"]["id"]
    assert all(not asset["live_tradable"] for asset in assets.values())


def test_provider_unavailable_status_survives_idempotent_startup_seed(logged_in):
    """Keep a real failed-source state visible after application restarts.

    Args:
        logged_in: Authenticated test client that initializes the isolated catalog.

    Returns:
        None; an unavailable no-key provider must not be reset to unverified on reseed.
    """
    from app.services.catalog import seed

    with SessionLocal() as db:
        provider = db.get(Provider, "sina_cn_public")
        prior_status, prior_detail = provider.status, dict(provider.detail or {})
        provider.status = "UNAVAILABLE"
        provider.detail = {**prior_detail, "last_error_code": "UPSTREAM_OR_DATA_VALIDATION_ERROR"}
        db.commit()
        seed(db)
        provider = db.get(Provider, "sina_cn_public")
        assert provider.status == "UNAVAILABLE"
        assert provider.detail["credential_required"] is False
        provider.status, provider.detail = prior_status, prior_detail
        db.commit()


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
        china_index = db.get(Asset, "cn_index:000001")
        validated = validate_underlying_request(china_index, "1d")
    assert validated["credential_required"] is False
    assert validated["provider_id"] == "sina_cn_public"


def test_public_index_daily_bars_persist_without_keys_and_keep_product_identity(tmp_path, monkeypatch):
    """Persist true Sina-shaped index candles without credentials or perpetual mixing.

    Args:
        tmp_path: Isolated temporary Parquet directory.
        monkeypatch: Replaces the market-data storage root.

    Returns:
        None; source identity, Decimal values and index/perpetual separation are checked.
    """
    monkeypatch.setattr(market_data, "DATA_ROOT", tmp_path)
    from app.storage import market_data_segments
    monkeypatch.setattr(market_data_segments, "DATA_ROOT", tmp_path)
    now_ms = int(datetime(2026, 10, 20, 12, tzinfo=timezone.utc).timestamp() * 1000)
    adapter = _PublicIndexBars([{
        "day": "2026-10-19", "open": "3821.25", "high": "3840.50",
        "low": "3802.10", "close": "3830.75", "volume": "123456789",
    }])
    with SessionLocal() as db:
        result = fetch_underlying_history(db, "cn_index:000001", "1d", days=1095, now_ms=now_ms, adapter=adapter)
        metadata = db.scalar(select(MarketData).where(MarketData.asset_id == "cn_index:000001", MarketData.interval == "1d"))
        stored = market_data.read_dataset(metadata)
        perpetual_data = db.scalar(select(MarketData.id).where(MarketData.asset_id == "binance_usdm:BTCUSDT"))
    assert adapter.calls[0][0] == "sh000001"
    assert result["source"] == "sina_cn_public"
    assert stored[0]["close"] == Decimal("3830.75")
    assert metadata.source["not_a_related_perpetual"] is True
    assert perpetual_data is None


def test_sina_and_tencent_public_adapters_decode_daily_rows_without_credentials():
    """Validate both no-key public adapter envelopes and their fixed GET routes.

    Returns:
        None; source symbols, daily values and absence of API-key parameters are checked.
    """
    requested = []

    def respond(request):
        """Return fixed provider-shaped JSON without contacting the network.

        Args:
            request: HTTPX request sent to one of the two fixed adapter hosts.

        Returns:
            An HTTP 200 response containing deterministic daily OHLCV test rows.
        """
        requested.append(request)
        if "quotes.sina.cn" in request.url.host:
            return httpx.Response(200, json=[{
                "day": "2026-10-19", "open": "3821", "high": "3840",
                "low": "3802", "close": "3830", "volume": "1234",
            }])
        return httpx.Response(200, json={"data": {"sh000001": {"day": [[
            "2026-10-19", "3821", "3830", "3840", "3802", "1234",
        ]]}}})

    transport = httpx.MockTransport(respond)
    sina = SinaChinaIndicesPublic(transport=transport).daily_bars("sh000001", "2026-10-01", "2026-10-20")
    tencent = TencentChinaIndicesPublic(transport=transport).daily_bars("sh000001", "2026-10-01", "2026-10-20")
    assert sina[0]["close"] == "3830"
    assert tencent[0] == {
        "day": "2026-10-19", "open": "3821", "high": "3840",
        "low": "3802", "close": "3830", "volume": "1234",
    }
    assert len(requested) == 2
    assert all("api_key" not in request.url.params and "apikey" not in request.url.params for request in requested)
    assert requested[0].url.params["scale"] == "240"


def test_public_index_source_falls_back_after_primary_failure(monkeypatch):
    """Select the Tencent fallback and preserve its identity after Sina fails.

    Args:
        monkeypatch: Replaces only the fallback adapter constructor.

    Returns:
        None; fallback selection and normalized Decimal close are asserted.
    """
    fallback = _PublicIndexBars([{
        "day": "2026-10-19", "open": "3821", "high": "3840",
        "low": "3802", "close": "3830", "volume": "1234",
    }])
    monkeypatch.setattr("app.services.underlying_market.TencentChinaIndicesPublic", lambda: fallback)
    source = {
        "provider_id": "sina_cn_public", "fallback_provider_ids": ["tencent_cn_public"],
        "alias": "sh000001", "kind": "candles", "intervals": ["1d"],
    }
    now_ms = int(datetime(2026, 10, 20, 12, tzinfo=timezone.utc).timestamp() * 1000)
    closed, live, selected = _fetch_candle_source(
        source, "1d", "2026-10-01", "2026-10-20", now_ms, _UnavailableIndexBars(),
    )
    assert selected == "tencent_cn_public"
    assert closed[0]["close"] == Decimal("3830")
    assert live is None


def test_startup_queues_no_key_china_indices_and_configured_key_sources(monkeypatch):
    """Verify startup queues no-key public indices and configured keyed providers.

    Args:
        monkeypatch: Sets a non-secret placeholder to model local key presence.

    Returns:
        None; queued tasks are exact registered underlying history requests.
    """
    from app.services.startup_market_data import queue_missing_underlying_history

    for key in ("MASSIVE_API_KEY", "TWELVE_DATA_API_KEY", "EIA_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    with SessionLocal() as db:
        created = []

        def record_task(session, kind, parameters):
            """Collect startup tasks without sending them to the real background queue."""
            task = SimpleNamespace(id=f"task-{len(created)}", status="QUEUED", parameters=parameters)
            created.append((kind, parameters))
            return task

        result = queue_missing_underlying_history(db, enqueue_task=record_task)
        assert result["queued_count"] == 6
        assert {parameters["asset_id"] for _, parameters in created} == {
            "cn_index:000001", "cn_index:399001", "cn_index:399006", "cn_index:000688", "cn_index:000905", "cn:000300",
        }
        monkeypatch.setenv("TWELVE_DATA_API_KEY", "configured-test-only")
        created.clear()
        result = queue_missing_underlying_history(db, enqueue_task=record_task)
    assert result["queued_count"] == 8
    assert {parameters["asset_id"] for _, parameters in created} == {
        "krx:000660", "krx:005930", "cn_index:000001", "cn_index:399001", "cn_index:399006", "cn_index:000688", "cn_index:000905", "cn:000300",
    }
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
