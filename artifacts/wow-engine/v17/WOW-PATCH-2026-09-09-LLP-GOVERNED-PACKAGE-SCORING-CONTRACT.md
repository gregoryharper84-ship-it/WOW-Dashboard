# WOW-PATCH-2026-09-09-LLP-GOVERNED-PACKAGE-SCORING-CONTRACT

## Status

```text
status=ACTIVE_PROJECT_CONTRACT_PENDING_REGRESSION
runtime_generation=V17_ACTIVE
route=LLP_TEAM_BETTING_ENGINE
terminal_authority=V17_TERMINAL_REDUCER
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
```

## Purpose

Require a complete immutable governed LLP probability package before official Brier/log-loss grading, probability ranking, or edge ranking. This patch removes legacy scoring aliases that could allow sportsbook/market probability, generic probability fields, mutable timestamps, or incomplete calibration records to masquerade as a governed model forecast.

This patch refines and supersedes only the scoring-package field aliases in `WOW-PATCH-2026-09-08-LLP-POSTMORTEM-LEARNING-AND-RECALIBRATION`. The learning, preserve/refine/regression-check, favorite/upset separation, realized failure-path, and non-mutating postmortem rules remain active.

## Required Governed Package

Before scoring or ranking, require:

```text
identifier:
  prediction_id OR candidate_id

probability:
  calibrated_probability
  calibrated_lower_bound
  calibrated_upper_bound

provenance:
  immutable_model_timestamp
  model_version OR model_artifact_version
  calibration_method
  calibration_version
  source_snapshot_id
  source_snapshot_timestamp
  outcome_space
```

Validation:

```text
0 <= calibrated_lower_bound
   <= calibrated_probability
   <= calibrated_upper_bound
   <= 1
```

The canonical scoring contract does not accept these fields as substitutes:

```text
probability
market_probability
calibrated_point
lower_bound
upper_bound
model_timestamp
created_at
```

A storage or specialist adapter may explicitly translate a certified upstream field into the canonical package before this gate, but the scorer itself must never search fallback aliases.

## Timestamp Immutability and Freshness

```text
immutable_model_timestamp cannot be overwritten by:
  odds refresh
  market audit
  final refresh
  settlement
  postmortem

immutable_model_timestamp < latest_material_update_at
=> STALE_MODEL_OUTPUT
=> rank_eligible=false
=> scoring_allowed=false
```

A malformed/non-timezone-aware model or source-snapshot timestamp is `MODEL_OUTPUT_INVALID`.

## Failure Taxonomy

```text
missing/malformed governed package
=> MODEL_OUTPUT_INVALID
=> rank_eligible=false
=> scoring_allowed=false

sport model exists but required inputs are missing/stale
=> MODEL_INPUTS_INSUFFICIENT
=> rank_eligible=false

controlling model selected/invoked but scorer fails, times out, or holds
=> MODEL_SCORER_FAILED
=> rank_eligible=false

selected scorer returns a malformed probability package
=> MODEL_OUTPUT_INVALID
=> rank_eligible=false
=> scoring_allowed=false

required controlling model capability genuinely absent before selection
=> MODEL_UNAVAILABLE
=> rank_eligible=false
```

Never rewrite `MODEL_SCORER_FAILED` or `MODEL_OUTPUT_INVALID` as `MODEL_UNAVAILABLE`.

## Scoring and Ranking Contract

```text
Brier probability input:
  calibrated_probability only

Log-loss probability input:
  calibrated_probability only

Probability leaderboard:
  calibrated_lower_bound DESC only

Edge leaderboard score:
  calibrated_lower_bound - no_vig_probability - friction_buffer
```

Sportsbook implied probability, closing no-vig probability, external projection probability, recent hit rate, and narrative confidence are evidence or comparison inputs only. They are never governed model probability.

## Objective Separation

A complete sporting probability may remain preserved when downstream market/value evidence is missing. Missing exact price/no-vig evidence blocks only the dependent market/edge/money publication and must not erase a completed governed sporting probability package.

## Acceptance Tests

1. `probability=0.99` cannot score when `calibrated_probability` is missing.
2. `market_probability` never changes Brier/log-loss when governed calibrated probability is present.
3. Legacy `lower_bound` cannot satisfy the probability leaderboard gate.
4. `created_at` or mutable `model_timestamp` cannot satisfy `immutable_model_timestamp`.
5. Missing prediction/candidate identifier is `MODEL_OUTPUT_INVALID`.
6. Missing lower or upper bound is `MODEL_OUTPUT_INVALID`.
7. Bounds outside or out of order are `MODEL_OUTPUT_INVALID`.
8. A stale immutable model timestamp is `STALE_MODEL_OUTPUT` and cannot score or rank.
9. Probability ranking uses calibrated lower bound, even when point-probability ordering differs.
10. Edge ranking uses lower bound minus no-vig minus friction.
11. Scorer timeout after model invocation is `MODEL_SCORER_FAILED`, not `MODEL_UNAVAILABLE`.
12. Malformed scorer package is `MODEL_OUTPUT_INVALID`, not `MODEL_UNAVAILABLE`.
13. Capability absent before selection is `MODEL_UNAVAILABLE`.
14. Immutable model timestamp overwrite attempt fails closed.
15. `can_execute=false` remains invariant.

## Deployment State Language

Repository merge, backend deployment, model capability, and live Custom GPT editor synchronization are separate states. This patch is not production-live until the repository regression passes, the approved change reaches the deployed backend, and any required live editor synchronization is separately verified.
