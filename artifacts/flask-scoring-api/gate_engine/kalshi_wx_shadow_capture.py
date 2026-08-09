"""
gate_engine/kalshi_wx_shadow_capture.py
WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW — Step 10D

Minimal flag-gated capture bridge: reads already-computed deterministic weather
values from the /wow/kalshi/weather/evaluate route and feeds them into the
existing WeatherResearchSnapshot → orchestrator → 5-subagent shadow pipeline.

WHAT THIS MODULE DOES
  maybe_fire_shadow_snapshot() receives local variables that are already
  computed at the insertion point in the route handler, constructs a
  WeatherResearchSnapshot from them (using the real frozen dataclass
  constructor directly, NOT build_test_snapshot), and fires the shadow
  orchestrator.

WHAT THIS MODULE DOES NOT DO
  - Does not call any fetch function or make any new network request.
  - Does not read any value that is not already a local variable at the
    insertion point.
  - Does not alter, substitute, recompute, or feed anything back into the
    deterministic route.
  - Does not interpret evidence, call Claude directly, coordinate subagents,
    produce advisory ceilings, or write shadow analytical results.
  - Does not propagate any exception to its caller under any circumstances.

UNAVAILABLE SENTINEL
  Fields that are not exposed by the deterministic fetch path at the capture
  insertion point are set to UNAVAILABLE_SENTINEL explicitly, not None
  silently, not fabricated, not inferred.
    dict  fields  → {"_status": UNAVAILABLE_SENTINEL}
    tuple fields  → (UNAVAILABLE_SENTINEL,)
  Affected fields:
    nws_gridpoint_forecast, open_meteo_forecast, noaa_ncei_forecast,
    official_observations_at_cutoff, source_provenance, source_disagreements

FEATURE FLAG (defense in depth)
  KALSHI_WX_SHADOW_AGENT_ENABLED (env var) is checked FIRST, before any
  import or construction.  The flag defaults to "false".  Must be "true"
  (case-insensitive) to enable.  app.py also checks this flag before calling
  this function; this module checks it again independently as a second gate.

EXCEPTION SAFETY
  The entire active body of maybe_fire_shadow_snapshot() is wrapped in a
  try/except that catches every exception, logs it as a shadow failure, and
  returns None.  No exception from this function can reach its caller.
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
# Applied to any field whose underlying data is not exposed at the capture
# insertion point.  Explicit and grep-searchable — never None, never fabricated.
UNAVAILABLE_SENTINEL: str = "UNAVAILABLE_NOT_EXPOSED_BY_DETERMINISTIC_FETCH"

# Typed wrappers: use these directly when constructing the snapshot.
_UNAVAIL_DICT: dict = {"_status": UNAVAILABLE_SENTINEL}
_UNAVAIL_TUPLE: tuple = (UNAVAILABLE_SENTINEL,)


# ── Helpers (pure functions — no side effects, no I/O) ────────────────────────

def _derive_source_failures(tier_detail: dict) -> tuple:
    """
    Derive source_failures from the tier_detail dict returned by
    _fetch_forecast_high_tiered().

    tier_detail structure (from production code):
        {
            "nws":        {"attempted": bool, "ok": bool, "error": str|None},
            "open_meteo": {"attempted": bool, "ok": bool, "error": str|None},
            "noaa_ncei":  {"attempted": bool, "ok": bool, "error": str|None,
                           "source_status": str|None},
        }

    Returns a tuple of strings — one entry per tier that was attempted but
    failed.  Empty tuple if all attempted tiers succeeded.
    Does not fabricate detail beyond what tier_detail already contains.
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
    Derive deterministic_weather_readiness_state from forecast_high.

    Transparent classification of existing data — not synthesized evidence:
      forecast_high is not None  →  "READY"
      forecast_high is None      →  "DATA_UNAVAILABLE"
    """
    return "READY" if forecast_high is not None else "DATA_UNAVAILABLE"


def _derive_source_timestamps(
    weather_data_source_tier: str,
    source_cutoff_timestamp: str,
) -> dict:
    """
    Approximate source_timestamps from already-available values.

    The deterministic waterfall does not record per-source fetch times.
    We record the capture-point cutoff timestamp as the timestamp for the
    winning tier.  This is an approximation: the actual fetch happened
    moments before the capture point.
    """
    return {weather_data_source_tier: source_cutoff_timestamp}


def _build_shadow_sdk_client():
    """
    Build an Anthropic SDK client from environment variables.

    Resolution order:
      1. AI_INTEGRATIONS_ANTHROPIC_API_KEY + AI_INTEGRATIONS_ANTHROPIC_BASE_URL
      2. ANTHROPIC_API_KEY

    Raises RuntimeError if the SDK is not installed or no API key is found.
    The RuntimeError is caught by maybe_fire_shadow_snapshot()'s outer
    try/except and logged as a shadow failure.
    """
    try:
        import anthropic as _sdk
    except ImportError as exc:
        raise RuntimeError(
            "SHADOW_CAPTURE: anthropic SDK not installed; "
            "shadow pilot requires the anthropic package"
        ) from exc

    api_key = (
        os.environ.get("AI_INTEGRATIONS_ANTHROPIC_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
    )
    if not api_key:
        raise RuntimeError(
            "SHADOW_CAPTURE: no Anthropic API key found; "
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
    Flag-gated shadow capture: reads already-computed deterministic weather
    values and fires the Kalshi Weather shadow research pipeline.

    Parameters
    ----------
    city                     : City string (e.g. "NYC") — already validated.
    station                  : NWS station code (e.g. "KNYC").
    market_date              : ISO-8601 date string (YYYY-MM-DD).
    forecast_high            : Forecast high (°F) from the deterministic
                               pipeline, or None if all waterfall tiers failed.
    weather_data_source_tier : Winning tier name (e.g. "nws_primary").
    sigma_f                  : Gaussian sigma used by the deterministic model.
    horizon_hours            : Forecast horizon (hours) computed by the route.
    tier_detail              : Per-tier ok/error summary from fc_result.

    Returns
    -------
    None.  Always.  This function is exception-safe: any exception is logged
    as a shadow failure and swallowed.  The production route is never affected.
    """
    # ── Second, independent flag gate (defense in depth) ─────────────────────
    # app.py checks the flag before calling us; we check again so that a direct
    # call to this function with the flag off is also a no-op.
    if not _SHADOW_ENABLED:
        return

    # ── Belt-and-suspenders exception fence ───────────────────────────────────
    # Nothing inside this block can propagate to the caller.
    try:
        # Lazy imports — only reached when the flag is on.  This keeps the
        # module loadable and the flag-off path truly inert without requiring
        # the snapshot or orchestrator modules to be imported at module-load
        # time (which would happen if they were top-level imports here).
        from gate_engine.kalshi_wx_shadow_snapshot import WeatherResearchSnapshot
        from gate_engine.kalshi_wx_shadow_capability_boundary import CapabilityBoundary
        from gate_engine.kalshi_wx_shadow_ledger import get_default_ledger
        from gate_engine.kalshi_wx_shadow_orchestrator import run_shadow_orchestrator

        # ── Derive values from already-computed locals ────────────────────────
        # source_cutoff_timestamp: UTC time at the capture point.
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

        # forecast_high_used_by_deterministic_model is typed float (not
        # Optional[float]) on the dataclass.  When forecast_high is None
        # (all tiers failed), we store 0.0 and rely on
        # deterministic_weather_readiness_state="DATA_UNAVAILABLE" to
        # communicate the absence of real data.
        fh_float = float(forecast_high) if forecast_high is not None else 0.0

        # ── Construct WeatherResearchSnapshot ─────────────────────────────────
        # Uses the real frozen dataclass constructor directly — not the
        # build_test_snapshot() development helper.
        # Fields unavailable at the capture point are set to the explicit
        # UNAVAILABLE sentinel (not None, not fabricated, not inferred).
        snapshot = WeatherResearchSnapshot(
            research_snapshot_id=snapshot_id,
            canonical_event_id=canonical_event_id,
            city=city,
            station=station,
            market_date=market_date,
            source_cutoff_timestamp=source_cutoff,
            # Per-tier raw dicts — not exposed at the capture insertion point.
            # Explicit UNAVAILABLE sentinel on all three optional forecast dicts.
            nws_gridpoint_forecast=_UNAVAIL_DICT,
            open_meteo_forecast=_UNAVAIL_DICT,
            noaa_ncei_forecast=_UNAVAIL_DICT,
            official_observations_at_cutoff=_UNAVAIL_DICT,
            # Deterministic model inputs — real values passed in from the route.
            forecast_high_used_by_deterministic_model=fh_float,
            weather_data_source_tier=weather_data_source_tier,
            forecast_horizon_hours=float(horizon_hours),
            sigma_f=float(sigma_f),
            deterministic_weather_readiness_state=readiness_state,
            # Source metadata — partially derivable from tier_detail; the rest
            # use the explicit UNAVAILABLE sentinel.
            source_timestamps=source_timestamps,
            source_provenance=_UNAVAIL_DICT,   # not exposed at capture point
            source_failures=source_failures,    # derived from tier_detail
            source_disagreements=_UNAVAIL_TUPLE,  # not exposed at capture point
        )

        # ── Build SDK client (raises RuntimeError if unavailable) ─────────────
        sdk_client = _build_shadow_sdk_client()

        # ── Fire the shadow orchestrator ─────────────────────────────────────
        # run_shadow_orchestrator runs all 5 subagents in sequence and records
        # the result to the shadow ledger.  The return value (ShadowValidationResult)
        # is intentionally discarded — it must not reach the production route.
        run_shadow_orchestrator(
            city=city,
            date=market_date,
            run_id=snapshot_id,
            sdk_client=sdk_client,
            capability_boundary=CapabilityBoundary(),
            ledger=get_default_ledger(),
            snapshot=snapshot,
        )

    except Exception as exc:
        # Shadow failure: log with enough context to debug later.
        # Under NO circumstances does this exception propagate.
        _logger.warning(
            "SHADOW_CAPTURE_FAILURE city=%s date=%s error_type=%s error=%s",
            city,
            market_date,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
