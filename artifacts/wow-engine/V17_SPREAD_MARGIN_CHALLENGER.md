# WOW V17 Point-Spread Margin-Distribution Challenger

Status: **CLASS C — SHADOW / CHALLENGER ONLY**  
Program: `WOW_V17_SPREAD_MARGIN_DISTRIBUTION_CHALLENGER_V1`  
Runtime: `V17_ACTIVE`  
Terminal authority: `V17_TERMINAL_REDUCER`  
`can_execute=false`  
`DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`

## Purpose

Add point-spread probability modeling to WOW without weakening V17 probability governance.

The spread lane predicts a **sporting scoring-margin distribution**.  An exact spread is then evaluated as a threshold on that fitted distribution.  The bookmaker line is not a training feature and the model never derives spread probability from moneyline probability, implied odds, external projections, narrative judgment, or generic LLM reasoning.

For signed home spread `s` and home scoring margin `m = home_score - away_score`:

- home covers when `m + s > 0`;
- push when `m + s = 0`;
- home does not cover when `m + s < 0`.

## Why margin distribution instead of ATS classification

A direct `cover / no-cover` classifier is tied to a particular posted line and invites market leakage.  Modeling the underlying margin lets one governed fitted artifact answer many exact lines while keeping the line outside the feature vector.

Example conceptually:

- fitted margin distribution -> `P(home margin > 2.5)` -> home `-2.5` cover probability;
- same distribution -> `P(home margin > 6.5)` -> home `-6.5` cover probability;
- same distribution -> `P(home margin = 3)` -> push mass for home `-3`.

## Initial architecture

### 1. Leakage-safe sporting features

`spread_margin_challenger.py` can build dynamic pregame rows from the existing V17 team-state intelligence feature family.  Historical replay can also consume existing governed pregame feature tables when those tables already represent frozen pregame state.

Every row carries:

- event id;
- event start time;
- `feature_as_of` strictly before the event;
- actual home scoring margin;
- numeric sporting feature vector;
- source-manifest hash.

The feature contract explicitly records:

- `market_features_used=false`;
- `moneyline_probability_used=false`;
- `spread_line_used_as_feature=false`;
- `manual_probability_adjustments=false`.

### 2. Chronological split

The challenger uses deterministic chronological partitions:

- first 60%: fit margin model;
- next 20%: estimate residual distribution;
- final 20%: untouched holdout evaluation.

This prevents future outcomes from entering earlier feature/model state.

### 3. Margin model

Initial challenger family:

`<SPORT>_SPREAD_MARGIN_RIDGE_EMPIRICAL_V1`

The first version uses standardized ridge regression for the expected home scoring margin.  It is deliberately simple and auditable; sophistication must be earned by replay evidence rather than introduced speculatively.

### 4. Residual distribution

The calibration partition supplies out-of-fit residuals:

`residual = observed_margin - fitted_margin`

For a new feature vector:

1. predict the margin center;
2. add each held-out calibration residual;
3. discretize to integer scoring margins;
4. form an empirical scoring-margin distribution;
5. query that distribution at the requested exact spread.

This produces coherent:

- `p_cover`;
- `p_push`;
- `p_not_cover`;
- `p_cover_given_no_push`;
- research-only Wilson lower bound on cover mass.

The probability triplet is regression-tested to normalize to 1.0.

### 5. Evaluation

The untouched chronological test partition is evaluated over sport-specific synthetic line grids.  Synthetic lines are evaluation thresholds only; they are not model features.

Required metrics:

- margin MAE;
- margin RMSE;
- three-way cover/push/loss Brier score;
- non-push cover Brier score;
- non-push cover log loss;
- non-push cover ECE;
- favorite cohort;
- underdog cohort;
- small-spread cohort;
- large-spread cohort;
- integer-line cohort;
- half-line cohort.

Additional certification work must add season/regime, playoffs, overtime-sensitive cases, lineup/injury regimes, and forward-shadow reliability.

## Sport ownership

Infrastructure may be shared.  Fitted artifacts may not be shared across sports.

Initial families:

- NFL -> `NFL_SPREAD_MARGIN_RIDGE_EMPIRICAL_V1`
- NBA -> `NBA_SPREAD_MARGIN_RIDGE_EMPIRICAL_V1`
- NCAAF -> `NCAAF_SPREAD_MARGIN_RIDGE_EMPIRICAL_V1`
- NCAAB -> `NCAAB_SPREAD_MARGIN_RIDGE_EMPIRICAL_V1`
- WNBA -> `WNBA_SPREAD_MARGIN_RIDGE_EMPIRICAL_V1`

Each sport must independently earn replay, calibration, counterexample, holdout, and forward-shadow evidence before any serving registration.

MLB run lines are intentionally excluded from this first shared spread family.  Baseball run distributions require a sport-specific design rather than pretending a football/basketball margin model transfers unchanged.

## Current historical-data readiness

Validation-project inspection on 2026-09-25 found:

| Sport | Settled/training games | Persisted pregame feature rows | Replay state |
|---|---:|---:|---|
| NFL | 1,457 | 1,438 | READY FOR CHALLENGER REPLAY |
| NBA | 8,919 | 8,747 | READY FOR CHALLENGER REPLAY |
| WNBA | 2,211 | 2,122 | READY FOR CHALLENGER REPLAY |
| NCAAF | 3,844 | 0 | `SPREAD_REPLAY_FEATURES_UNAVAILABLE` |
| NCAAB | no dedicated training table found | no dedicated feature table found | `SPREAD_REPLAY_DATASET_UNAVAILABLE` |

Those NCAAF/NCAAB states must remain typed blockers.  They must not become `MODEL_UNAVAILABLE`, inferred probabilities, moneyline conversions, or generic estimates.

## Replay interface

Read-only CLI:

```bash
cd artifacts/wow-engine
PYTHONPATH=. python scripts/run_spread_margin_challenger_replay.py \
  --sport NFL \
  --output /tmp/nfl-spread-replay.json
```

Supported first-pass live-data replay sports: `NFL`, `NBA`, `WNBA`.

The replay adapter:

- reads existing governed training and pregame-feature tables;
- performs no DDL;
- performs no inserts/updates/deletes;
- does not register the challenger as a serving specialist;
- emits a serialized candidate artifact plus research receipt;
- reports `database_mutated=false` and `production_registry_mutated=false`.

## Certification gates before production serving

The experiment **must not** move into the V17 serving registry merely because unit tests pass or historical metrics look promising.

Required sequence:

1. real historical replay per sport;
2. leakage/source-manifest audit;
3. counterexample review;
4. season/regime split review;
5. calibration reliability review;
6. large-favorite / large-underdog tail review;
7. integer-line push calibration review;
8. holdout validation;
9. forward-shadow validation against contemporaneous exact lines;
10. regression against existing team/event lanes;
11. independent review + QA;
12. governed certification decision;
13. only after certification: separate production-registration change.

A production-serving change would require its own review and acceptance work.  This experiment does not contain one.

## Governance invariants

Hard-coded and tested:

- `CAN_EXECUTE = False`
- `AUTOMATIC_CERTIFICATION = False`
- `AUTOMATIC_PROMOTION = False`
- `PROBABILITY_PUBLISHABLE = False`
- `MARKET_PROBABILITY_SUBSTITUTION_ALLOWED = False`
- `MONEYLINE_TO_SPREAD_CONVERSION_ALLOWED = False`
- `MANUAL_PROBABILITY_ADJUSTMENTS_ALLOWED = False`
- `GLOBAL_TERMINAL_REDUCER = "V17_TERMINAL_REDUCER"`

The model cannot place, route, approve, modify, cancel, or execute a wager or market order.

## Experiment terminal state

Until sport-specific historical and forward evidence earns a certification decision, the correct terminal state is:

`EXPERIMENT_CREATED`

not `FIXED_AND_VERIFIED`, not production-ready, and not certified.
