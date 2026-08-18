---
name: 1IP production route fix
description: Root causes and fix for every 1IP_PITCHES_THROWN row terminating DATA_CONTRACT_FAIL before the event-tree model could fire.
---

## Root causes (two independent bugs)

### Bug A — prob_ledger + failure_path blocked the event-tree lane

**Rule:** `MODEL_REQUIRED_COMPONENTS = {"l10_distribution", "role_usage"}` in `prob_ledger.py` are pitcher K/Outs adapter constructs, not event-tree constructs.

**Why it broke:** `mlb.pitcher_prob_ledger_adapter.canonical_stat_key("1IP_PITCHES_THROWN")` returns `None` — the adapter's `_CANONICAL_STAT_KEYS` dict only covers K/SO/OUTS. So the adapter never ran → ledger components absent → `model_probability_complete=False`.

Simultaneously, `failure_path_inputs` (the 3-scenario primary/secondary/black_swan structure) are undefined for the single-path Monte Carlo event-tree lane. `failure_path.run()` fired `DATA_CONTRACT_FAIL` at `pipeline.py:757`, **before** the 1IP field gate at line 783 was even reached.

**Fix:** In `pipeline.py`, a bypass block before `prob_ledger.run()` and `failure_path.run()` detects `1IP_PITCHES_THROWN` and:
- Stamps `model_probability_complete = bool(enr["first_inning_bf_distribution"])`
- Writes a `prob_ledger` gate dict with code `1IP_EVENT_TREE_BYPASS` / `1IP_BF_DIST_MISSING`
- Skips `failure_path.run()` entirely
- The 1IP field gate at line 783+ remains the sole data-contract enforcer

**How to apply:** Any new stat type that uses a dedicated event-tree model (not the K/Outs pitcher adapter) will hit this same issue if added to the pipeline without a corresponding bypass or adapter.

### Bug B — non-dict `bf_dist` caused `AttributeError` in `simulate_1ip`

**Why it broke:** `hit_probability.py:773` set `_bf_n_explicit=None` for non-dict `bf_dist` (via `isinstance(bf_dist, dict)` guard), but the breach condition was only `bf_dist is None or n==0`. A truthy non-dict fell through to `simulate_1ip(bf_distribution=non_dict, ...)` → `.get("p_bf_3")` → `AttributeError`.

**Fix:** Added `not isinstance(bf_dist, dict)` to the breach condition in `hit_probability.py`. Any non-dict `bf_dist` is now treated as absent → typed `PROBABILITY_PIPELINE_CONTRACT_BREACH`.

## Test file
`artifacts/flask-scoring-api/tests/test_1ip_route_fix.py` — 14 tests (T1–T10b).

## Key invariants preserved
- `failure_path.py` is a protected file — bypass is in `pipeline.py` only
- `can_execute=False` unconditional; `terminal ceiling = MODEL_QUALIFIED_HOLD` unchanged
- All 7,589 pre-existing tests pass

## Commit
`35bcfa3`
