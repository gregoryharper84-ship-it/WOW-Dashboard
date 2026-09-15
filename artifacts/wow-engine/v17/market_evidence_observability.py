"""Process-level market-evidence counters and per-run deltas.

A bare ``RUNDOWN_SCHEMA_UNRECOGNISED`` in a log line says nothing about how the
run got there.  These counters make the acquisition lane legible: how many
snapshot requests were actually issued, how many were served from cache or
collapsed by single-flight, and how each provider refusal was classified.

Counters are diagnostics only.  Nothing here can change a model status, a
probability, a ranking, or execution authority, and no counter ever holds a
credential, a price, or a participant name.
"""
from __future__ import annotations

import threading
from typing import Any

CAN_EXECUTE = False

COUNTER_NAMES = (
    "rundown_snapshot_requests",
    "rundown_provider_calls",
    "rundown_cache_hits",
    "rundown_singleflight_hits",
    "rundown_429_burst",
    "rundown_429_quota_exhausted",
    "rundown_429_unknown",
    "rundown_429_retries",
    "rundown_schema_unrecognised",
    "rundown_market_catalog_rejected",
    "rundown_snapshot_without_prices",
    "rundown_auth_failures",
    "rundown_snapshot_ok",
)

_LOCK = threading.Lock()
_COUNTERS: dict[str, int] = {name: 0 for name in COUNTER_NAMES}


def increment(name: str, amount: int = 1) -> None:
    if name not in _COUNTERS:
        return
    with _LOCK:
        _COUNTERS[name] += int(amount)


def counters() -> dict[str, int]:
    with _LOCK:
        return dict(_COUNTERS)


def reset() -> None:
    """Test/rollback seam.  Never called on the production request path."""
    with _LOCK:
        for name in _COUNTERS:
            _COUNTERS[name] = 0


def run_delta(before: dict[str, int], after: dict[str, int] | None = None) -> dict[str, int]:
    """Counters attributable to one research run."""
    after = after if after is not None else counters()
    return {name: int(after.get(name, 0)) - int(before.get(name, 0)) for name in COUNTER_NAMES}


def classify_failure_counter(code: Any, status: Any = None) -> str | None:
    """Map a typed provider failure code onto the counter it belongs to."""
    token = str(code or "").upper()
    try:
        http = int(status) if status is not None else None
    except (TypeError, ValueError):
        http = None
    if token.endswith("BURST_THROTTLED"):
        return "rundown_429_burst"
    if token.endswith("QUOTA_EXHAUSTED"):
        return "rundown_429_quota_exhausted"
    if http == 429 or token.endswith("_HTTP_429") or token.endswith("HTTP_429_UNKNOWN"):
        return "rundown_429_unknown"
    if http in {401, 403} or token.endswith("_HTTP_401") or token.endswith("_HTTP_403"):
        return "rundown_auth_failures"
    if "MARKET_CATALOG_NOT_ODDS_SNAPSHOT" in token:
        return "rundown_market_catalog_rejected"
    if "SNAPSHOT_WITHOUT_PRICES" in token:
        return "rundown_snapshot_without_prices"
    if "SCHEMA_UNRECOGNISED" in token or "SCHEMA_UNRECOGNIZED" in token:
        return "rundown_schema_unrecognised"
    return None


def record_failure(code: Any, status: Any = None) -> None:
    counter = classify_failure_counter(code, status)
    if counter:
        increment(counter)


__all__ = [
    "CAN_EXECUTE",
    "COUNTER_NAMES",
    "classify_failure_counter",
    "counters",
    "increment",
    "record_failure",
    "reset",
    "run_delta",
]
