# MLB Prequential Run-Contrast Adaptation Receipt — 2026-09-23

Related: #777 / PR #778

Status: **EXPERIMENT_CREATED / REJECTED_FOR_PROMOTION**

This experiment is Class C research only. It does not alter the frozen MLB champion, specialist registry, production probabilities, calibration/qualification thresholds, publication rules, terminal authority, or execution capability. `can_execute=false` throughout.

## Hypothesis

Prior evidence showed a strong season-regime inversion: the same iteration-20 run backbone needed probability shrinkage in 2025 but strong de-compression in 2026. A probability-only rolling calibrator improved all tracked metrics in 2026 but failed cross-season in 2025.

This experiment moved adaptation upstream into the run model rather than fitting another final-probability map.

For baseline fitted log run means `lh=log(home_mu)` and `la=log(away_mu)`:

- `center = (lh + la) / 2`
- `contrast = (lh - la) / 2`
- adjusted home log mean = `center + gamma * contrast`
- adjusted away log mean = `center - gamma * contrast`

`gamma` is fitted by Poisson likelihood from strictly earlier settled HOME/AWAY run counts within the season.

Governance invariants:

- same-day outcomes excluded;
- future outcomes excluded;
- minimum prior sample 60 games;
- `gamma` constrained positive (`0.05 <= gamma <= 5.0`), so HOME/AWAY fitted ordering cannot flip;
- geometric mean of HOME/AWAY fitted run means is preserved, so the adaptation changes separation strength rather than the common scoring environment;
- game probabilities still come from the existing governed shared-negative-binomial reducer;
- no sportsbook probability or market signal enters fitting;
- research only, non-publishable, `can_execute=false`.

The chronology and center/order invariants are source-controlled in `v17/mlb_prequential_run_contrast_challenger.py` and regression-tested.

## 2025 prequential replay

Backbone: iteration-20 run trainer `76a28c25-f6f0-49bd-a642-bb6ca6604ac4`, distribution `27423ed3-5dc7-4541-8cdf-3e0b3fd0086b`.

Evaluation begins Aug 15, 2025 once 63 prior scored games exist and continues through Nov 1. n=657.

Observed run-contrast scale range: **0.319 to 0.771** (seasonal shrinkage).

| Metric | Incumbent calibrated | Iter20 raw | Iter20 run-contrast |
|---|---:|---:|---:|
| Brier | **0.244369** | 0.245092 | 0.246055 |
| Log loss | **0.681856** | 0.683437 | 0.685255 |
| ROC-AUC | 0.586988 | **0.588021** | 0.585312 |
| Hit rate | 57.38% | **58.30%** | 58.14% |
| ECE | **4.68%** | 8.47% | 6.30% |
| Max equal-count bin gap | **10.90%** | 14.21% | 16.06% |

The model-level contrast adaptation does not improve the 2025 incumbent and therefore fails generalization.

## 2026 prequential forward replay

The identical rule was applied to the immutable 2026 forward feature/score cohort. Evaluation begins Sep 2 once 66 prior games exist and continues through Sep 23. n=276.

Observed run-contrast scale range: **0.726 to 2.677**, showing a materially different within-season regime from 2025.

| Metric | Incumbent calibrated | Iter20 raw | Iter20 run-contrast |
|---|---:|---:|---:|
| Brier | 0.245221 | 0.244960 | **0.241979** |
| Log loss | 0.683552 | 0.683035 | **0.676838** |
| ROC-AUC | 0.617451 | **0.622625** | 0.619827 |
| Hit rate | 55.80% | **56.88%** | **56.88%** |
| ECE | 9.54% | 10.85% | **6.70%** |
| Max equal-count bin gap | 22.74% | 23.21% | **14.28%** |

Versus the incumbent, the run-contrast challenger improves all tracked metrics in the 2026 prequential cohort. It also repairs most of the raw iteration-20 calibration error while retaining an AUC improvement over the incumbent.

However, the identical architecture fails the 2025 replay. The experiment therefore does **not** earn promotion.

Exact blocker: `CROSS_SEASON_RUN_CONTRAST_GENERALIZATION_FAILED`.

## Feature-distribution drift audit

The frozen 2024 standardization frame was compared with 2025 training features and 2026 forward snapshots. Large mean shifts exist in history-depth fields, especially:

- `min_team_prior_games`: ~+2.73 frozen-standard-deviations in 2026;
- `park_prior_games`: ~+2.68;
- `opp_starter_prior_starts`: ~+1.70.

But the incumbent coefficients on the two largest shifts are small. Their mean linear-predictor contributions are approximately +0.016 and +0.013 log-run units and are largely common to both sides. This is real schema/scale drift worth guarding, but it is not sufficient by itself to explain the HOME/AWAY ranking defect.

## Conclusion

Two independent chronology-safe adaptations now show the same structural result:

- **2026:** strong evidence that the iteration-20 backbone plus same-season adaptation can improve proper scores, ranking, hit rate, and calibration simultaneously;
- **2025:** the same universal adaptation does not beat the incumbent.

This strengthens the diagnosis that WOW MLB has a **season/regime model-governance problem**, not merely a scalar probability-compression defect.

The next earned Class C design is a multiseason fitted run specialist with explicit temporal/regime structure and leave-season/time-forward validation. A future untouched forward cohort remains required before production adoption.

Unchanged:

- frozen V17 MLB champion;
- production sporting probabilities;
- specialist registry;
- qualification/publication thresholds;
- 4-point lower-bound-gap governor;
- typed V17 failure semantics;
- `V17_TERMINAL_REDUCER` sole terminal authority;
- `can_execute=false`;
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`.
