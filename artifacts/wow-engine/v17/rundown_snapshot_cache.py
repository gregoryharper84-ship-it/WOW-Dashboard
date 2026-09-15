"""Single-flight + TTL cache for TheRundown sport/date odds snapshots.

Scoring a slate used to mean one provider request per scored row, so a 12-game
board produced 12 identical sport/date fetches — avoidable quota pressure, and
rows whose market evidence carried different snapshot timestamps inside a single
research run.

This layer collapses identical requests: the first caller fetches, every
concurrent caller for the same key awaits that same in-flight result, and the
normalised snapshot is reused for a short TTL.  One research run therefore sees
one provider snapshot with one timestamp.

The cache key carries every dimension that materially changes the response, so a
different sport, date, market set, affiliate set, main-line flag or closed-event
flag can never collide.  Nothing is cached unless the fetch succeeded: a typed
failure is returned to its caller and the next caller retries cleanly.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Hashable

CAN_EXECUTE = False

_LOCK = threading.Lock()
_ENTRIES: dict[Hashable, "_Entry"] = {}
_INFLIGHT: dict[Hashable, "_Flight"] = {}
_STATS = {
    "requests": 0,
    "provider_calls": 0,
    "cache_hits": 0,
    "singleflight_hits": 0,
}


@dataclass
class _Entry:
    value: Any
    stored_at: float


class _Flight:
    """One in-progress fetch that later callers block on instead of duplicating."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.value: Any = None
        self.error: BaseException | None = None
        self.waiters = 0


def ttl_seconds() -> float:
    try:
        configured = float(os.environ.get("WOW_RUNDOWN_SNAPSHOT_TTL_SECONDS", "90"))
    except ValueError:
        configured = 90.0
    return max(0.0, min(configured, 900.0))


def enabled() -> bool:
    return os.environ.get("WOW_RUNDOWN_SNAPSHOT_CACHE_ENABLED", "true").strip().lower() == "true"


def snapshot_key(
    *,
    provider: str,
    capability: str,
    sport_key: str,
    sport_id: Any,
    slate_date: str,
    market_ids: Any = None,
    affiliate_ids: Any = None,
    main_line: Any = None,
    hide_closed: Any = None,
) -> tuple:
    """Every dimension that materially changes the provider response."""

    def _tuple(value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, (list, tuple, set, frozenset)):
            return tuple(sorted(str(item) for item in value))
        return (str(value),)

    return (
        str(provider).upper(),
        str(capability),
        str(sport_key),
        str(sport_id),
        str(slate_date),
        _tuple(market_ids),
        _tuple(affiliate_ids),
        None if main_line is None else bool(main_line),
        None if hide_closed is None else bool(hide_closed),
    )


def _fresh(entry: _Entry, ttl: float) -> bool:
    return ttl > 0 and (time.monotonic() - entry.stored_at) <= ttl


def get_or_fetch(
    key: Hashable,
    fetcher: Callable[[], Any],
    *,
    cacheable: Callable[[Any], bool] | None = None,
) -> tuple[Any, str]:
    """Return ``(value, origin)`` where origin is CACHE, SINGLEFLIGHT or PROVIDER.

    ``cacheable`` decides whether a result is worth storing — a typed provider
    failure must never be pinned for the rest of the TTL.
    """
    with _LOCK:
        _STATS["requests"] += 1

    if not enabled():
        with _LOCK:
            _STATS["provider_calls"] += 1
        return fetcher(), "PROVIDER"

    ttl = ttl_seconds()

    with _LOCK:
        entry = _ENTRIES.get(key)
        if entry is not None and _fresh(entry, ttl):
            _STATS["cache_hits"] += 1
            return entry.value, "CACHE"
        if entry is not None:
            _ENTRIES.pop(key, None)

        flight = _INFLIGHT.get(key)
        if flight is not None:
            flight.waiters += 1
            _STATS["singleflight_hits"] += 1
            leader = False
        else:
            flight = _Flight()
            _INFLIGHT[key] = flight
            _STATS["provider_calls"] += 1
            leader = True

    if not leader:
        flight.done.wait()
        if flight.error is not None:
            raise flight.error
        return flight.value, "SINGLEFLIGHT"

    try:
        value = fetcher()
    except BaseException as exc:  # noqa: BLE001 - re-raised to every waiter
        flight.error = exc
        with _LOCK:
            _INFLIGHT.pop(key, None)
        flight.done.set()
        raise
    else:
        flight.value = value
        keep = ttl > 0 and (cacheable(value) if cacheable is not None else True)
        with _LOCK:
            if keep:
                _ENTRIES[key] = _Entry(value=value, stored_at=time.monotonic())
            _INFLIGHT.pop(key, None)
        flight.done.set()
        return value, "PROVIDER"


def reset() -> None:
    """Test/rollback seam.  Never called on the production request path."""
    with _LOCK:
        _ENTRIES.clear()
        _INFLIGHT.clear()
        for name in _STATS:
            _STATS[name] = 0


def stats() -> dict[str, int]:
    with _LOCK:
        return dict(_STATS)


__all__ = [
    "CAN_EXECUTE",
    "enabled",
    "get_or_fetch",
    "reset",
    "snapshot_key",
    "stats",
    "ttl_seconds",
]
