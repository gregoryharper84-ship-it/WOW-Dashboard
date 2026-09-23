# MLB probability-quality challenger receipt — 2026-09-23

Issue: #777  
PR: #778  
Status: **EXPERIMENT_CREATED — CHAMPION UNCHANGED**

This receipt supersedes any earlier interpretation that the repair is simply “train the frozen run model to numerical convergence” or “force the three scoring regimes to differ.” Both hypotheses were explicitly tested and rejected as sufficient fixes.

## Safety / governance

No production probability behavior has changed.

- Frozen MLB champion remains unchanged.
- Existing 4-point lower-bound-gap governor remains unchanged.
- No publication rule is widened.
- `V17_TERMINAL_REDUCER` authority is unchanged.
- `automatic_promotion=false`.
- `probability_publishable=false` for challenger artifacts.
- `can_execute=false`.
- No market-implied or generic LLM probability substitutes for the fitted specialist.

## Refined root-cause findings

### 1. Probability compression is real

Canonical 2026 forward replay uses the earliest qualified immutable shadow per official event, yielding 341 games.

Incumbent calibrated output:

- median selected probability: `52.48%`
- 75th percentile: `53.74%`
- maximum: `59.96%`
- mean absolute fitted run differential: `0.194 runs`
- projected-vs-actual run-differential correlation: `0.195`
- selected-side hit rate: `56.30%`
- Brier: `0.24548`
- log loss: `0.68408`

There are 342 immutable grade rows for 341 official events because official event `823985` has a historical duplicate grade. No immutable row is deleted; replay canonicalizes by official event.

### 2. The current intercept-only calibration leaves severe underconfidence

The incumbent V2D calibration is a pooled home-prevalence logit intercept shift. It does not fit calibration slope to outcome labels.

On the canonical 2026 forward cohort:

- raw calibration slope: `5.266`
- incumbent-intercept calibration slope: `5.266`
- raw calibration intercept: `+0.160`
- incumbent calibration intercept: `-0.396`

The unchanged slope shows that the intercept transform moves the mean but does not repair probability resolution.

The same incumbent transform changes the raw selected side in `155 / 341` games (`45.5%`). Those flipped selections won only `49.68%`. In 145 games the calibrated HOME leader had a lower fitted home run mean than the away run mean.

### 3. Full Platt calibration repairs much of the underconfidence, but its intercept can move the 50% side boundary

A temporal full-Platt challenger was fit only on Aug 10–Sep 15, 2024 (499 games):

- slope: `3.34944`
- intercept: `0.14836`

On the untouched Sep 16–30, 2024 block (185 games):

| Metric | Incumbent intercept | Temporal full Platt |
|---|---:|---:|
| Brier | 0.24746 | **0.24493** |
| Log loss | 0.68803 | **0.68244** |
| Hit rate | 54.59% | **55.14%** |
| ECE (10 equal-count bins) | 9.69% | **8.36%** |
| Calibration slope | 3.573 | **1.067** |
| Calibration intercept | -0.395 | **-0.176** |
| Median selected probability | 52.76% | 56.07% |
| 75th percentile selected probability | 54.29% | 59.84% |

This is strong temporal evidence that the incumbent is underconfident.

A beta-calibration challenger on the same temporal split also improves materially but is slightly worse than full Platt on proper scores:

- beta Brier: `0.24498`
- beta log loss: `0.68258`

A stable 20-bin monotone/PAVA isotonic screen is worse than both parametric challengers:

- isotonic Brier: `0.24661`
- isotonic log loss: `0.68635`

The PR still contains an exact sklearn isotonic implementation for future replay; no isotonic method is selected for promotion.

### 4. “Train longer” is not the whole model fix; model selection was using the wrong downstream objective

The frozen run trainer is stored at iteration 10 with `status=TRAINING` and gradient norm about `0.401`. A research iteration-20 state exists at gradient norm about `0.235`.

On run-level MSE/NLL, blindly continuing from iteration 10 toward iteration 20 does not consistently improve the chronological holdout and can slightly worsen it. Therefore numerical convergence alone is not an earned fix.

However, WOW's governed target is game win probability, not run MSE by itself. When the two trainers are evaluated through the fitted score distribution on the actual downstream probability task, iteration 20 is better.

Untouched Sep 16–30, 2024 raw game-probability comparison:

| Metric | Iteration 10 | Iteration 20 |
|---|---:|---:|
| Mean absolute fitted run diff | 0.237 | **0.334** |
| Brier | 0.24659 | **0.24550** |
| Log loss | 0.68630 | **0.68407** |
| Hit rate | 55.14% | **56.76%** |
| Median selected probability | 51.59% | **52.28%** |
| 75th percentile | 52.64% | **53.70%** |
| Maximum | 57.25% | **60.01%** |

This identifies a fitting/governance defect: run-trainer stopping/selection was not being validated against the downstream probability objective that the specialist ultimately governs.

### 5. Leading combined challenger: iteration-20 run model + centered logit calibration

Ordinary affine Platt calibration improves proper scores but a positive intercept moves the 50% decision boundary. On the iteration-20 holdout that reduced selected-side hit rate despite improved Brier/log loss.

A centered logit/temperature challenger therefore fits only a positive slope and fixes the intercept at zero. This preserves `p=0.5 -> 0.5` and cannot reverse the raw fitted winner.

Fit window: Aug 10–Sep 15, 2024.  
Iteration-20 centered slope: `2.34367`.

Untouched Sep 16–30, 2024:

| Metric | Incumbent | Iter20 + centered slope |
|---|---:|---:|
| Mean absolute fitted run diff | 0.237 | **0.334** |
| Brier | 0.24746 | **0.24310** |
| Log loss | 0.68803 | **0.67879** |
| Hit rate | 54.59% | **56.76%** |
| ECE | 9.69% | **8.81%** |
| Maximum calibration-bin gap | 21.24% | **15.95%** |
| Calibration intercept | -0.395 | **-0.024** |
| Calibration slope | 3.573 | **1.116** |
| Median selected probability | 52.76% | **55.34%** |
| 75th percentile | 54.29% | **58.61%** |
| Maximum | 59.82% | **72.13%** |

This is the current leading challenger because it improves all required holdout directions while preserving the model-selected side.

### 6. Later 2026 replay is supportive but no longer qualifies as untouched certification evidence

Using the exact immutable 2026 feature snapshots, the iteration-20 + centered calibration counterfactual gives:

- mean absolute run diff: `0.279`
- run-diff correlation: `0.198`
- Brier: `0.24121`
- log loss: `0.67535`
- hit rate: `58.06%`
- median selected probability: `54.12%`
- 75th percentile: `56.94%`
- maximum: `75.86%`

These are exploratory/forward diagnostics only because this cohort has now been repeatedly inspected. It cannot be used as the final untouched certification set. Its ECE is also not uniformly improved, which is another reason not to promote from this evidence alone.

### 7. Zero shared-regime variance is data-driven under the current positive-shared-multiplier family

Rebuilding the score distribution from the iteration-20 trainer still produces `shared_multiplier_variance=0`.

Training residual covariance is negative:

- iteration 10 residual covariance: `-0.1197`
- iteration 20 residual covariance: `-0.1073`

The distribution fitter explicitly clamps shared multiplier variance to `max(0, covariance / mean_product)`. Therefore forcing non-zero low/center/high multipliers would manufacture unsupported positive shared covariance. The correct next variance experiment is a different tail/dispersion family, not an arbitrary three-regime spread.

### 8. Feature audit supports a stable-feature / regularized challenger

The strongest standardized run coefficients in the frozen model are only about `0.005–0.010` log-runs per SD. Several features have stronger forward correlation than their fitted weight suggests, while several others reverse sign between training and holdout.

Examples:

- opponent starter K rate: beta about `-0.0102`, holdout correlation about `-0.115`
- offense total bases/game: beta about `+0.0092`, holdout correlation about `+0.097`
- offense runs/game: beta about `+0.0067`, holdout correlation about `+0.112`
- opponent runs allowed/game: beta about `+0.0056`, holdout correlation about `+0.128`
- park total runs prior: beta only about `+0.0012`, holdout correlation about `+0.141`

This warrants a stable-feature/regularization challenger. It does not justify manually inflating selected coefficients.

## Governance defects confirmed independently of champion replacement

### Calibration health

Current `PASS` is primarily a sample/provenance gate. The live row can say PASS while local maximum calibration-bin gap is about `12.13%`. The research harness now computes Brier, log loss, ECE, max bin gap, calibration intercept/slope, ROC-AUC, hit rate and probability spread, but production `PASS` is intentionally not changed until quality thresholds are certified.

### Lower-bound semantics

Current `LOCAL_DECILE_WILSON_95` bounds are historical cohort reliability intervals from roughly 68–69 historical games per bin. They are not event-specific uncertainty. They must remain a distinct evidence type from a future bootstrap/parameter-based event uncertainty interval.

### Grading semantics

Canonical graded immutable snapshots are:

- 334 `SHADOW_SCORED_LINEUP_PENDING`
- 7 `SHADOW_SCORED_BOUND_BLOCKED`
- 0 lineup-confirmed at score time

A mutable event row becoming CONFIRMED later cannot convert the earlier frozen score into a final-pregame prediction. Required ledgers remain:

1. `EARLY_PREGAME_MODEL_GRADE`
2. `FINAL_PREGAME_PUBLISHED_GRADE`
3. `DOMINANCE_DIAGNOSTIC`

### Dominance diagnostics

The branch includes research-only score-PMF diagnostics for:

- regulation win probability
- tie-after-9 probability / explicit extra-inning contribution
- P(win by 2+)
- P(win by 4+)
- P(win by 6+)
- expected run differential
- P(loss by 4+)
- blowout asymmetry

These diagnostics do not rewrite outright P(win).

## Counterexample evidence

Among canonical 50–55% selected-side predictions in the seen 2026 cohort:

- 35 winners finished by 5+ runs
- 23 winners finished by 7+ runs

The current 4-point lower-bound-gap filter would have skipped 71 games. Those skipped selected sides won only about 49.3%, despite five 5+ wins and five 7+ wins. Therefore threshold relaxation remains explicitly rejected as the repair.

## What still blocks promotion / issue closure

The leading challenger is **not production-certified**. Before #777 can close:

1. Generate a genuinely untouched forward or later-season certification set not used in this investigation.
2. Replay the iteration-20 + centered calibration challenger there with pre-registered metrics and thresholds.
3. Build and compare the stable-feature/regularized run-model challenger.
4. Test an earned alternative tail/dispersion model; do not force positive shared covariance.
5. Implement a true event-specific uncertainty experiment (bootstrap/parameter uncertainty) while preserving Wilson cohort reliability as a separate field.
6. Implement immutable final-pregame published grading and dominance ledgers.
7. Define quantitative calibration-health V2 thresholds from certified evidence, then review the production PASS semantics.
8. Re-audit the 4-point lower-bound-gap rule only after the uncertainty semantics are corrected.
9. Run normalization, bound-validity, typed-failure, final-refresh, immutable-prediction, terminal-authority and `can_execute=false` regression.
10. Submit any champion replacement through governed Class C review; no automatic promotion.

Current terminal engineering status for this slice: **EXPERIMENT_CREATED / PR_CREATED**, not FIXED_AND_VERIFIED.
