"""
gate_engine/kalshi_wx_shadow_capture.py
WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW — Step 10D (non-blocking)

Minimal flag-gated capture bridge: reads already-computed deterministic weather
values from the /wow/kalshi/weather/evaluate route, constructs a frozen
WeatherResearchSnapshot, and dispatches the shadow orchestrator in a daemon
thread so the live HTTP response is never blocked by Claude latency.

WHAT THIS MODULE DOES
  maybe_fire_shadow_snapshot() receives local variables already computed at
  the insertion point in the route handler, constructs a WeatherResearchSnapshot,
  then starts a daemon thread that calls run_shadow_orchestrator().  The function
  returns to the route immediately after .start().

WHAT THIS MODULE DOES NOT DO
  - Does not call any fetch function or make any new network request.
  - Does not read any value that is not already a local variable at the
    insertion point.
  - Does not alter, substitute, recompute, or feed anything back into the
    deterministic route.
  - Does not interpret evidence, call Claude directly on the request thread,
    produce advisory ceilings, or write shadow analytical results from the
    request thread.
  - Does not propagate any exception to its caller under any circumstances.

NON-BLOCKING DISPATCH
  Snapshot construction and SDK client validation happen synchronously on the
  request thread (both are fast — pure Python, no I/O).  The orchestrator call
  (which makes 5 sequential Anthropic API calls) is dispatched to a daemon thread
  via _Thread, matching the existing fire-and-forget pattern used throughout
  app.py (threading.Thread(target=_run, daemon=True).start()).

CONCURRENCY LIMIT
  _SHADOW_SEMAPHORE = Semaphore(1) ensures at most one shadow run is in flight
  per worker process at any time.  A second concurrent request acquires(False)
  → logs SHADOW_CAPTURE_SKIPPED and returns immediately, never queuing work.
  The semaphore is always released in the daemon thread's finally block.
  _Thread is a module-level alias for threading.Thread, patchable in tests
  without touching the global threading module.

UNAVAILABLE SENTINEL
  Fields not exposed at the capture insertion point are set to UNAVAILABLE_SENTINEL
  explicitly — never None, never fabricated, never inferred.
    dict  fields  → {"_status": UNAVAILABLE_SENTINEL}
    tuple fields  → (UNAVAILABLE_SENTINEL,)
  Affected: nws_gridpoint_forecast, open_meteo_forecast, noaa_ncei_forecast,
            official_observations_at_cutoff, source_provenance, source_disagreements

FEATURE FLAG (defense in depth)
  KALSHI_WX_SHADOW_AGENT_ENABLED checked first (before any import or I/O),
  both in app.py and independently inside this module.  Default "false".

EXCEPTION SAFETY
  The entire active body (construction + thread dispatch) is wrapped in a
  try/except that catches every exception, logs it as a shadow failure, and
  returns None.  The daemon thread also has its own inner try/except so that
  orchestrator failures are logged without affecting anything.
"""
from __future__ import annotations

import logging
import os
import threading as _threading
import uuid
from datetime import datetime, timezone
from typing import Optional

_logger = logging.getLogger(__name__)

# ── Feature flag ───────────────────────────────────────────────────────────────
# Module-level bool evaluated once at import time.
# Tests may patch: patch("gate_engine.kalshi_wx_shadow_capture._SHADOW_ENABLED", True)
_SHADOW_ENABLED: bool = (
    os.environ.get("KALSHI_WX_SHADOW_AGENT_ENABLED", "false").strip().lower() == "true"
)

# ── Thread alias (patchable without touching the global threading module) ──────
# Tests patch: patch("gate_engine.kalshi_wx_shadow_capture._Thread", _SyncThread)
_Thread = _threading.Thread

# ── Concurrency semaphore — at most 1 shadow run in flight per worker ──────────
# acquire(blocking=False): skip if already running (never queue duplicate work).
# Released unconditionally in the daemon thread's finally block.
# Tests may patch: patch("gate_engine.kalshi_wx_shadow_capture._SHADOW_SEMAPHORE", ...)
_SHADOW_SEMAPHORE = _threading.Semaphore(1)

# ── UNAVAILABLE sentinel ───────────────────────────────────────────────────────
UNAVAILABLE_SENTINEL: str = "UNAVAILABLE_NOT_EXPOSED_BY_DETERMINISTIC_FETCH"
_UNAVAIL_DICT: dict  = {"_status": UNAVAILABLE_SENTINEL}
_UNAVAIL_TUPLE: tuple = (UNAVAILABLE_SENTINEL,)


# ── Helpers (pure functions — no side effects, no I/O) ────────────────────────

def _derive_source_failures(tier_detail: dict) -> tuple:
    """
    Derive source_failures from the tier_detail dict returned by
    _fetch_forecast_high_tiered().

    tier_detail structure:
        {"nws": {"attempted": bool, "ok": bool, "error": str|None}, ...}

    Returns a tuple of strings — one entry per tier attempted but failed.
    """
    failures: list[str] = []
    for tier_name, info in tier_detail.items():
        if not isinstance(info, dict):
            continue
        if info.get("attempted") and not info.get("ok"):
            error_msg = info.get("error") or "unknown_error"
            failures.append(f"{tier_name}: {error_msg}")
    return tuple(failures)


def _derive_readiness_state(forecast_high: Optional[float]) -> str:
    """
    Transparent classification from forecast_high:
      not None → "READY"
      None     → "DATA_UNAVAILABLE"
    """
    return "READY" if forecast_high is not None else "DATA_UNAVAILABLE"


def _derive_source_timestamps(
    weather_data_source_tier: str,
    source_cutoff_timestamp: str,
) -> dict:
    """
    Approximate source_timestamps from already-available values.
    Records the cutoff timestamp for the winning tier only.
    """
    return {weather_data_source_tier: source_cutoff_timestamp}


def _build_shadow_sdk_client():
    """
    Build an Anthropic SDK client from environment variables.
    Raises RuntimeError if SDK not installed or no API key found.
    """
    try:
        import anthropic as _sdk
    except ImportError as exc:
        raise RuntimeError(
            "SHADOW_CAPTURE: anthropic SDK not installed"
        ) from exc

    api_key = (
        os.environ.get("AI_INTEGRATIONS_ANTHROPIC_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
    )
    if not api_key:
        raise RuntimeError(
            "SHADOW_CAPTURE: no Anthropic API key — "
            "set AI_INTEGRATIONS_ANTHROPIC_API_KEY or ANTHROPIC_API_KEY"
        )

    base_url = os.environ.get("AI_INTEGRATIONS_ANTHROPIC_BASE_URL")
    if base_url:
        return _sdk.Anthropic(api_key=api_key, base_url=base_url)
    return _sdk.Anthropic(api_key=api_key)


# ── Main capture entry point ──────────────────────────────────────────────────

def maybe_fire_shadow_snapshot(
    city: str,
    station: str,
    market_date: str,
    forecast_high: Optional[float],
    weather_data_source_tier: str,
    sigma_f: float,
    horizon_hours: float,
    tier_detail: dict,
) -> None:
    """
    Flag-gated, non-blocking shadow capture.

    Constructs WeatherResearchSnapshot synchronously (fast), then dispatches
    run_shadow_orchestrator() to a daemon thread.  Returns immediately after
    thread.start() so the production HTTP response is never blocked.

    Returns None always.  Exception-safe — no exception can reach the caller.
    """
    # ── Independent second flag gate (defense in depth) ───────────────────────
    if not _SHADOW_ENABLED:
        return

    # ── Outer exception fence — nothing propagates to the caller ─────────────
    try:
        # Lazy imports — only executed when flag is on.
        from gate_engine.kalshi_wx_shadow_snapshot import WeatherResearchSnapshot
        from gate_engine.kalshi_wx_shadow_capability_boundary import CapabilityBoundary
        from gate_engine.kalshi_wx_shadow_ledger import get_default_ledger
        from gate_engine.kalshi_wx_shadow_orchestrator import run_shadow_orchestrator

        # ── Derive values from already-computed route locals ──────────────────
        source_cutoff = (
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        )
        snapshot_id        = f"wx-capture-{uuid.uuid4()}"
        canonical_event_id = f"kalshi-nhigh-{city}-{market_date}"
        readiness_state    = _derive_readiness_state(forecast_high)
        source_failures    = _derive_source_failures(tier_detail)
        source_timestamps  = _derive_source_timestamps(
            weather_data_source_tier, source_cutoff
        )
        fh_float = float(forecast_high) if forecast_high is not None else 0.0

        # ── Construct snapshot (frozen dataclass, fast, no I/O) ───────────────
        snapshot = WeatherResearchSnapshot(
            research_snapshot_id=snapshot_id,
            canonical_event_id=canonical_event_id,
            city=city,
            station=station,
            market_date=market_date,
            source_cutoff_timestamp=source_cutoff,
            nws_gridpoint_forecast=_UNAVAIL_DICT,          # sentinel
            open_meteo_forecast=_UNAVAIL_DICT,             # sentinel
            noaa_ncei_forecast=_UNAVAIL_DICT,              # sentinel
            official_observations_at_cutoff=_UNAVAIL_DICT, # sentinel
            forecast_high_used_by_deterministic_model=fh_float,
            weather_data_source_tier=weather_data_source_tier,
            forecast_horizon_hours=float(horizon_hours),
            sigma_f=float(sigma_f),
            deterministic_weather_readiness_state=readiness_state,
            source_timestamps=source_timestamps,
            source_provenance=_UNAVAIL_DICT,               # sentinel
            source_failures=source_failures,               # derived
            source_disagreements=_UNAVAIL_TUPLE,           # sentinel
        )

        # ── Validate SDK client before dispatching thread ─────────────────────
        sdk_client = _build_shadow_sdk_client()

        # ── Concurrency gate — skip if a run is already in flight ─────────────
        if not _SHADOW_SEMAPHORE.acquire(blocking=False):
            _logger.info(
                "SHADOW_CAPTURE_SKIPPED city=%s date=%s "
                "reason=concurrent_run_already_in_flight",
                city, market_date,
            )
            return

        # ── Capture closure vars for daemon thread ────────────────────────────
        # All are immutable or thread-safe by construction.
        _city        = city
        _market_date = market_date
        _snap_id     = snapshot_id

        def _fire_orchestrator():
            """Daemon thread body — runs fully async from the HTTP request."""
            try:
                run_shadow_orchestrator(
                    city=_city,
                    date=_market_date,
                    run_id=_snap_id,
                    sdk_client=sdk_client,
                    capability_boundary=CapabilityBoundary(),
                    ledger=get_default_ledger(),
                    snapshot=snapshot,
                )
            except Exception as exc:
                _logger.warning(
                    "SHADOW_ORCHESTRATOR_FAILURE city=%s date=%s "
                    "error_type=%s error=%s",
                    _city, _market_date, type(exc).__name__, exc,
                    exc_info=True,
                )
            finally:
                # Always release — even if the orchestrator raised.
                _SHADOW_SEMAPHORE.release()

        # ── Dispatch — returns immediately; route is now unblocked ────────────
        _Thread(
            target=_fire_orchestrator,
            daemon=True,
            name=f"kalshi-wx-shadow-{snapshot_id}",
        ).start()

    except Exception as exc:
        # Outer fence: catches snapshot construction failure, SDK failure,
        # Thread.start() failure, or any import error.  Never propagates.
        _logger.warning(
            "SHADOW_CAPTURE_FAILURE city=%s date=%s error_type=%s error=%s",
            city, market_date, type(exc).__name__, exc,
            exc_info=True,
        )
