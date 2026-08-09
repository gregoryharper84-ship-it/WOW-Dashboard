---
name: Kalshi WX WeatherResearchSnapshot contract
description: Step 10C frozen dataclass for shadow pilot — fields, constraints, test patterns, and the one frozen-dataclass testing gotcha.
---

## Rule
`WeatherResearchSnapshot` is a `frozen=True` dataclass in `gate_engine/kalshi_wx_shadow_snapshot.py`. One instance is created per orchestrator run and the **same object** is passed unchanged to all five subagents (`id()` equality enforced by SC4).

## Fields (19, all evidence-only)
Identity: `research_snapshot_id`, `canonical_event_id`
Coordinates: `city`, `station`, `market_date`, `source_cutoff_timestamp`
Forecast evidence: `nws_gridpoint_forecast`, `open_meteo_forecast`, `noaa_ncei_forecast`, `official_observations_at_cutoff` (all Optional[dict])
Model inputs: `forecast_high_used_by_deterministic_model`, `weather_data_source_tier`, `forecast_horizon_hours`, `sigma_f`, `deterministic_weather_readiness_state`
Source metadata: `source_timestamps`, `source_provenance` (dict), `source_failures`, `source_disagreements` (tuple)

## Governance boundary
No field name appears in `FORBIDDEN_GOVERNANCE_KEYS`. SC2 in test_kalshi_wx_shadow_snapshot.py enforces this statically.

## Only constructor
`build_test_snapshot(**kwargs)` — keyword-only, all values explicit or defaulted. NO network calls. Production fetch wiring is deferred to a separate future step (10D).

## How subagents receive it
- Each `run_*_subagent()` has `snapshot: Optional[WeatherResearchSnapshot] = None`.
- When provided: `_format_snapshot_evidence(snap)` renders a structured block including `research_snapshot_id`, `station`, `sigma_f`, readiness state, etc. into the user message.
- When None: falls back to the old city/date/run_id-only format (backward-compatible; all 179 pre-10C tests still pass).
- Orchestrator: `run_shadow_orchestrator()` accepts `snapshot=None`; passes the same instance to all five via the `_run_and_check` closure.

## Critical testing gotcha
**`object.__setattr__(frozen_instance, field, value)` silently SUCCEEDS** — it bypasses the Python-level `__setattr__` hook that frozen dataclasses install.
**Always use `setattr(snap, field, value)` in tests** — this routes through `type(snap).__setattr__` and correctly raises `FrozenInstanceError`.

**Why:** Python's `object.__setattr__` operates at the C slot level, before the Python descriptor/`__setattr__` machinery. Frozen dataclasses override `__setattr__` at the Python level only.

## Test file
`tests/test_kalshi_wx_shadow_snapshot.py` — 16 tests (SC1–SC4), 195 total shadow tests pass.
