# WOW-PATCH-2026-09-08-LLP-POSTMORTEM-LEARNING-AND-RECALIBRATION

## Status

```text
status=ACTIVE_PROJECT_CONTRACT_PENDING_REGRESSION
runtime_generation=V17_ACTIVE
route=TEAM_EVENT_PROBABILITY_CAPABILITY
terminal_authority=V17_TERMINAL_REDUCER
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
```

## Purpose

Make the LLP team/event probability lane learn systematically from immutable pregame forecasts and official settlements without confusing card-construction losses, market prices, or realized variance with sporting-model probability.

This is a model-learning patch. It is intentionally separate from the card-sharpness/session-exposure patch.

```text
LLP_MODEL_LEARNING
= was the team/event probability calibrated and ranked well?

CARD/PORTFOLIO_LEARNING
= were otherwise valid probabilities combined intelligently?
```

## Existing LLP Contract Preserved

The current LLP probability-lane contract already requires one Stage-20 prediction/calibration row per discovery candidate with, among other fields:

```text
independent_probability
market_prior_weight
unconditional_probability
calibrated_probability
lower_bound
upper_bound
failure_tags
model_timestamp
closing_no_vig
result
brier_score
log_loss
clv
postmortem_note
```

The current moneyline probability expert also requires:

```text
market_prior_weight
independent_model_weight
```

and classifies market-prior weight above 0.50 as `MARKET_DEPENDENT_MODEL`.

Favorites and upsets already have distinct lower-bound leaderboard tiers and must be ranked by calibrated lower bound. Failure paths and final refresh are already mandatory sporting-probability stages.

This patch does not replace those requirements. It turns the immutable prediction/settlement history into repeatable out-of-sample diagnostics.

## Non-Negotiable Governance

```text
market_probability != governed_model_probability
close_loss != model_error_by_definition
single_loss != recalibration_trigger
card_loss != LLP_probability_loss
postmortem_metadata != rewritten_prediction
historical_rank != retroactively_reordered_rank
automatic_parameter_mutation=false
can_execute=false
```

Never change a historical row's:

```text
model_probability
independent_probability
unconditional_probability
calibrated_probability
calibrated_lower_bound
lower_bound
upper_bound
terminal_label
result
```

based on settlement.

Recalibration is forward-looking and requires sufficient out-of-sample evidence plus the normal V17 certification path.

## PATCH-1 — Immutable Official Scoring

Only the final immutable pregame snapshot receives official Brier/log-loss grading.

Diagnostic pre-refresh snapshots may be retained to study refresh effectiveness but may never replace the final immutable row after the outcome is known.

For binary outright-win outcomes:

```text
Brier = (p - y)^2
LogLoss = -(y*ln(p) + (1-y)*ln(1-p))
```

Numerical clipping used to keep log loss finite is computation-only and must never mutate the stored forecast.

Push/void/refund rows are not coerced into wins or losses.

## PATCH-2 — Favorite and Upset Calibration Stay Separate

Default calibration groups are:

```text
sport + market_role
```

where favorite/winner and underdog/upset roles are distinct.

Do not pool the favorite and upset lanes into one reliability curve. Their base rates, rank tiers, and failure regimes are different under the existing LLP contract.

Per group, calculate:

```text
sample_size
observed_win_rate
mean_calibrated_probability
calibration_bias
ECE
mean_brier_score
mean_log_loss
mean_lower_bound
lower_bound_empirical_gap
mean_interval_width
reliability_buckets
```

The reliability-bin width is analytical configuration. It is not a sporting-probability threshold.

## PATCH-3 — Lower-Bound Reliability Diagnostics

Because LLP leaderboards rank by calibrated lower bound, postmortem analysis must test whether lower-bound ordering and realized outcomes remain empirically credible.

Diagnostic:

```text
lower_bound_empirical_gap = observed_win_rate - mean_lower_bound
```

Interpretation:

```text
positive => realized win rate exceeded the average published lower bound
negative => average lower bound exceeded realized win rate in the evaluated sample
```

This is an empirical reliability diagnostic, not a claim of formal confidence-interval coverage unless the controlling calibrator separately certifies that interpretation.

## PATCH-4 — Minimum-Sample Recalibration Gate

No isolated miss, upset, close finish, or bad slate may directly change LLP parameters.

The learning engine exposes a runtime-configurable minimum sample size and optional review thresholds. Default behavior without review thresholds is diagnostic only.

Possible states:

```text
INSUFFICIENT_SAMPLE
DIAGNOSTIC_ONLY_NO_RECALIBRATION_THRESHOLDS
PRESERVE_CURRENT_CALIBRATION
RECALIBRATION_REVIEW_RECOMMENDED
```

`RECALIBRATION_REVIEW_RECOMMENDED` is not permission to mutate production calibration. Any numeric change still requires fitted-model/calibration work, leakage-safe validation, regression, and normal V17 certification.

## PATCH-5 — Failure-Path Realization Learning

The LLP pregame package already requires quantified favorite loss paths and upset/favorite-failure paths.

After settlement, append realized failure-path tags when objectively attributable.

Measure:

```text
predicted_failure_tag_counts
realized_failure_tag_counts
realized_path_match_rate
unanticipated_failure_prediction_ids
```

Do not invent a failure-path label when the game does not support one. `UNKNOWN` is preferable to narrative hindsight.

Repeated unanticipated paths are candidates for feature/model review only after sufficient evidence accumulates.

## PATCH-6 — Market-Prior Dependency Audit

Preserve the existing LLP safeguard:

```text
market_prior_weight > 0.50
=> MARKET_DEPENDENT_MODEL
```

Postmortem analysis groups performance by market-prior-weight bands and compares observed accuracy/Brier performance.

This audit asks whether LLP's independent signal is contributing useful information. It never substitutes closing no-vig probability for the model forecast and never changes the historical sporting probability.

## PATCH-7 — Lower-Bound Ranking Regret

For each immutable pregame slate/lane:

```text
rank = calibrated_lower_bound DESC
```

Record whether the top-ranked candidate lost while a lower-ranked candidate won.

This is a ranking-inversion diagnostic. It must not retroactively reorder historical leaderboards or count the lower-ranked winner as the model's original top selection.

Over many slates, repeated inversions can identify ranking/calibration weaknesses even when aggregate hit rate appears acceptable.

## PATCH-8 — Final-Refresh Effectiveness

When both a diagnostic pre-refresh snapshot and final immutable pregame snapshot exist, compare the absolute forecast error before and after refresh.

Report:

```text
IMPROVED
WORSENED
UNCHANGED
```

Only the final immutable pregame forecast receives official Brier/log-loss grading.

This enables sport-by-sport measurement of whether lineup, pitcher, goalie, QB, injury, weather, or other required final-refresh inputs improve forecasts in practice.

## PATCH-9 — Uncertainty-Width Diagnostics

Track interval width alongside calibration error.

The purpose is to detect patterns such as:

```text
narrow intervals + repeated miss => uncertainty may be understated
wide intervals + stable calibration => possible future tightening candidate
```

No universal interval-width cutoff is added. Any adjustment must be learned and certified by the controlling calibration layer.

## Preserve -> Refine -> Regression-Check

This patch must preserve what already works.

A good-performing sport/lane sample should produce:

```text
PRESERVE_CURRENT_CALIBRATION
```

when configured review thresholds are satisfied.

A bad isolated result must produce:

```text
INSUFFICIENT_SAMPLE
```

when the sample gate is not met.

A sufficiently large, systematically biased sample may produce:

```text
RECALIBRATION_REVIEW_RECOMMENDED
```

but never automatic parameter mutation.

## Workflow Placement

```text
pregame LLP full model
-> final refresh
-> calibrated probability + lower bound
-> V17 terminal reducer
-> immutable Stage-20 prediction write
-> event settlement
-> official result append
-> Brier/log-loss/CLV append
-> realized failure-path append when supported
-> favorite/upset sport-specific calibration diagnostics
-> lower-bound reliability diagnostics
-> market-prior dependency diagnostics
-> ranking-regret diagnostics
-> refresh-effectiveness diagnostics
-> minimum-sample gate
-> preserve current calibration OR recommend offline recalibration review
-> leakage-safe fit/backtest/certification before any future production change
```

## Regression Requirements

1. Favorite and upset rows are never pooled into one calibration group.
2. Brier and log loss use only the immutable pregame forecast.
3. Pregame probabilities and lower bounds remain unchanged after learning analysis.
4. A one-game miss cannot trigger numeric recalibration when minimum sample is not met.
5. A sufficiently large biased synthetic sample can recommend review without mutating parameters.
6. A calibrated synthetic sample can explicitly preserve current calibration.
7. `market_prior_weight > 0.50` remains flagged as market-dependent without a probability haircut.
8. Predicted vs realized failure paths are measured without hindsight relabeling.
9. Lower-bound rank inversions are logged without rewriting historical rank.
10. Pre-refresh snapshots are diagnostic only; final immutable snapshot is the official grade.
11. Push/void rows are not coerced into binary outcomes.
12. `V17_TERMINAL_REDUCER` remains terminal authority.
13. `can_execute=false` remains invariant.

## Files

```text
v17/llp_postmortem_recalibration.py
v17/llp-postmortem-learning-schema.json
v17/test_llp_postmortem_recalibration.py
flask-scoring-api/tests/test_v17_llp_postmortem_recalibration.py
```

## Definition of Done

```text
focused_llp_learning_regression=PASS
required_three_regression=PASS
governed_probability_backend=PASS
additional_required_regression=PASS
probability_immutability=PASS
no_automatic_parameter_mutation=PASS
can_execute=false
```

Until those checks pass and any future fitted recalibration is separately certified, this patch is a learning/diagnostic improvement rather than a claim that production LLP probabilities have already been recalibrated.
