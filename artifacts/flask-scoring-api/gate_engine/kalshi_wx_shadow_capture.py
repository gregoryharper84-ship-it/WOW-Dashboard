"""
gate_engine/kalshi_wx_shadow_capture.py
WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW — Step 12.5 (durable DB queue)

Flag-gated shadow snapshot persistence bridge.

WHAT THIS MODULE DOES
  maybe_fire_shadow_snapshot() receives local variables already computed at
  the insertion point in the route handler, constructs a frozen
  WeatherResearchSnapshot, serialises it, and performs a single synchronous
  INSERT into kalshi_wx_shadow_snapshot_queue (status='PENDING').
  The live route immediately returns its production response after the call.

WHAT THIS MODULE DOES NOT DO
  - Does not call any fetch function or make any new network request.
  - Does not spawn any thread of any kind.
  - Does not call Claude, coordinate subagents, or build any SDK client.
  - Does not read any value that is not already a local variable at the
    insertion point in the route handler.
  - Does not alter, substitute, recompute, or feed anything back into
    the deterministic route.
  - Does not propagate any exception to its caller under any circumstances.

REMOVED RELATIVE TO PREVIOUS VERSION
  - Daemon-thread dispatch and concurrency guard (no async execution of any kind)
  - Orchestrator invocation (live route never calls the orchestrator)
  - SDK client construction (live route never calls Claude)

NEW BEHAVIOUR
  1. Construct WeatherResearchSnapshot (same fields, same UNAVAILABLE sentinels
     for fields not exposed by the deterministic pipeline — unchanged).
  2. Call insert_shadow_snapshot(snapshot) from gate_engine.kalshi_wx_shadow_db
     (single synchronous INSERT, no thread).
  3. Log SHADOW_CAPTURE_OK on success.
  4. Return None.

UNAVAILABLE SENTINEL
  Fields not exposed at the capture insertion point are set to
  UNAVAILABLE_SENTINEL explicitly — never None, never fabricated, never
  inferred.
    dict  fields → {"_status": UNAVAILABLE_SENTINEL}
    tuple fields → (UNAVAILABLE_SENTINEL,)
  Affected: nws_gridpoint_forecast, open_meteo_forecast, noaa_ncei_forecast,
            official_observations_at_cutoff, source_provenance, source_disagreements

FEATURE FLAG (defense in depth)
  KALSHI_WX_SHADOW_AGENT_ENABLED checked first (before any import or I/O),
  both in app.py and independently inside this module.  Default "false".

EXCEPTION SAFETY
  The entire active body (snapshot construction + DB insert) is wrapped in a
  try/except that catches every exception, logs it as SHADOW_CAPTURE_FAILURE,
  and returns None.  A database error during the shadow insert cannot affect
  the production route's response.
"""
from __future__ import annotations

import logging
import os
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
    Flag-gated, synchronous shadow snapshot persistence.

    Constructs a WeatherResearchSnapshot from already-computed route locals,
    then inserts it into kalshi_wx_shadow_snapshot_queue via a single
    synchronous DB write.  Returns immediately after the insert commits.

    Returns None always.  Exception-safe — no exception can reach the caller.
    No threads are spawned.  No Claude calls are made.
    """
    # ── Independent second flag gate (defense in depth) ───────────────────────
    if not _SHADOW_ENABLED:
        return

    # ── Outer exception fence — nothing propagates to the caller ─────────────
    try:
        # Lazy imports — only executed when flag is on.
        from gate_engine.kalshi_wx_shadow_snapshot import WeatherResearchSnapshot
        from gate_engine.kalshi_wx_shadow_db import insert_shadow_snapshot

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

        # ── Persist to Postgres shadow queue — synchronous INSERT, no thread ──
        insert_shadow_snapshot(snapshot)

        _logger.info(
            "SHADOW_CAPTURE_OK city=%s date=%s snapshot_id=%s",
            city, market_date, snapshot_id,
        )

    except Exception as exc:
        # Outer fence: catches snapshot construction failure, DB connection
        # failure, INSERT error, or any import error.  Never propagates.
        _logger.warning(
            "SHADOW_CAPTURE_FAILURE city=%s date=%s error_type=%s error=%s",
            city, market_date, type(exc).__name__, exc,
            exc_info=True,
        )
