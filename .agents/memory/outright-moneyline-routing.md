---
name: OUTRIGHT_WINNER moneyline routing
description: Architecture for first-class OUTRIGHT_WINNER market-family classification and MONEYLINE_V1 input contract in WOW v16 Clean Core.
---

## Rule
OUTRIGHT_WINNER rows are classified by `gate_engine/market_family.py` BEFORE generic prop normalization and never enter `_ge_run_pipeline`.

## Routing chain
OUTRIGHT_WINNER → objective=OUTRIGHT_WIN_PROBABILITY_ONLY → controlling_skill=wow.llp-moneyline-probability-expert → scored by `gate_engine/moneyline_probability.py`

## MONEYLINE_V1 contract
Required: sport, team, opponent, market_type, event_id, slate_date
Prohibited: line, direction, prop_type, stat_key, game_log, player_role

## Compatibility guard (pre-pipeline)
`guard_route_config(rows)` runs before `_ge_run_pipeline`. Mixed OUTRIGHT_WINNER + PLAYER_PROP batch → HTTP 409 `RUN_INVALID_ROUTE_CONFIGURATION` with `primary_blocker=MONEYLINE_ROUTED_TO_PROP_CONTRACT`, `candidate_evaluation_completed=false`.

**Why:** Routing bugs must never resolve to NO_PLAY — that label is reserved for correctly-routed candidates that complete qualification and fail.

## Sport model registry
ACTIVE: MLB, NBA, WNBA, ATP, WTA, TENNIS, MMA, UFC
PROVISIONAL: NFL, NHL, SOCCER, EPL, MLS
Sportsbook odds cannot substitute when model is UNAVAILABLE.

## Soccer 1X2
Three-state (home/draw/away) — binary conversion prohibited. `compute_1x2_three_state()` enforces sum≈1.0; missing `outcome` field → contract violation.

## Event deduplication
Same (sport, participants sorted, slate_date) from N sportsbooks → one canonical row; platform_appearances list preserves all metadata.

## STALE_MODEL_INVALIDATED
Starting pitcher change (MLB) or key player going out invalidates prior snapshot → `STALE_MODEL_INVALIDATED` terminal label + mandatory rerun.

## Route compatibility output fields
Every scored row and the governance handshake carry: route_id, market_family, objective, controlling_skill_id, input_contract_version, required_field_profile, compatibility (PASS/FAIL).

## Key files
- gate_engine/market_family.py
- gate_engine/moneyline_probability.py
- gate_engine/tests/test_moneyline_routing.py (87 tests)
- gate_engine/route_registry.py (MARKET_FAMILY_REQUIRED_GATES, ROUTE_COMPATIBILITY_FIELDS)
- Patch: WOW-PATCH-2026-08-07-OUTRIGHT-MONEYLINE-ROUTING (precedence 102, patch #23)

## Patch count
23 active patches as of 2026-08-07 (governance.py + both acceptance test files updated).
