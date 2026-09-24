# MLB Forward Discrimination Follow-up Addendum — 2026-09-23

Related: #777 / PR #778

Status: **EXPERIMENT_CREATED / REJECTED_FOR_PROMOTION**

This addendum continues the forward-discrimination receipt after the original two-logit stack result. Every experiment here is research-only. The frozen MLB champion, production probability path, publication rules, lower-bound governor, `V17_TERMINAL_REDUCER`, and `can_execute=false` remain unchanged.

## Defect contract

Expected: a Class C MLB challenger must improve proper scoring, calibration quality, discrimination, and selected-side performance across independent temporal/season evidence before any champion replacement can be considered.

Observed: prior challengers improved calibration/resolution but lost ROC-AUC. The remaining defect is not solved by simply stretching probabilities or changing thresholds.

Change class: **Class C**.

Done for this cycle means: test ranking-specific hypotheses, terminate every failed trainer/hypothesis explicitly, preserve chronology, use HOME probability vs HOME outcome for discrimination/calibration metrics, and reject promotion if cross-season acceptance is not met.

## 1. Semantic frame guard caught and enforced

`wow_mlb_forward_shadow_grades.actual_outcome` is selected-side correctness, not the conventional HOME binary target. One intermediate ranking query using that field was discarded immediately.

All accepted AUC/calibration comparisons in this addendum use `wow_mlb_forward_shadow_events.home_win` as the binary outcome. This matches Calibration Health V2 semantics and prevents selected-side grading from contaminating HOME-probability discrimination metrics.

## 2. Narrow Direct36 tie-breaker — rejected

Hypothesis: Direct36 may add useful ordering information only where the run model is nearly indifferent, while broader blending damages strong run-model ordering.

A fixed gate was selected only on the 2024 Aug 10–Sep 15 selection block:

- use Direct36 only when `abs(P_run - 0.5) <= 0.015`;
- additive logit weight `gamma = 1.0`;
- selection objective: maximize AUC without reducing selected-side hit rate.

On the later Sep 16–30, 2024 block, without retuning:

- run AUC: **0.580061**
- gated AUC: **0.589995**
- run hit rate: **55.14%**
- gated hit rate: **56.22%**

However the same fixed gate failed on 2026 forward evidence:

### 2026 full canonical cohort, n=342

- run AUC: **0.617582**
- gated AUC: **0.586813**
- run hit rate: **56.73%**
- gated hit rate: **58.77%**

### 2026 Sep 16–23 block, n=90

- run AUC: **0.664523**
- gated AUC: **0.612568**
- run hit rate: **56.67%**
- gated hit rate: **58.89%**

The gate improves classification hit rate but materially damages probability ranking. It is **REJECTED_FOR_PROMOTION** with blocker `DISCRIMINATION_REGRESSION_FORWARD_2026`.

## 3. Compact residual-discrimination specialist — rejected and stopped

A cross-season residual screen identified features whose incremental residual association had the same sign in 2024 and 2025. A deliberately small detached specialist used:

1. run logit backbone;
2. bullpen ERA differential;
3. starter K-rate differential;
4. starter H-rate differential;
5. starter ERA differential;
6. starter strike-rate differential;
7. runs-allowed differential.

Trainer evidence:

- trainer id: `b7168399-911f-43d3-aeff-58faa6bf8622`
- fit rows: **2,176**
- training SHA-256: `a6c549bf7643b2e4658ecb6ff092f0a87551426f5d3bc08650f8cf612ea42628`
- ridge: 0.003
- learning rate: 0.05
- `can_execute=false`

The feature set and 30-step horizon were fixed before the Sep 16–30, 2024 evaluation.

At iteration 30 on that evaluation block:

| Metric | Run baseline | Residual specialist |
|---|---:|---:|
| Brier | **0.246590** | 0.251119 |
| Log loss | **0.686302** | 0.695409 |
| ROC-AUC | **0.580061** | 0.566269 |
| Hit rate | **55.14%** | 49.73% |
| Probability stddev | 0.02398 | 0.02432 |

The trainer was explicitly moved to `STOPPED` at iteration 30. No registry/spec/distribution/publication artifact was created.

Disposition: **EXPERIMENT_CREATED / REJECTED_FOR_PROMOTION**.

## 4. Existing iteration-20 run specialist — strongest new forward ranking evidence

The existing detached iteration-20 run trainer was forward-scored directly from immutable 2026 Run38 HOME/AWAY snapshots using its own persisted distribution:

- run trainer: `76a28c25-f6f0-49bd-a642-bb6ca6604ac4`
- iteration: 20
- distribution: `27423ed3-5dc7-4541-8cdf-3e0b3fd0086b`
- distribution family: `MLB_V2C_SHARED_NB_2024_R1`
- shared multiplier variance: 0
- `can_execute=false`

### 2026 Sep 16–23 forward block, n=90

Raw iteration 20 versus incumbent raw ranking frame:

- Brier: **0.243416** vs 0.245372
- log loss: **0.679925** vs 0.683875
- ROC-AUC: **0.666502** vs 0.664523
- hit rate: **60.00%** vs 56.67%
- probability stddev: **0.02986** vs 0.02044

This is the first tested discrimination challenger in this cycle to improve AUC as well as proper scores and hit rate on the later 2026 block.

### 2026 full canonical cohort, n=342

Compared with the actual calibrated incumbent:

| Metric | Incumbent calibrated | Iteration 20 raw |
|---|---:|---:|
| Brier | 0.245528 | **0.245143** |
| Log loss | 0.684174 | **0.683405** |
| ROC-AUC | 0.617582 | **0.621257** |
| Hit rate | 56.14% | **58.19%** |
| Probability stddev | 0.01949 | **0.02777** |

But raw calibration remains unacceptable:

- incumbent ECE: **10.23%**
- iteration-20 raw ECE: 11.53%
- incumbent max equal-count bin gap: **21.83%**
- iteration-20 raw max gap: 27.53%

Therefore raw iteration 20 is **not** independently promotable.

## 5. Fixed chronological calibration of iteration 20 — improved proper scores, incomplete bin calibration

Chronology was fixed before test evaluation:

- calibration fit: Aug 28–Sep 15, 2026, n=252
- test: Sep 16–23, 2026, n=90
- no test outcome entered the calibration fit.

Fitted maps:

- centered slope: **3.541345**
- full Platt: **intercept 0.134691, slope 3.514722**

On the 90-game later block, full Platt produced:

- Brier: **0.233644** vs incumbent 0.244700
- log loss: **0.659272** vs incumbent 0.682489
- ROC-AUC: **0.666502** vs incumbent 0.664523
- hit rate: **65.56%** vs incumbent 53.33%
- ECE: **15.21%** vs incumbent 15.45%
- max bin gap: 46.17% vs incumbent **39.97%**

The worst-bin error fails the full acceptance contract. The test block is now selection evidence and cannot be reused as untouched certification evidence.

## 6. Strictly prequential rolling centered calibration — 2026 success, 2025 failure

A chronology-safe rolling calibrator was then tested. For every game date:

- only strictly earlier settled rows may fit the slope;
- same-day outcomes are excluded;
- future outcomes are excluded;
- minimum prior sample = 60;
- the centered map keeps 0.5 fixed;
- negative slopes are fail-closed;
- no side-flipping prevalence intercept is allowed.

The implementation is source-controlled in `v17/mlb_prequential_calibration_challenger.py` with tests that flip same-day/future outcomes and verify earlier/current predictions cannot change.

### 2026 prequential evaluation

Evaluation begins Sep 2 once the prior cohort reaches 66 rows and continues through Sep 23. n=276.

| Metric | Incumbent calibrated | Iter20 rolling centered |
|---|---:|---:|
| Brier | 0.245221 | **0.240515** |
| Log loss | 0.683552 | **0.673852** |
| ROC-AUC | 0.617451 | **0.618296** |
| Hit rate | 55.80% | **56.88%** |
| ECE | 9.54% | **8.77%** |
| Max bin gap | 22.74% | **15.97%** |

Observed rolling slope range: **1.539 to 3.992**.

This is the first tested architecture in the cycle to improve every tracked forward metric simultaneously on a prequential 2026 cohort.

### 2025 prequential replay

The identical rule was replayed on the iteration-20 2025 scores. Evaluation begins Aug 15 once the prior cohort reaches 63 rows and continues through Nov 1. n=657.

| Metric | Incumbent calibrated | Iter20 rolling centered |
|---|---:|---:|
| Brier | **0.244369** | 0.246232 |
| Log loss | **0.681856** | 0.685660 |
| ROC-AUC | **0.586988** | 0.583282 |
| Hit rate | 57.38% | **58.30%** |
| ECE | **4.68%** | 5.78% |
| Max bin gap | **10.90%** | 14.68% |

Observed rolling slope range: **0.311 to 1.006**.

The same architecture therefore does not generalize across seasons. The slope regime inversion is substantial: 2025 requires shrinkage while 2026 requires strong de-compression.

Disposition: **EXPERIMENT_CREATED / REJECTED_FOR_PROMOTION** despite the strong 2026 result.

Exact blocker: `CROSS_SEASON_CALIBRATION_GENERALIZATION_FAILED`.

## 7. 2025-trained run specialist — rejected before 2026 forward scoring

A season-specific 2025 run trainer was evaluated on its own Aug 10–Nov 1, 2025 holdout:

- trainer: `c09d6efe-f517-4832-9f83-16209c3e6302`
- iteration: 40
- distribution: `2b9ef808-8f2f-4f0c-b834-4fbcad6067a1`
- training end: 2025-08-09
- holdout rows: 720
- `can_execute=false`

Against the calibrated incumbent:

| Metric | Incumbent calibrated | 2025-trained iter40 raw |
|---|---:|---:|
| Brier | **0.244517** | 0.246321 |
| Log loss | **0.682125** | 0.685778 |
| ROC-AUC | **0.581498** | 0.572996 |
| Hit rate | **56.39%** | 56.11% |
| ECE | **4.37%** | 4.91% |
| Max bin gap | 14.06% | **7.83%** |

Only worst-bin calibration improves; overall probability quality and discrimination regress. This artifact was rejected before any 2026 forward scoring.

A later iteration-50 trainer (`057904b6-8949-4e90-9435-66ef1e3ef0d4`) was also checked before constructing a distribution. Relative to iteration 40, its 2025 calibration-window run MAE/MSE/Poisson NLL are flat-to-worse, so no new distribution was created.

## 8. Engineering/model conclusion

The evidence now rules out four shortcuts:

1. broad Direct36 mixing;
2. narrow run-uncertainty tie-breaking;
3. a small linear residual classifier over existing direct features;
4. calibration-only or naive season-specific refitting as a universal repair.

Iteration-20 run scoring remains the most promising ranking backbone, and the 2026 prequential rolling calibration result is materially positive. But the 2025 replay proves that the probability mapping is season/regime dependent enough that a universal promotion is not earned.

The next justified Class C hypothesis is a **multiseason run model with explicit season/phase or hierarchical regime structure**, evaluated with true leave-season/time-forward validation. Calibration should then be fitted within the governed model-validation protocol rather than used to compensate for unresolved model drift.

A future untouched forward cohort is still required before any production adoption.

Unchanged throughout:

- frozen V17 MLB champion;
- production sporting probabilities;
- fitted specialist registry;
- calibration/qualification thresholds;
- publication/rank eligibility;
- 4-point lower-bound-gap governor;
- typed V17 failure semantics;
- `V17_TERMINAL_REDUCER` sole terminal authority;
- `can_execute=false`;
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`.
