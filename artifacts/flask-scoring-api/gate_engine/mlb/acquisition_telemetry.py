"""
gate_engine/mlb/acquisition_telemetry.py
==========================================
Route/dependency latency telemetry and WOW scan status classification.

Status classes emitted in route responses and scan summaries:

  BACKEND_HEALTH         OK
  MLB_DATA_ACQUISITION   OK | DEGRADED_LATENCY | DATA_ACQUISITION_PENDING
  ODDS_INTERNAL_AUTH     OK | AUTH_CONTRACT_FAIL
  PLAYER_IDENTITY_CACHE  HIT | MISS | UNAVAILABLE

Design
------
- Pure Python, no external dependencies.
- Thread-safe in-process ring buffer (deque, max 1000 entries).
- None of these statuses maps to NO_PLAY, model rejection, 504, or backend
  outage — they are observability signals only.
- Never logs credentials.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

# ── Status constants ──────────────────────────────────────────────────────────

# Backend-level health (infra / gunicorn / process)
BACKEND_HEALTH_OK = "OK"

# MLB data acquisition — the slow pybaseball / Statcast path
MLB_DATA_ACQ_OK              = "OK"
MLB_DATA_ACQ_DEGRADED        = "DEGRADED_LATENCY"    # completed but slow (>10s)
MLB_DATA_ACQ_PENDING         = "DATA_ACQUISITION_PENDING"  # in-flight
MLB_DATA_ACQ_FAILED          = "FETCH_FAILED"

# Odds API internal route auth
ODDS_AUTH_OK               = "OK"
ODDS_AUTH_CONTRACT_FAIL    = "AUTH_CONTRACT_FAIL"  # wrong / missing header

# Player identity cache
IDENTITY_CACHE_HIT         = "HIT"
IDENTITY_CACHE_MISS        = "MISS"
IDENTITY_CACHE_UNAVAILABLE = "UNAVAILABLE"  # DB unreachable

# Thresholds
_DEGRADED_LATENCY_MS = 10_000   # 10 s — acquisition is "slow" above this


# ── Event dataclass ───────────────────────────────────────────────────────────

@dataclass
class AcquisitionEvent:
    route:           str
    dependency:      str          # e.g. "pybaseball_statcast", "player_identity_cache"
    cache_hit:       Optional[bool]
    elapsed_ms:      float
    status_class:    str          # one of the constants above
    candidate_count: int = 0
    error_class:     Optional[str] = None
    ts:              float = field(default_factory=time.monotonic)


# ── In-process ring buffer ────────────────────────────────────────────────────

_buffer_lock = threading.Lock()
_buffer: deque[AcquisitionEvent] = deque(maxlen=1000)


def record_event(event: AcquisitionEvent) -> None:
    """Append a telemetry event (thread-safe, non-blocking)."""
    with _buffer_lock:
        _buffer.append(event)


def recent_events(n: int = 50) -> list[AcquisitionEvent]:
    """Return up to *n* most-recent events (newest last)."""
    with _buffer_lock:
        return list(_buffer)[-n:]


def clear_events() -> None:
    """Flush the ring buffer (used in tests)."""
    with _buffer_lock:
        _buffer.clear()


# ── Scan summary helper ───────────────────────────────────────────────────────

def get_scan_summary() -> dict:
    """Return a structured scan-status dict suitable for inclusion in responses.

    Classifies the backend health, MLB acquisition, Odds auth, and cache
    based on recent telemetry.  Never contains credentials.
    """
    events = recent_events(200)
    if not events:
        return {
            "backend_health":        BACKEND_HEALTH_OK,
            "mlb_data_acquisition":  MLB_DATA_ACQ_OK,
            "odds_internal_auth":    ODDS_AUTH_OK,
            "player_identity_cache": IDENTITY_CACHE_HIT,
            "events_observed":       0,
        }

    # MLB acquisition status
    mlb_events = [e for e in events if "pybaseball" in e.dependency or "statcast" in e.dependency]
    mlb_status = MLB_DATA_ACQ_OK
    if mlb_events:
        slowest = max(e.elapsed_ms for e in mlb_events)
        if any(e.status_class == MLB_DATA_ACQ_FAILED for e in mlb_events):
            mlb_status = MLB_DATA_ACQ_FAILED
        elif slowest > _DEGRADED_LATENCY_MS:
            mlb_status = MLB_DATA_ACQ_DEGRADED
    elif any(e.dependency == "player_identity_cache" and e.status_class == IDENTITY_CACHE_MISS
             for e in events):
        mlb_status = MLB_DATA_ACQ_PENDING

    # Odds auth status
    odds_events = [e for e in events if "odds_internal" in e.dependency]
    odds_auth = ODDS_AUTH_OK
    if any(e.status_class == ODDS_AUTH_CONTRACT_FAIL for e in odds_events):
        odds_auth = ODDS_AUTH_CONTRACT_FAIL

    # Cache hit rate
    cache_events = [e for e in events if e.dependency == "player_identity_cache"]
    if cache_events:
        hits = sum(1 for e in cache_events if e.cache_hit)
        cache_status = IDENTITY_CACHE_HIT if hits > 0 else IDENTITY_CACHE_MISS
        if all(e.status_class == IDENTITY_CACHE_UNAVAILABLE for e in cache_events):
            cache_status = IDENTITY_CACHE_UNAVAILABLE
    else:
        cache_status = IDENTITY_CACHE_HIT  # no cache lookups yet — not degraded

    # Aggregate avg latency for MLB path
    avg_mlb_ms = (
        round(sum(e.elapsed_ms for e in mlb_events) / len(mlb_events), 1)
        if mlb_events else None
    )

    return {
        "backend_health":        BACKEND_HEALTH_OK,   # infra is always OK if we're responding
        "mlb_data_acquisition":  mlb_status,
        "odds_internal_auth":    odds_auth,
        "player_identity_cache": cache_status,
        "avg_mlb_acquisition_ms": avg_mlb_ms,
        "events_observed":       len(events),
        # These never become NO_PLAY, model rejection, 504, or backend outage:
        "_note": (
            "DEGRADED_LATENCY means data was fetched successfully but slowly. "
            "AUTH_CONTRACT_FAIL means the wrong internal header was sent; "
            "upstream data was obtained via direct API fallback."
        ),
    }


# ── Context-manager helper for timing ────────────────────────────────────────

class timed_acquisition:
    """Context manager that records an AcquisitionEvent on exit.

    Usage::

        with timed_acquisition(
            route="/wow/mlb/pitcher",
            dependency="pybaseball_statcast",
        ) as ctx:
            result = _get_pitcher_savant(first, last)
            ctx.cache_hit = False
            ctx.candidate_count = 1

    ctx.status_class is set to MLB_DATA_ACQ_DEGRADED when elapsed > threshold,
    MLB_DATA_ACQ_FAILED if an exception propagates, MLB_DATA_ACQ_OK otherwise.
    """

    def __init__(self, route: str, dependency: str):
        self.route           = route
        self.dependency      = dependency
        self.cache_hit: Optional[bool] = None
        self.candidate_count = 0
        self.error_class: Optional[str] = None
        self._start: float = 0.0
        self.elapsed_ms: float = 0.0
        self.status_class: str = MLB_DATA_ACQ_OK

    def __enter__(self) -> "timed_acquisition":
        self._start = time.monotonic()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.elapsed_ms = (time.monotonic() - self._start) * 1000
        if exc_type is not None:
            self.status_class = MLB_DATA_ACQ_FAILED
            self.error_class  = exc_type.__name__
        elif self.elapsed_ms > _DEGRADED_LATENCY_MS:
            self.status_class = MLB_DATA_ACQ_DEGRADED
        else:
            self.status_class = MLB_DATA_ACQ_OK
        record_event(AcquisitionEvent(
            route           = self.route,
            dependency      = self.dependency,
            cache_hit       = self.cache_hit,
            elapsed_ms      = self.elapsed_ms,
            status_class    = self.status_class,
            candidate_count = self.candidate_count,
            error_class     = self.error_class,
        ))
        return False  # do not suppress exceptions
