# MLB feature-ablation challenger receipt — 2026-09-23

Issue #777 / PR #778

Status: **REJECTED CHALLENGERS — no production promotion**

## Purpose

The probability-resolution audit found some feature correlations that were unstable across chronological windows. We tested whether removing unstable features could improve discrimination while retaining the proper-score/resolution gains of the iteration-20 run-model challenger.

No production artifact was changed. Every research trainer/distribution remained `can_execute=false` and non-publishable.

## Exploratory five-feature ablation

An exploratory iteration-20 research clone zeroed five previously suspicious coefficients. After centered-logit calibration fit on Aug 10–Sep 15, 2024, the untouched Sep 16–30 block produced:

- mean absolute fitted run diff: `0.31576`
- Brier: `0.242741`
- log loss: `0.678052`
- hit rate: `55.68%`
- ROC-AUC: `~0.57983`

This improved proper scores but did not exceed incumbent discrimination (`~0.58006`) and underperformed the unablated iteration-20 challenger on hit rate. **Rejected.**

## Pre-registered three-feature ablation

To remove test leakage from feature selection, the next candidate used only training (`<= 2024-08-09`) versus selection (`2024-08-10 .. 2024-09-15`) correlation behavior.

Pre-declared rule: zero only features whose train/selection correlation changes sign and whose absolute selection correlation is at least `0.03`.

That selected exactly:

- `opp_bp_hr_rate` (feature 19)
- `opp_starter_prior_starts` (feature 23)
- `opp_starter_bb_rate` (feature 26)

The candidate was trained at the same iteration-20/ridge-0.003 horizon. Its centered-logit slope was fit only on Aug 10–Sep 15: approximately `2.32672`.

Untouched Sep 16–30 results:

- mean absolute fitted run diff: `0.33679`
- Brier: `0.243935`
- log loss: `0.680515`
- hit rate: `54.59%`
- probability stddev: `0.07639`
- median selected probability: `55.19%`
- p75 selected probability: `58.91%`
- maximum selected probability: `71.77%`
- ROC-AUC: `~0.57223`

Although this candidate increased run-difference separation, its hit rate regressed and discrimination materially worsened. **Rejected.**

## Conclusion

The evidence does not support manual feature deletion as the immediate repair. The unablated iteration-20 + centered-logit challenger remains the strongest current candidate on proper scores, calibration, probability spread and hit rate, but it still has a small ROC-AUC regression versus the incumbent on the 185-game untouched block (`~0.57889` vs `~0.58006`).

Therefore the full user acceptance bar is **not yet met**. The next Class C model work should focus on an independently designed feature-strength/matchup challenger with pre-registered selection criteria and genuinely new forward certification—not further ad-hoc ablation against the already-opened Sep 16–30 test.

No threshold changes, lower-bound relaxation, champion replacement, publication widening, or execution changes were made.
