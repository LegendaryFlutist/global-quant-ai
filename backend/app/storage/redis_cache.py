"""Short-lived Redis cache for forming public market candles."""

import json
import re
from functools import lru_cache

from redis import Redis
from redis.exceptions import RedisError

from app.adapters.public_binance import INTERVALS
from app.core.config import REDIS_URL

LIVE_CANDLE_TTL_SECONDS = 20


@lru_cache(maxsize=1)
def _redis_client() -> Redis:
    """Reuse one bounded-timeout Redis connection pool per Python process.

    Returns:
        Redis client using the configured queue/cache service and decoded strings.

    Notes:
        Connection and read timeouts are bounded so cache outages do not stall
        a market refresh or API fallback for an unbounded interval.
    """
    return Redis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=0.5,
        socket_timeout=0.5,
        health_check_interval=20,
    )


def _live_candle_key(asset_id: str, interval: str) -> str:
    """Create a namespaced cache key for one exact Binance product and period.

    Args:
        asset_id: Exact verified `binance_usdm:SYMBOL` product identifier.
        interval: Supported native Binance candle period.

    Returns:
        Redis key unique to the asset and interval.

    Raises:
        ValueError: Product identity or period is not a supported public stream.
    """
    if (
        not isinstance(asset_id, str)
        or not re.fullmatch(r"binance_usdm:[A-Z0-9_]{1,40}", asset_id)
        or interval not in INTERVALS
    ):
        raise ValueError("实时缓存只允许精确核验的Binance永续标的和原生周期")
    return f"market:live:{asset_id}:{interval}"


def cache_live_candle(
    asset_id: str,
    interval: str,
    payload: dict,
    *,
    client: Redis | None = None,
) -> bool:
    """Store one temporary forming-candle snapshot with a short expiration.

    Args:
        asset_id: Exact verified `binance_usdm:SYMBOL` product identifier.
        interval: Native candle period used for the snapshot.
        payload: Public market snapshot with stringified prices and its digest.
        client: Optional Redis-compatible client for tests or explicit injection.

    Returns:
        True if Redis accepted the expiring key; False if Redis is unavailable.

    Raises:
        ValueError: Asset identity or native period is invalid.
    """
    key = _live_candle_key(asset_id, interval)
    if (
        not isinstance(payload, dict)
        or payload.get("asset_id") != asset_id
        or payload.get("symbol") != asset_id.split(":", 1)[1]
        or payload.get("interval") != interval
    ):
        raise ValueError("实时缓存内容必须匹配键中的标的和周期")
    encoded = json.dumps(payload, sort_keys=True, allow_nan=False, separators=(",", ":"))
    try:
        return bool((client or _redis_client()).set(key, encoded, ex=LIVE_CANDLE_TTL_SECONDS))
    except RedisError:
        return False


def read_cached_live_candle(
    asset_id: str,
    interval: str,
    *,
    client: Redis | None = None,
) -> dict | None:
    """Read a short-lived public forming-candle snapshot when its key exists.

    Args:
        asset_id: Exact verified `binance_usdm:SYMBOL` product identifier.
        interval: Native candle period used for the snapshot.
        client: Optional Redis-compatible client for tests or explicit injection.

    Returns:
        Decoded snapshot dictionary, or None for a miss, invalid JSON, or Redis
        connection failure. The caller must still validate identity and SHA256.

    Raises:
        ValueError: Asset identity or native period is invalid.
    """
    key = _live_candle_key(asset_id, interval)
    try:
        encoded = (client or _redis_client()).get(key)
    except RedisError:
        return None
    if encoded is None:
        return None
    try:
        payload = json.loads(encoded)
    except (TypeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("asset_id") != asset_id
        or payload.get("symbol") != asset_id.split(":", 1)[1]
        or payload.get("interval") != interval
    ):
        return None
    return payload
