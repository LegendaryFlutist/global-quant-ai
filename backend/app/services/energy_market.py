"""Persistent energy reference refresh, independent of browser and legacy backfills."""

import time
import re

from sqlalchemy import select

from app.adapters.energy_public import ENERGY_SYMBOLS, PARSER_VERSION, fetch_yahoo_energy
from app.core.db import SessionLocal
from app.domains.models import Asset, Alias, Membership, Provider, MarketData, Setting
from app.storage.redis_cache import cache_underlying_refresh, read_cached_underlying_refresh
from app.storage.provider_rate_limit import _redis_client
from app.services.energy_health import energy_health

POLL_SECONDS = {"15m": 300, "1d": 21600}
ENERGY_FRESHNESS = "Yahoo延迟参考；免费个人研究接口，无实时性或可用性保证"


def seed_energy_catalog(db):
    """Register separate public references without rewriting existing futures or keys.

    Return None; preserve customized asset bindings and all historical datasets.
    EIA's existing source identity remains EIA, now served by its no-key tables.
    """
    if db.get(Provider, "yahoo_energy") is None:
        db.add(Provider(id="yahoo_energy", name="Yahoo能源延迟参考（个人研究）", status="PUBLIC_NO_KEY",
            detail={"credential_required": False, "freshness_contract": ENERGY_FRESHNESS}))
    db.flush()
    for code, symbol in ENERGY_SYMBOLS.items():
        asset_id = f"yahoo_energy:{code}"
        if db.get(Asset, asset_id) is not None:
            continue
        db.add(Asset(id=asset_id, symbol=symbol, name="WTI原油连续参考" if code == "CL" else "Henry Hub天然气连续参考",
            underlying_id="crude_oil" if code == "CL" else "natural_gas", asset_type="commodity_indicator",
            venue="YAHOO_REFERENCE", priority=20 if code == "CL" else 21, verification="RESEARCH_ONLY",
            specification={"currency": "USD", "execution_eligible": False, "roll_rules": "UNVERIFIED",
                "market_data_source": {"provider_id": "yahoo_energy", "alias": symbol, "kind": "candles",
                    "intervals": ["15m", "1d"], "timezone": "America/New_York", "exchange": "NYM",
                    "freshness": ENERGY_FRESHNESS, "history_days": {"15m": 59, "1d": 1095},
                    "automatic_refresh": True}}))
        db.flush()
        db.add(Alias(asset_id=asset_id, provider_id="yahoo_energy", alias=symbol))
        db.add(Membership(asset_id=asset_id, sector="能源参考"))


def energy_status_key(asset_id, interval):
    """Return a deterministic per-stream setting key; contains no user credentials."""
    return f"market_refresh:{asset_id}:{interval}"


def read_energy_snapshot(db, asset_id, interval, now_ms=None):
    """Serve cache-only energy status to charts; HTTP polling never initiates upstream I/O.

    Return last real closed bars and recomputed freshness. Old cache is explicitly
    stale after its poll deadline; source age is distinct from request success.
    """
    current = int(now_ms if now_ms is not None else time.time()*1000)
    if asset_id not in {f"yahoo_energy:{code}" for code in ENERGY_SYMBOLS} or interval not in POLL_SECONDS:
        raise ValueError("未登记的能源流")
    result = read_cached_underlying_refresh(asset_id, interval) or {
        "asset_id": asset_id, "interval": interval, "closed_candles": [], "live_candle": None,
        "source": "yahoo_energy", "source_freshness": ENERGY_FRESHNESS, "research_only": True,
        "updated_at_ms": None, "source_event_at_ms": None,
    }
    status = db.get(Setting, energy_status_key(asset_id, interval))
    state = status.value if status else {}
    health = energy_health(result, state, interval, POLL_SECONDS[interval], current)
    result["health"] = health
    result["stale"] = health["stale"]
    result["refresh_state"] = state
    result.pop("refresh_error", None)
    if result["stale"]:
        result["refresh_error"] = health["collector_error"] or "行情或缓存偏旧；请查看分项状态，不能作为即时交易价格"
    return result


def fetch_energy_history(db, asset_id, interval, days=1095, now_ms=None):
    """Persist source-native closed bars and cache provisional display separately.

    UTC milliseconds determine a capped 59-day intraday / 1095-day daily window.
    Return actual coverage, proof hashes and requested versus available limits.
    No Binance product is written and no raw history is shortened or synthesized.
    """
    from app.services.underlying_market import _merge_save_bars, _set_provider_state
    from app.services.market_data import FIELDS
    if asset_id not in {f"yahoo_energy:{code}" for code in ENERGY_SYMBOLS} or interval not in POLL_SECONDS:
        raise ValueError("未登记的能源流")
    asset = db.get(Asset, asset_id)
    expected = ENERGY_SYMBOLS[asset_id.split(":")[1]]
    source = (asset.specification or {}).get("market_data_source", {}) if asset else {}
    if source.get("provider_id") != "yahoo_energy" or source.get("alias") != expected:
        raise ValueError("能源流绑定被修改，自动采集已停止")
    if not isinstance(days, int) or not 1 <= days <= 3650:
        raise ValueError("历史窗口必须为1至3650天")
    current = int(now_ms if now_ms is not None else time.time()*1000)
    lookback = min(days, 59 if interval == "15m" else 1095)
    closed, forming, evidence = fetch_yahoo_energy(expected, interval, current-lookback*86_400_000, current)
    if not closed:
        raise ValueError("能源源未返回已确认K线")
    received = evidence["received_at_ms"]
    provenance = {**evidence, "provider_symbol": expected, "instrument_identity": asset_id,
        "timestamp_alignment": "exchange_session", "mode": "delayed_continuous_reference",
        "roll_rules": "UNVERIFIED", "not_a_related_perpetual": True,
        "requested_lookback_days": days, "source_window_days": lookback}
    saved = _merge_save_bars(db, asset, interval, closed, provenance, received, lookback)
    payload = {"asset_id": asset_id, "symbol": asset.symbol, "interval": interval,
        "closed_candles": [{**row, **{field: str(row[field]) for field in FIELDS}} for row in closed[-100:]],
        "live_candle": ({**forming, **{field: str(forming[field]) for field in FIELDS}} if forming else None),
        "last_closed_at_ms": saved["last_close_ms"], "updated_at_ms": received,
        "source_event_at_ms": evidence["source_event_at_ms"], "source": "yahoo_energy",
        "source_freshness": evidence["source_freshness"] + ("；存在异常未确认K线，已忽略" if evidence.get("discarded_provisional_bars") else ""),
        "discarded_provisional_bars": evidence.get("discarded_provisional_bars", 0),
        "dataset_sha256": saved["sha256"],
        "research_only": True, "stale": received-evidence["source_event_at_ms"] > 90*60_000}
    cache_underlying_refresh(asset_id, interval, payload, 86400)
    _set_provider_state(db, "yahoo_energy", "CONNECTED", asset_id=asset_id, interval=interval,
        latest_at_ms=evidence["source_event_at_ms"])
    db.commit()
    return {**saved, "source": "yahoo_energy", "kind": "candles", "research_only": True,
        "requested_days": days, "source_window_days": lookback}


def energy_failure_code(exc):
    """Classify sanitized adapter failures without persisting arbitrary exception text.

    Return a fixed category or HTTP status digits so operators can distinguish
    connection failures from changed upstream schemas without exposing credentials.
    """
    from app.adapters.underlying_public import UnderlyingDataError
    if isinstance(exc, UnderlyingDataError):
        message = str(exc)
        match = re.fullmatch(r"能源公开源 HTTP (\d{3})", message)
        if match:
            return "UPSTREAM_HTTP_" + match[1]
        if message == "能源公开源暂时不可达":
            return "UPSTREAM_UNREACHABLE"
        return "SOURCE_VALIDATION"
    return type(exc).__name__


def refresh_streams_once(db, *, now_ms=None, client=None, fetch_candles=None, fetch_observations=None):
    """Refresh due energy streams under per-stream Redis locks and durable retry state.

    Return safe stream outcomes. First startup backfills capped history; subsequent
    cycles repair the recent window. Persist failures/backoff across process restart,
    honor provider cooldowns, and never delete prior successful data on an error.
    """
    from app.services.underlying_market import fetch_underlying_history
    current = int(now_ms if now_ms is not None else time.time()*1000)
    redis = client or _redis_client()
    candle_fetch = fetch_candles or fetch_energy_history
    observation_fetch = fetch_observations or fetch_underlying_history
    streams = [(f"yahoo_energy:{code}", interval, seconds) for interval, seconds in POLL_SECONDS.items() for code in ENERGY_SYMBOLS]
    streams += [(asset_id, "1d", 21600) for asset_id in ("eia:WTI_CUSHING_SPOT", "eia:HENRY_HUB_SPOT")]
    results = []
    for asset_id, interval, seconds in streams:
        current = int(now_ms if now_ms is not None else time.time()*1000)
        lock = redis.lock(f"energy-stream-lock:{asset_id}:{interval}", timeout=180, blocking_timeout=0)
        if not lock.acquire(blocking=False):
            continue
        try:
            key = energy_status_key(asset_id, interval)
            state_row = db.get(Setting, key, populate_existing=True)
            state = dict(state_row.value or {}) if state_row else {}
            due = int(state.get("next_attempt_at_ms", 0))
            # Upgrade old generic network backoff without shortening any 429 cooldown.
            if interval == "15m" and state.get("error_code") == "UnderlyingDataError":
                due = min(due, int(state.get("last_attempt_at_ms", current)) + seconds*1000)
            if state.get("error_code") == "SOURCE_VALIDATION" and state.get("parser_version") != PARSER_VERSION:
                due = current  # One retry after a parser upgrade, never a 429 bypass.
            if due > current:
                continue
            asset = db.get(Asset, asset_id)
            source = (asset.specification or {}).get("market_data_source", {}) if asset else {}
            if source.get("provider_id") != ("yahoo_energy" if asset_id.startswith("yahoo_energy:") else "eia"):
                continue
            state.update({"last_attempt_at_ms": current, "interval": interval, "poll_seconds": seconds, "parser_version": PARSER_VERSION})
            try:
                if asset_id.startswith("yahoo_energy:"):
                    previous = db.scalar(select(MarketData).where(MarketData.asset_id == asset_id, MarketData.interval == interval))
                    days = 1095 if previous is None else min(1095, max(3, int((current-previous.last_close_ms)/86_400_000)+3))
                    result = candle_fetch(db, asset_id, interval, days, now_ms=current)
                    latest = result["last_close_ms"]
                else:
                    result = observation_fetch(db, asset_id, "1d", 1095, now_ms=current)
                    latest = result["last_observed_at_ms"]
                state.update({"status": "OK", "failures": 0, "last_success_at_ms": current,
                    "latest_data_at_ms": latest, "next_attempt_at_ms": current+seconds*1000})
                state.pop("error_code", None)
            except Exception as exc:
                db.rollback()
                failures = int(state.get("failures", 0))+1
                delay = max(min(seconds, 60 * 2**min(failures, 8)), getattr(exc, "retry_after_seconds", None) or 0)
                state.update({"status": "ERROR", "failures": failures, "error_code": energy_failure_code(exc),
                    "next_attempt_at_ms": current+delay*1000})
            state_row = db.get(Setting, key, populate_existing=True)
            if state_row is None:
                db.add(Setting(key=key, value=state))
            else:
                state_row.value = state
            db.commit()
            results.append({"asset_id": asset_id, "interval": interval, "status": state["status"]})
        finally:
            if lock.owned():
                lock.release()
    return results


def main():
    """Run a restartable read-only public collector; never log credentials or bodies.

    Redis heartbeat and DB stream checkpoints expose health independently of the
    chart. Unexpected infrastructure failures retry on the next 15-second cycle.
    """
    import json
    while True:
        try:
            with SessionLocal() as db:
                outcomes = refresh_streams_once(db)
            _redis_client().set("energy-refresh:heartbeat", int(time.time()), ex=180)
            if outcomes:
                print(json.dumps({"energy_refresh": outcomes}), flush=True)
        except Exception as exc:
            print(json.dumps({"energy_refresh_error": type(exc).__name__}), flush=True)
        time.sleep(15)


if __name__ == "__main__":
    main()
