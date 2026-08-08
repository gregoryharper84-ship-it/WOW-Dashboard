---
name: Fantasy Score Generative Model Architecture
description: WOW v16 shadow/test fantasy score modeling layer — structure, constraints, and wiring
---

## What was built
`gate_engine/fantasy_score_model/` — a new package alongside `wow_fantasy_score/`.

**Modules:**
- `__init__.py` — exports `run`, `score_fantasy_row`, `SUPPORTED_SPORTS`, `SHADOW_MODE=True`, `can_execute=False`
- `gate.py` — pipeline gate and scoring entry point; lazy imports all generators
- `calibration_families.py` — 7 families (NBA, WNBA, NFL_QB, NFL_RB, NFL_WR_TE, MLB_HITTER, MLB_PITCHER); CLB computation, thin-sample detection
- `shared.py` — Monte Carlo runner, score_line, apply_market_prior, run_stress_suite, determine_label, check_final_refresh, build_output
- `diagnostics.py` — observational counterfactuals only; explicitly not hard gates
- `generators/basketball.py` — NBA/WNBA correlated minutes-shared generator; 11 game states
- `generators/nfl.py` — QB/RB/WR_TE dispatch; 5 game scripts
- `generators/mlb_hitter.py` — PA opportunity distribution → per-PA multinomial event tree
- `generators/mlb_pitcher.py` — 7-regime UNCONDITIONAL mixture; regime weights from failure_path_matrix

**Pipeline wiring:** `pipeline.py` calls `_fantasy_score_model.run(row)` after `tennis_total_games_gate.run(row)` and before `classifier.classify(row)`.

## Hard constraints (never weaken)
- `can_execute=False` unconditional in every module and every output
- No MONEY_QUALIFIED, FINAL_APPROVED, PLAYABLE, LOCK, or execution labels
- YES_MODEL_QUALIFIED requires CLB ≥ 65% AND identity_locked AND settlement_locked AND model NOT provisional
- All Fantasy Score models are currently PROVISIONAL → capped at MODEL_QUALIFIED_HOLD
- Market weight >50% → clamp to 50% + MARKET_DEPENDENT_MODEL label
- Shadow mode: writes to `row["gates"]["fantasy_score_model"]` only; never touches `row["terminal_label"]`

**Why:** These constraints are the governance invariants established in WOW v16 spec; violation would corrupt the execution label integrity the 3-agent workflow depends on.

## Identity locks
- `_lock_scoring_identity()` first tries `wow_fantasy_score/fantasy_score_formulas.json` via FormulaRegistry; falls back to hardcoded provisional version strings
- `_lock_settlement_identity()` checks enrichment["settlement_basis"] against a known set

## NFL reception scoring weight
`RECEPTION_WEIGHT=0.5` is flagged UNCONFIRMED in `generators/nfl.py` — must be validated against the official PrizePicks NFL scoring sheet before any promotion beyond PROVISIONAL.

## Tests
`tests/test_fantasy_score_model.py` — 46 tests covering all 15 spec-mandated regression cases; run in ~1.3s offline.
