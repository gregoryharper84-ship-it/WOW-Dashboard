# WOW V17 — Opponent-aware scenario shadow bridge

Research status: CLASS_C_OFFLINE_ONLY. No fitted model trained or promoted by this module. It is a *data-contract bridge* for externally fitted team-event scenarios, not a new moneyline specialist. can_execute=false; V17_TERMINAL_REDUCER remains the sole terminal authority.

The existing v17/team_state_challenger_training.py owns the fitted model/feature program. Existing v17/experiments/nov16_ml_slip_learning/learning_shadow.py owns postmortem and holdout grading. This bridge consumes separately generated frozen pregame scenario weights and optional fitted per-state player count probabilities. It cannot authenticate the supplied digest/receipt without independent retrieval.

## Usage

Import evaluate_scenario_outcomes and evaluate_player_usage from shadow_bridge. Supply a frozen_snapshot and an optional fitted_prop_scenarios dictionary.

frozen_snapshot requires sport, official_event_id, event_start_utc, snapshot_at_utc, scenario_fit_cutoff_utc, scenario_model_artifact_sha256, feature_manifest_sha256, scenario_receipt_id, favorite_side, outcome_space, and scenarios. Each scenario requires unique scenario_id, positive normalized weight, outcome (HOME, AWAY, DRAW only in three-way), home_game_state, away_game_state (LEADING/TIED/TRAILING). An in-game trailing state can still produce a full-game win.

fitted_prop_scenarios needs matching sport/event, player_id, market_id, exact_line, prop_fit_artifact_sha256, prop_fit_cutoff_utc, prop_source_manifest_sha256, settlement_basis and one scenario_counts row per scenario ID with count mean, variance, and fitted exact-line P(over). The bridge computes weighted mean, total variance, tail. It never invents a tail from the mean or moneyline.

TWO_WAY_FULL_GAME_OVERTIME corresponds to FULL_GAME_OVERTIME, THREE_WAY_REGULATION to REGULATION_ONLY. Missing or stale evidence returns explicit unavailable status, not guessed probabilities.

## Tests

python -m pytest -q artifacts/wow-engine/v17/experiments/opponent_scenario_bridge/test_shadow_bridge.py

## Acceptance

1. Independently authenticate receipts and model/source hashes, not just their string formats.
2. Fit and test genuine scenario distributions in the existing LLP training lifecycle on a prespecified chronological train/calibration/untouched holdout.
3. Validate player count distributions and compare Brier/log-loss, MAE/CRPS, calibration and time-correct market baselines.
4. Keep runtime integration in a separate independently approved protected change; this research module cannot self-publish or execute.
5. Slips and joint probabilities remain the existing Nov16 research evaluator's responsibility; no expected-value claim without executable offers.

This research bridge does not constitute a completed fitted model or a certified full-slate card.
