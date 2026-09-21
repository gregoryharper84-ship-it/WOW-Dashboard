# WOW V17 — LLP V17.1 Sharpness Challenger

Date: 2026-09-21
Status: SHADOW / CHALLENGER ONLY
Class: Class C model/ranking hypothesis package
Runtime: V17_ACTIVE
Terminal authority: V17_TERMINAL_REDUCER
can_execute: false

## Purpose

Test whether LLP team/event winner ranking can become sharper and less mechanically conservative without weakening structural integrity, replacing governed fitted probabilities with market odds, or changing production behavior before evidence is sufficient.

The challenger addresses six hypotheses:

1. Pure `calibrated_lower_bound DESC` ranking may underperform `calibrated_probability DESC` for the specific objective “most likely winner.”
2. Some uncertainty states should remain soft uncertainty when a valid fitted probability package exists rather than behaving like structural invalidity.
3. The same uncertainty source may be consumed by the core model, failure-path distribution, calibration, and lower-bound layer; this requires explicit provenance to detect redundant penalty risk.
4. Market no-vig probability may be useful as a contradiction/staleness diagnostic without becoming the governed sporting forecast.
5. Event-level counterfactual sensitivity should expose which factors materially move P(win).
6. Calibration review should be cohort-aware with shrinkage so small samples do not create brittle local corrections.

## Non-negotiable invariants

This patch does NOT change production scoring or publication.

```text
SERVING_MODE = SHADOW_ONLY
AUTOMATIC_PROMOTION_ALLOWED = false
PRODUCTION_RANKING_MUTATION_ALLOWED = false
PRODUCTION_CALIBRATION_MUTATION_ALLOWED = false
PRODUCTION_MARKET_PRIOR_MUTATION_ALLOWED = false
MARKET_PRIOR_WEIGHT = 0.0
V17_TERMINAL_REDUCER = sole_global_terminal_authority
can_execute = false
```

The current production ranking contract remains authoritative until governed validation explicitly promotes a replacement.

## Challenger ranking views

For every valid candidate with calibrated probability `p` and calibrated lower bound `lb`, compute three parallel research scores:

```text
winner_likelihood_score = p
confidence_floor_score = lb
uncertainty_adjusted_score = p - lambda * (p - lb)
```

Properties:

```text
lambda = 0 -> calibrated-probability ranking
lambda = 1 -> current lower-bound ranking
0 < lambda < 1 -> challenger compromise
```

`lambda` is never selected by intuition for production. It must be evaluated out of sample across historical/forward slates.

## Hard vs soft uncertainty

Hard structural blockers remain fail-closed, including wrong/stale event identity, started/final events, unsupported/unavailable model route, scorer failure, invalid model output, invalid probability package, unresolved settlement identity, and stale probability after a material update.

Soft uncertainty is research-visible and may remain shadow-rank-eligible when a valid fitted probability package exists. Examples include bounded lineup/starter/goalie/QB uncertainty, weather uncertainty, workload uncertainty, thin effective sample, bounded source conflict, calibration-sample thinness, and model disagreement.

This classification is challenger-only. It does not override the production terminal reducer.

## Uncertainty provenance audit

For each material uncertainty source record whether it was consumed by:

```text
core_model
failure_path
calibration
bound
```

Multiple consumption is not automatically an error. It creates `redundancy_review_required=true` because each extra use should be justified as modeling a distinct residual uncertainty rather than repeatedly penalizing the same fact.

No probability is modified by this audit.

## Market divergence diagnostic

Market evidence remains separate from sporting probability.

```text
divergence = calibrated_probability - market_no_vig_probability
```

When absolute divergence exceeds a configured research threshold, emit:

```text
RECHECK_INPUTS
```

This triggers hydration/status/news review only. It does not blend market probability into the governed forecast and does not change `MARKET_PRIOR_WEIGHT=0.0`.

A future bounded-market-prior challenger may be proposed only if historical and forward replay demonstrate an improvement in Brier score, log loss, calibration, and robustness without market leakage.

## Event sensitivity attribution

For each fitted forecast, research may score certified counterfactual scenarios using the same model family, for example:

```text
baseline P(win)
replacement-level starter scenario
rested bullpen scenario
neutral weather scenario
confirmed full lineup scenario
```

Report delta in probability percentage points and rank by absolute sensitivity.

Sensitivity output is diagnostic only and must not be used to hand-edit the governed probability.

## Hierarchical cohort calibration diagnostic

For supported calibration cohorts, compute a shrunk observed-rate target:

```text
local_weight = n / (n + prior_strength)
shrunk_target = local_weight * cohort_observed_rate
               + (1-local_weight) * global_observed_rate
```

Small cohorts therefore borrow heavily from global evidence; large cohorts increasingly express local behavior.

This is a challenger diagnostic, not a production calibration replacement.

## Required replay comparison

Compare at minimum:

```text
A = CALIBRATED_PROBABILITY
B = CALIBRATED_LOWER_BOUND
C(lambda) = UNCERTAINTY_ADJUSTED
```

Across chronological out-of-sample and pristine forward slates report:

```text
top1_win_rate
selected_top3_hit_rate
selected_top5_hit_rate
selected_Brier
selected_log_loss
calibration_bias
ECE
lower_bound_reliability
number_of_rank_order_changes
number_of_previous_correct_top1s_flipped
number_of_previous_incorrect_top1s_corrected
```

Where available also segment by:

```text
sport
league
favorite/underdog role
probability band
uncertainty-width band
lineup/status certainty
failure-path tier
forecast-to-start-time bucket
```

## Promotion bar

No production change is earned merely because challenger top-1 hit rate is higher on one slate.

Promotion review requires:

```text
chronological historical replay complete
counterexample review complete
untouched holdout complete
forward shadow sample sufficient
Brier non-inferior or improved
log loss non-inferior or improved
calibration non-inferior or improved
ranking objective materially improved
no hard-block weakening
no typed-failure weakening
no market leakage
no terminal-authority change
can_execute=false preserved
```

If results are mixed, remain shadow-only and preserve production behavior.

## Acceptance tests

1. A 68% / 53% LB candidate ranks above a 59% / 55% LB candidate by calibrated probability but below it by lower bound.
2. `lambda=0` reproduces calibrated probability and `lambda=1` reproduces lower bound.
3. Soft uncertainty does not become a hard structural rejection in the challenger when the probability package remains valid.
4. Hard structural blockers remain excluded.
5. Market divergence can trigger `RECHECK_INPUTS` without changing the sporting probability or market prior weight.
6. Duplicate uncertainty consumption is surfaced without mutating probability.
7. Sensitivity diagnostics preserve the baseline probability and expose signed deltas.
8. Small calibration cohorts shrink more heavily toward global evidence than large cohorts.
9. Ranking strategies can be replayed across historical slates and compared by top-rank accuracy plus proper scoring rules.
10. Automatic promotion, production ranking mutation, production calibration mutation, production market-prior mutation, and execution remain disabled.

## Files

```text
artifacts/wow-engine/v17/llp_v17_1_sharpness_challenger.py
artifacts/wow-engine/v17/test_llp_v17_1_sharpness_challenger.py
```

## Design intent

The target is not “looser governance.” The target is separation of concerns:

```text
P(win)                  = sporting forecast
uncertainty interval    = forecast uncertainty
governance              = structural trust/integrity
ranking objective       = user/model objective
market divergence       = diagnostic evidence
```

The challenger should make LLP more discriminating where evidence supports it, while preserving hard fail-closed protections where structural trust is actually broken.
