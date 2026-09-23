# MLB probability-quality + calibration governance audit — 2026-09-23

Status: **CLASS C CHALLENGER / NO PRODUCTION PROMOTION**

Issue: #777

## Governance boundary

This audit does not alter the frozen MLB champion, fitted artifacts, probability outputs, lower-bound governor, publication rules, or terminal authority. `can_execute=false` and dry-run-only remain binding. Any probability-producing change requires challenger replay, untouched temporal holdout, regression review, and governed promotion.

## Live validation evidence

The evidence below was reproduced against the `wow-engine-validation` Supabase project on 2026-09-23.

### Frozen champion is pinned to a non-converged run trainer

Frozen spec `d233092f-3d73-4986-8d76-33f51a9a4fee` points to run trainer `5ae9dbd2-ce9b-4a91-9888-e562a1bd526f`.

That trainer is persisted at:

- `status=TRAINING`
- `iteration=10`
- `gradient_norm=0.40066914413709`
- `learning_rate=0.005`
- `ridge=0.003`
- `training_rows=3330`

The trainer implementation declares convergence only when `gradient_norm < 1e-6`. A second research trainer on the same training-data hash at iteration 20 has a lower gradient norm (~0.234905) and materially larger fitted coefficients. This establishes that the frozen run model was captured before its own declared convergence condition.

This is a candidate root cause for narrow fitted run differences. It is not sufficient by itself to promote a retrained model; replay/holdout evidence is required.

### Active shared-scoring regime is degenerate

Frozen distribution `01eac659-d9ce-4be6-bcd9-7897f33e1ad7` (`MLB_V2C_SHARED_NB_2024_R1`) has:

- `shared_multiplier_variance=0`
- `low_multiplier=1`
- `center_multiplier=1`
- `high_multiplier=1`
- weights `0.25 / 0.50 / 0.25`

Therefore all three configured scoring regimes are numerically identical. Dominance/failure-tail calculations cannot gain regime resolution from the current mixture.

### Canonical forward cohort confirms probability compression

Using the earliest qualified immutable shadow per `(spec_id, official_event_id)`, the current canonical cohort contains **341** graded games.

Observed diagnostics:

- median selected-side probability: `0.5247798051`
- 75th percentile selected-side probability: `0.5374278063`
- maximum selected-side probability: `0.5995767171`
- mean absolute projected run differential: `0.1938973670`
- correlation between projected and actual run differential: `0.1948605884`
- selected-side hit rate: `0.5630498534`
- calibrated Brier: `0.2454792144`
- calibrated log loss: `0.6840754121`

The raw forward probability is only marginally worse on proper scores:

- raw Brier: `0.2465998080`
- calibrated Brier: `0.2454792144`
- raw log loss: `0.6863376534`
- calibrated log loss: `0.6840754121`
- raw selected-side hit rate: `0.5659824047`
- calibrated selected-side hit rate: `0.5630498534`

Thus the incumbent intercept mapping provides a small proper-score improvement while slightly reducing selected-side accuracy. That does not demonstrate repaired probability resolution.

### Current intercept calibration can reverse the fitted side

The V2D calibration function does not fit a standard label-based Platt model. It applies a single logit intercept so the mean 2024 probability matches a pooled historical home-win prior.

In the canonical forward cohort:

- calibrated leader differs from raw leader in **155 / 341 games (45.5%)**;
- the calibrated selected side on those flipped games won **49.68%**;
- in **145** games the calibrated HOME leader had a lower fitted home run mean than the away run mean;
- no symmetric AWAY case was observed where the calibrated away leader had the lower fitted run mean.

This is consistent with a pooled home-prior shift overwhelming a weak/compressed matchup signal. Challenger calibration must therefore be evaluated for both proper-score quality and side/resolution behavior.

### Calibration-health `PASS` is not a quantitative calibration pass

The current health row reports `calibration_health_status=PASS` with:

- `graded_shadow_n=341`
- `local_bin_min_n=68`
- `local_bin_max_abs_gap=0.1213162040`

The current assessment function blocks only for insufficient graded sample/provenance. It does not use Brier, log loss, ECE, calibration intercept/slope, or maximum reliability-bin error to determine `PASS`.

The challenger harness therefore adds an explicit quantitative calibration-health assessor, but it does **not** replace production health semantics in this PR.

### Current lower bound is cohort reliability evidence, not event uncertainty

`wow_mlb_v2d_candidate_bounds` selects a historical calibration bin and returns `LOCAL_DECILE_WILSON_95`. Current bins are approximately 68–69 historical games each (2024-08-10 through 2024-09-30).

That interval answers a cohort-reliability question. It is not an event-specific uncertainty interval for one matchup. Existing production columns remain untouched; the follow-on design must separate cohort reliability from event-level uncertainty.

### Grading cohort is early pre-lineup, not final published pregame

For the 341 canonical graded games, immutable score-snapshot states are:

- 334 `SHADOW_SCORED_LINEUP_PENDING`
- 7 `SHADOW_SCORED_BOUND_BLOCKED`
- 0 lineup-confirmed at score time

Some mutable event rows were later updated to `CONFIRMED`, but that cannot retroactively change the identity/time of the immutable score snapshot being graded.

Required follow-on ledgers:

1. `EARLY_PREGAME_MODEL_GRADE`
2. `FINAL_PREGAME_PUBLISHED_GRADE`
3. `DOMINANCE_DIAGNOSTIC`

### Legacy duplicate grade exists and replay must canonicalize it

The immutable grade table currently has 342 grade rows for 341 distinct official games. Official event `823985` has two historical grade rows. No immutable row should be deleted. Replay and calibration metrics must canonicalize by official event using the same earliest-qualified rule as governance.

### Counterexample replay confirms missed dominance cases

Among canonical selected-side probabilities from 50% through 55%:

- **35** winners finished by 5+ runs;
- **23** winners finished by 7+ runs.

Examples with 7+ margins include games where the selected side had an extremely small or even opposite-signed fitted run-mean differential, including a 16–1 selected-side win at `p=0.5207` where the selected home run mean was slightly lower than the away run mean.

This is diagnostic evidence for missing matchup-resolution and/or variance structure. It must not be converted mechanically into a higher P(win).

### Existing 4-point lower-bound-gap governor still has discrimination

For the canonical cohort, the current `< 0.04` gap would skip 71 games. The selected side in those games won `49.30%`, while 5 of those skipped selections won by 5+ runs and 5 won by 7+ runs.

Therefore threshold relaxation is not the root fix. Re-test the governor only after probability resolution, calibration, and uncertainty semantics are corrected.

## Challenger infrastructure in this branch

`v17/mlb_probability_quality.py` adds research-only utilities for:

- canonical immutable replay de-duplication;
- Brier, log loss, equal-count ECE, maximum calibration gap, calibration intercept/slope, ROC-AUC, hit rate, and probability-resolution metrics;
- quantitative calibration-health assessment under an explicit policy;
- chronological calibration challenge with fit -> selection -> untouched test separation;
- identity, full Platt/logit-affine, beta, and isotonic calibration candidates;
- dominance diagnostics from fitted score PMFs: regulation/extra-inning outright P(win), P(win by 2+), P(win by 4+), P(win by 6+), expected run differential, P(loss by 4+), and blowout asymmetry.

All challenger outputs retain:

- `automatic_promotion=false`
- `probability_publishable=false`
- `can_execute=false`

## Still required before issue #777 can close

- retrain the run-model challenger to its declared convergence/early-stop contract without mutating the frozen champion;
- regenerate a non-degenerate distribution challenger where earned by residual evidence;
- run chronological replay/holdout comparing incumbent vs run-model + calibrator combinations;
- produce quantitative calibration-health thresholds from certified evidence rather than hand-tuning them to pass;
- design/validate event-specific uncertainty separately from cohort reliability;
- persist final-pregame published grading separately from early shadow grading;
- run full counterexample review and threshold audit on the corrected stack;
- prove Brier/log loss/ECE/calibration intercept+slope/discrimination/resolution/hit-rate regression on untouched data;
- submit any champion replacement through governed Class C review.
