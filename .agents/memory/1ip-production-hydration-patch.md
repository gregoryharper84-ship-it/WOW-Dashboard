---
name: 1IP Production Hydration Patch
description: Fix for MLB 1IP_PITCHES_THROWN rows never reaching the event-tree simulator; BF key alias, acquisition routing, TEST_ONLY promotion.
---

## Rule
Any future change to 1IP_PITCHES_THROWN scoring must preserve all four fix layers.

## What was broken (root cause chain)
1. `_check_prop_game_log` dispatched all MLB props to generic MLB Stats API game_log — never called `savant_1ip_ledger.build_1ip_ledger`.
2. `first_inning_bf_distribution` was therefore always absent from enrichment.
3. Pipeline gate rejected with `DATA_CONTRACT_FAIL:missing_field:first_inning_bf_distribution`.
4. `hit_probability.py` had a `TEST_ONLY` short-circuit that returned `hit_probability=None` regardless.
5. Key mismatch: `_bf_distribution()` returned `p_bf_5plus`; `simulate_1ip()` consumed `p_bf_gte5` — silent zero inputs.

## Four-layer fix (commit af96567)
- **savant_1ip_ledger.py**: `_bf_distribution` emits both `p_bf_5plus` and `p_bf_gte5` (alias). New `compute_pitches_per_batter_dist(ledger_rows)` derives ppb mean/std (3+ starts needed; else genre defaults mean=4.2 std=1.1).
- **acquisition_orchestrator.py**: `_1IP_STAT_KEYS = frozenset({"1IP_PITCHES_THROWN", "1IP"})`. Early branch in `_check_prop_game_log` routes MLB 1IP rows to `_check_1ip_acquisition()`. That function: resolves MLBAM ID, calls `build_1ip_ledger`, writes `first_inning_bf_distribution` + `pitches_per_batter_distribution` + `savant_1ip_ledger` to `enrichment[row_id]`. Typed `PROBABILITY_PIPELINE_CONTRACT_BREACH` breach dict on failure.
- **hit_probability.py**: 1IP firewall promoted from TEST_ONLY to production generator. Calls `simulate_1ip()` when bf_dist present. Gate: `n=0` explicit → breach; `n` key absent (GPT-supplied probs) → allowed. Degenerate sim output (outside 0.01–0.99) fails closed to None.
- **pipeline.py**: Gate detail message updated to say backend acquisition is attempted.

## Key invariants
- `can_execute=False` unconditional; ceiling stays `MODEL_QUALIFIED_HOLD`.
- `p_bf_gte5` alias must always be present alongside `p_bf_5plus` in bf_dist output.
- GPT-supplied `first_inning_bf_distribution` (no `n` key) is accepted without going through acquisition.
- Market readiness and model readiness stay separate; `no_vig_prob` is stored as `market_calibration` not blended into the simulation result.
- `labels.py` untouched (hard-protected); model_used string `"1ip_monte_carlo_event_tree_v1"` is a module-level constant in `hit_probability.py`.

**Why:** The Savant first-inning CSV is the only source for per-inning BF and pitch counts; the generic MLB Stats API game log contains season-level IP, not inning-1 details.

**How to apply:** Any new 1IP stat key must be added to `_1IP_STAT_KEYS` frozenset and tested with `test_1ip_production_hydration.py` T5 fixtures.
