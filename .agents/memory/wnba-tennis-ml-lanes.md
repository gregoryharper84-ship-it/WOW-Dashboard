---
name: WNBA and Tennis Moneyline Specialist Lanes
description: Root cause and fix for WNBA/tennis OUTRIGHT_WINNER rows hitting INDEPENDENT_PROBABILITY_UNAVAILABLE — sport-specific acquisition dispatch + specialist submodels.
---

# WNBA and Tennis Moneyline Specialist Lanes

**Patch:** WOW-PATCH-2026-08-17-WNBA-TENNIS-ML-LANES v1.0 — Patch #27, precedence 106.

## Root Cause
`_MONEYLINE_TEAM_SUPPORTED = frozenset({"NBA", "MLB"})` — WNBA and tennis were explicitly excluded. Every WNBA/tennis OUTRIGHT_WINNER row reached `sport_model.compute_independent_probability` with all submodels empty → `INDEPENDENT_PROBABILITY_UNAVAILABLE:insufficient_non_market_data` (vague, non-actionable).

## Fix Architecture

### Sport-specific dispatch (NOT a shared frozenset extension)
```
_MONEYLINE_TEAM_SUPPORTED = {NBA, MLB}   → _check_nba_mlb_moneyline
_WNBA_ML_SUPPORTED        = {WNBA}       → _check_wnba_ml_acquisition
_TENNIS_ML_SUPPORTED      = {ATP,WTA,TENNIS} → _check_tennis_match_acquisition
```
Never collapse families into a shared frozenset — that is how the original omission happened.

### Files changed
- `gate_engine/moneyline/team_acquisition.py` — `acquire_team_data()` dispatches sport-specifically; added `_acquire_wnba_ml()` (BDL WNBA standings + row-derived) and `_acquire_tennis_match()` (row-derived + ESPN best-effort).
- `gate_engine/acquisition_orchestrator.py` — `_check_moneyline_acquisition()` is now a dispatcher; three sport-specific check functions.
- `gate_engine/moneyline/sport_model.py` — `_wnba_ml_specialist()` (Bradley-Terry on win_pct + efficiency) and `_tennis_match_winner_specialist()` (surface_adjusted_form → Elo → hold/break → H2H) added; each fires only for its sport.
- `gate_engine/moneyline/pipeline.py` — vague blocker string replaced with typed hydration failure object: `{hydration_profile, missing_fields[], specialist_status, eligible_for_model, retryable}`.

### Hard constraint: WNBA player-prop isolation
`_acquire_wnba_ml()` must NEVER read or write `game_log` / `box_score_log`. Those keys are scoped to the WNBA_Enrichment_Key_Contract (player-prop rows only). WNBA moneyline hydration uses team/game-state fields (win_pct, efficiency ratings, pace, rest_days).

### Typed failure schema (pipeline.py)
```python
{
  "hydration_profile":  "WNBA_ML_V1" | "TENNIS_MATCH_WINNER_V1" | ...,
  "missing_fields":     ["field_a", "field_b"],   # derived from sport_model notes
  "specialist_status":  "NOT_READY",
  "eligible_for_model": False,
  "retryable":          True,
}
```
`hydration_profile` sourced from `enrichment["hydration_profile"]` (stamped at acquisition) or inferred from sport via `_profile_map`.

### Partial acquisition guard
Partial data (some fields present, some missing) must NEVER silently fall back to `market_no_vig_probability` as the independent model output. `independent_probability` stays `None`; pipeline routes to MARKET_OBSERVATION_ONLY (if market odds present) or DATA_CONTRACT_FAIL (if not).

## Why
The verifier correctly identified that the Gatekeeper was doing the right thing by refusing to substitute sportsbook odds for a missing model. The fix adds the missing acquisition + specialist paths, not loosens the gate.

**How to apply:** Any new sport added to moneyline scoring needs its OWN frozenset + check function + specialist submodel + hydration profile. Do not extend existing frozensets.
