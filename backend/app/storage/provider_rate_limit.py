"""Redis-backed request pacing shared by API and ingestion workers."""

from contextlib import contextmanager
from functools import lru_cache
import json
import time

from redis import Redis
from redis.exceptions import RedisError

from app.core.config import CONFIG_ROOT, REDIS_URL

_ALLOWED_BUCKETS = frozenset({"massive_stocks", "yahoo_chart", "coinbase_exchange"})
_STATE_TTL_MS = 86_400_000
_RESERVE_SLOT_LUA = """
local now_ms = tonumber(ARGV[1])
local spacing_ms = tonumber(ARGV[2])
local minimum_ttl_ms = tonumber(ARGV[3])
local last_slot_ms = tonumber(redis.call('GET', KEYS[1]) or '0')
local blocked_until_ms = tonumber(redis.call('GET', KEYS[2]) or '0')
local slot_ms = math.max(now_ms, last_slot_ms + spacing_ms, blocked_until_ms)
local ttl_ms = math.max(minimum_ttl_ms, slot_ms - now_ms + spacing_ms)
redis.call('SET', KEYS[1], tostring(slot_ms), 'PX', ttl_ms)
return slot_ms
"""
_NEXT_ALLOWED_LUA = """
local now_ms = tonumber(ARGV[1])
local spacing_ms = tonumber(ARGV[2])
local last_slot_ms = tonumber(redis.call('GET', KEYS[1]) or '0')
local blocked_until_ms = tonumber(redis.call('GET', KEYS[2]) or '0')
return math.max(now_ms, last_slot_ms + spacing_ms, blocked_until_ms)
"""
_COOLDOWN_LUA = """
local now_ms = tonumber(ARGV[1])
local cooldown_ms = tonumber(ARGV[2])
local minimum_ttl_ms = tonumber(ARGV[3])
local previous_ms = tonumber(redis.call('GET', KEYS[1]) or '0')
local blocked_until_ms = math.max(previous_ms, now_ms + cooldown_ms)
local ttl_ms = math.max(minimum_ttl_ms, blocked_until_ms - now_ms + 60000)
redis.call('SET', KEYS[1], tostring(blocked_until_ms), 'PX', ttl_ms)
return blocked_until_ms
"""


class ProviderQueueUnavailable(RuntimeError):
    """Safe failure raised when shared rate coordination cannot reach Redis."""


@lru_cache(maxsize=1)
def _redis_client() -> Redis:
    """Create a bounded Redis connection pool for provider request scheduling.

    Returns:
        A shared decoded Redis client using the configured runtime/test service.
    """
    return Redis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=0.5,
        socket_timeout=0.5,
        health_check_interval=20,
    )


@lru_cache(maxsize=1)
def _provider_pacing_policies() -> dict[str, dict]:
    """Load static provider pacing policy without reading runtime credentials.

    Returns:
        Policies from the committed market source registry, keyed by provider ID.
    """
    registry_path = CONFIG_ROOT / "market_sources.json"
    registry = json.loads(registry_path.read_text())
    return {
        provider_id: contract["request_pacing"]
        for provider_id, contract in registry.get("providers", {}).items()
        if isinstance(contract, dict) and isinstance(contract.get("request_pacing"), dict)
    }


def _bucket_keys(provider_id: str) -> tuple[str, str]:
    """Map a fixed provider ID to its shared slot and cooldown keys.

    Args:
        provider_id: Server-configured provider ID, never a request-supplied key.

    Returns:
        Redis keys shared across the API and all ingestion workers for that source.

    Raises:
        ValueError: The provider does not have a rate-limit bucket configured.
    """
    if provider_id not in _ALLOWED_BUCKETS:
        raise ValueError("该行情源没有配置请求限流队列")
    prefix = f"market:provider-quota:{provider_id}"
    return f"{prefix}:next-slot", f"{prefix}:blocked-until"


def reserve_provider_slot(
    provider_id: str,
    minimum_spacing_seconds: int | None = None,
    *,
    now_ms: int | None = None,
    client: Redis | None = None,
) -> int:
    """Reserve the next global provider request slot and return its wait time.

    Args:
        provider_id: Provider bucket shared by all keys in the configured account.
        minimum_spacing_seconds: Minimum delay between this project's requests.
        now_ms: Optional UTC epoch milliseconds for deterministic tests.
        client: Optional isolated Redis client; production uses the service cache.

    Returns:
        Milliseconds to wait before issuing the provider request; the first slot
        is immediate. Callers must wait before making the outbound HTTP request.

    Raises:
        ValueError: Provider or spacing configuration is invalid.
        ProviderQueueUnavailable: Redis could not safely reserve a shared slot.
    """
    _bucket_keys(provider_id)
    policy = _provider_pacing_policies().get(provider_id, {})
    spacing_seconds = (
        policy.get("safe_spacing_seconds")
        if minimum_spacing_seconds is None
        else minimum_spacing_seconds
    )
    if not isinstance(spacing_seconds, int) or not 1 <= spacing_seconds <= 3600:
        raise ValueError("行情供应商请求间隔配置无效")
    current_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    next_key, blocked_key = _bucket_keys(provider_id)
    try:
        slot_ms = int((client or _redis_client()).eval(
            _RESERVE_SLOT_LUA,
            2,
            next_key,
            blocked_key,
            current_ms,
            spacing_seconds * 1000,
            _STATE_TTL_MS,
        ))
    except RedisError:
        raise ProviderQueueUnavailable("共享行情限流队列暂不可用，已暂停上游请求") from None
    return max(0, slot_ms - current_ms)


@contextmanager
def provider_request_slot(
    provider_id: str,
    minimum_spacing_seconds: int | None = None,
    *,
    client: Redis | None = None,
):
    """Serialize one outbound call and enforce spacing at dispatch time.

    Args:
        provider_id: Fixed provider bucket shared by the API and workers.
        minimum_spacing_seconds: Optional test/administrative spacing override.
        client: Optional isolated Redis client; production uses the service cache.

    Yields:
        Seconds spent waiting for this provider's shared dispatch slot.

    Raises:
        ValueError: Provider or spacing policy is invalid.
        ProviderQueueUnavailable: Redis cannot safely coordinate dispatch.

    Temporal constraint:
        The caller must perform exactly one provider request inside the context;
        the distributed lock remains held through its HTTP response, preventing
        concurrent API/worker dispatches from overtaking reserved timestamps.
    """
    _bucket_keys(provider_id)
    policy = _provider_pacing_policies().get(provider_id, {})
    spacing_seconds = (
        policy.get("safe_spacing_seconds")
        if minimum_spacing_seconds is None
        else minimum_spacing_seconds
    )
    if not isinstance(spacing_seconds, int) or not 1 <= spacing_seconds <= 3600:
        raise ValueError("行情供应商请求间隔配置无效")
    redis_client = client or _redis_client()
    prefix = f"market:provider-quota:{provider_id}"
    next_key, blocked_key = _bucket_keys(provider_id)
    lock = redis_client.lock(
        f"{prefix}:dispatch-lock", timeout=60, blocking_timeout=3600,
    )
    queue_started = time.monotonic()
    waited_seconds = 0.0
    while True:
        try:
            acquired = lock.acquire(blocking=True)
        except RedisError:
            raise ProviderQueueUnavailable("共享行情限流队列暂不可用，已暂停上游请求") from None
        if not acquired:
            raise ProviderQueueUnavailable("共享行情限流队列等待超时，已暂停上游请求")
        current_ms = int(time.time() * 1000)
        try:
            allowed_ms = int(redis_client.eval(
                _NEXT_ALLOWED_LUA, 2, next_key, blocked_key,
                current_ms, spacing_seconds * 1000,
            ))
        except RedisError:
            try:
                lock.release()
            except RedisError:
                pass
            raise ProviderQueueUnavailable("共享行情限流队列暂不可用，已暂停上游请求") from None
        wait_ms = max(0, allowed_ms - current_ms)
        if wait_ms:
            try:
                lock.release()
            except RedisError:
                raise ProviderQueueUnavailable("共享行情限流队列暂不可用，已暂停上游请求") from None
            delay_seconds = wait_ms / 1000
            time.sleep(delay_seconds)
            waited_seconds += delay_seconds
            continue
        try:
            reserve_provider_slot(
                provider_id, spacing_seconds, now_ms=current_ms, client=redis_client,
            )
        except (RedisError, ProviderQueueUnavailable):
            try:
                lock.release()
            except RedisError:
                pass
            raise ProviderQueueUnavailable("共享行情限流队列暂不可用，已暂停上游请求") from None
        try:
            yield max(waited_seconds, time.monotonic() - queue_started)
        except Exception as exc:
            retry_after_seconds = getattr(exc, "retry_after_seconds", None)
            if isinstance(retry_after_seconds, int):
                try:
                    defer_provider_after_limit(
                        provider_id, retry_after_seconds, client=redis_client,
                    )
                except ProviderQueueUnavailable:
                    pass
            raise
        finally:
            try:
                lock.release()
            except RedisError:
                pass
        return


def defer_provider_after_limit(
    provider_id: str,
    cooldown_seconds: int,
    *,
    now_ms: int | None = None,
    client: Redis | None = None,
) -> int:
    """Extend the shared provider cooldown after an HTTP 429 response.

    Args:
        provider_id: Fixed provider ID whose account/asset-class quota was hit.
        cooldown_seconds: Sanitized Retry-After seconds; longer than one hour is honored.
        now_ms: Optional UTC epoch milliseconds for deterministic tests.
        client: Optional isolated Redis client; production uses the service cache.

    Returns:
        UTC epoch milliseconds when the next provider call may be scheduled.

    Raises:
        ValueError: Provider or cooldown configuration is invalid.
        ProviderQueueUnavailable: Redis could not persist the shared cooldown.
    """
    if not isinstance(cooldown_seconds, int) or not 1 <= cooldown_seconds <= 31_536_000:
        raise ValueError("行情供应商冷却时长配置无效")
    policy = _provider_pacing_policies().get(provider_id, {})
    policy_cooldown = policy.get("cooldown_on_429_seconds", 60)
    if not isinstance(policy_cooldown, int) or not 1 <= policy_cooldown <= 3600:
        raise ValueError("行情供应商429冷却配置无效")
    cooldown_seconds = max(cooldown_seconds, policy_cooldown)
    current_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    _next_key, blocked_key = _bucket_keys(provider_id)
    try:
        blocked_until_ms = int((client or _redis_client()).eval(
            _COOLDOWN_LUA,
            1,
            blocked_key,
            current_ms,
            cooldown_seconds * 1000,
            _STATE_TTL_MS,
        ))
    except RedisError:
        raise ProviderQueueUnavailable("共享行情限流队列暂不可用，已暂停上游请求") from None
    return blocked_until_ms
