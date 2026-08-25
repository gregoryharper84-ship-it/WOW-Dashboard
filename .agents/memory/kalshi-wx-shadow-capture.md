---
name: Kalshi WX shadow capture (Step 10D)
description: How maybe_fire_shadow_snapshot() bridges the weather route to the shadow pipeline, lazy-import patching rules, and sentinel design.
---

## Rule
gate_engine/kalshi_wx_shadow_capture.py is the ONLY place that reads already-computed route locals and feeds them into the shadow orchestrator. app.py contains exactly one flag-gated try/except block for this, inserted immediately after `horizon_hours = _compute_forecast_horizon_hours(...)`.

## Lazy-import patching rule (critical for tests)
`maybe_fire_shadow_snapshot()` imports all heavy dependencies lazily inside its try block. Those names are NEVER attributes of the kalshi_wx_shadow_capture module. Patches must target the SOURCE module:
- `run_shadow_orchestrator`  → `gate_engine.kalshi_wx_shadow_orchestrator`
- `WeatherResearchSnapshot`  → `gate_engine.kalshi_wx_shadow_snapshot`
- `CapabilityBoundary`       → `gate_engine.kalshi_wx_shadow_capability_boundary`
- `get_default_ledger`       → `gate_engine.kalshi_wx_shadow_ledger`
- `_build_shadow_sdk_client` → `gate_engine.kalshi_wx_shadow_capture` (it IS module-level)
- `_SHADOW_ENABLED`          → `gate_engine.kalshi_wx_shadow_capture` (module-level bool)

## UNAVAILABLE sentinel
Fields not exposed at the capture point use `UNAVAILABLE_SENTINEL = "UNAVAILABLE_NOT_EXPOSED_BY_DETERMINISTIC_FETCH"`:
- dict fields → `{"_status": UNAVAILABLE_SENTINEL}`
- tuple fields → `(UNAVAILABLE_SENTINEL,)`
Affected: nws_gridpoint_forecast, open_meteo_forecast, noaa_ncei_forecast, official_observations_at_cutoff, source_provenance, source_disagreements

## tier_detail structure (from _fetch_forecast_high_tiered)
Keys are `"nws"`, `"open_meteo"`, `"noaa_ncei"` (not the tier-name strings like "nws_primary").
Each entry: `{"attempted": bool, "ok": bool, "error": str|None}`.
`source_failures` is derived from tiers where attempted=True and ok=False.

## forecast_high_used_by_deterministic_model when forecast_high is None
Stored as 0.0 (float required by dataclass). `deterministic_weather_readiness_state` = "DATA_UNAVAILABLE" signals the absence.

## Structural app.py test: use idx_start-offset find for end marker
`"Step 3: Live Kalshi prices"` appears earlier in app.py. When extracting the shadow block via src.find(), always pass `idx_start` as the start argument to the second find call: `src.find(end_marker, idx_start)`.

**Why:** First occurrence of "Step 3" is before line 25467; naive find returns idx_end < idx_start → empty block.
