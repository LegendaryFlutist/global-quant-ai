"""Regression tests for expiring Redis-backed live market snapshots."""

from unittest.mock import Mock

import pytest
from redis.exceptions import RedisError

from app.storage.redis_cache import (
    LIVE_CANDLE_TTL_SECONDS,
    cache_live_candle,
    read_cached_live_candle,
)


def test_live_candle_cache_uses_exact_key_and_short_ttl():
    """Round-trip one forming candle through the Redis TTL cache contract.

    Returns:
        None; asserts the namespaced product key, expiration and decoded payload.
    """
    client = Mock()
    client.set.return_value = True
    payload = {
        "asset_id": "binance_usdm:BTCUSDT",
        "symbol": "BTCUSDT",
        "interval": "15m",
        "fetched_at_ms": 1_797_900_000_000,
        "candle": {"close": "60000.123456789012"},
        "sha256": "a" * 64,
    }

    assert cache_live_candle(payload["asset_id"], "15m", payload, client=client)
    key, encoded = client.set.call_args.args
    assert key == "market:live:binance_usdm:BTCUSDT:15m"
    assert client.set.call_args.kwargs == {"ex": LIVE_CANDLE_TTL_SECONDS}
    client.get.return_value = encoded
    assert read_cached_live_candle(payload["asset_id"], "15m", client=client) == payload


def test_live_candle_cache_rejects_unmapped_products_and_survives_redis_outage():
    """Reject unverified keys and degrade to a cache miss on Redis failure.

    Returns:
        None; asserts invalid products cannot be written and network failures do
        not convert a successful local market-data refresh into an exception.
    """
    client = Mock()
    client.set.side_effect = RedisError("offline")
    client.get.side_effect = RedisError("offline")
    payload = {
        "asset_id": "binance_usdm:BTCUSDT",
        "symbol": "BTCUSDT",
        "interval": "15m",
    }

    with pytest.raises(ValueError, match="Binance永续"):
        cache_live_candle("asset:KORU", "15m", payload, client=client)
    assert not cache_live_candle(payload["asset_id"], "15m", payload, client=client)
    assert read_cached_live_candle("binance_usdm:BTCUSDT", "15m", client=client) is None
