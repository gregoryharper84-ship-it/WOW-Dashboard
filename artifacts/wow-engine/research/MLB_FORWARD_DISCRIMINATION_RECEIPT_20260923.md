# MLB Forward Discrimination Challenger Receipt — 2026-09-23

Related: #777 / PR #778

Status: **EXPERIMENT_CREATED / REJECTED_FOR_PROMOTION**

This receipt is research evidence only. It does not alter the frozen MLB champion, production probabilities, publication eligibility, lower-bound policy, terminal authority, or execution capability. `can_execute=false` throughout.

## Defect contract

Expected: an MLB probability challenger must improve probability quality and ranking/selection quality out of sample before Class C promotion can be considered.

Observed: the incumbent forward probabilities remain compressed. Prior Direct36 and calibration challengers improved proper scores and resolution but did not improve discrimination consistently.

Severity: model-quality / governance defect.

Change class: **Class C**.

Done for this experiment means: prove forward feature compatibility, use canonical immutable forward grades, fit without test leakage, compare Brier/log loss/ECE/bin error/calibration slope+intercept/AUC/hit rate, and reject promotion if any required dimension regresses.

## 1. Forward feature compatibility proved exactly

All 1,958 current `wow_mlb_forward_feature_snapshots` rows use the same 38-feature order as the frozen MLB run trainer. All 1,958 are `hydration_status=PASS` and all scored feature snapshots use that same order.

The historical Direct36 game vector is a deterministic transform of the HOME/AWAY Run38 pair. The mapping was validated against all 2,349 2024 games for which both representations coexist:

- games checked: **2,349**
- feature cells checked: 84,564
- maximum absolute reconstruction delta: **1.4210854715202e-14**
- cells differing by more than `1e-12`: **0**
- games with any mismatch: **0**

No approximation or new feature was introduced. The transform is source-controlled in `v17/mlb_forward_discrimination_challenger.py`.

## 2. Genuinely later forward cohort

Immutable forward grades span **2026-08-28 through 2026-09-23**:

- grade rows: 343
- distinct official events: 342
- rows with both HOME/AWAY feature snapshots: 343
- rows with PASS feature pairs: 343
- executable rows: 0

One duplicate official event (`823985`) exists. Replay canonicalizes by `official_event_id` and retains the earliest frozen prediction timestamp; immutable evidence is not deleted.

## 3. Direct36 forward screen

On the 342 canonical events:

| Metric | Incumbent calibrated | Pooled Direct36 iter30 |
|---|---:|---:|
| Brier | 0.245528 | **0.243866** |
| Log loss | 0.684174 | **0.680822** |
| Selected-side hit rate | 56.14% | **57.89%** |
| Probability stddev | 0.01949 | **0.03727** |
| Median selected P | 52.48% | **52.88%** |
| P75 selected P | 53.74% | **55.04%** |
| Max selected P | 59.96% | **62.28%** |
| ECE | 10.23% | **7.98%** |
| Max equal-count bin gap | 21.83% | **17.61%** |
| ROC-AUC | **0.61758** | 0.60563 |

Direct36 improves proper scores, hit rate, calibration error, and resolution but loses ranking discrimination. It therefore remains **REJECTED_FOR_PROMOTION**.

## 4. Chronological two-logit stack

To test whether the run model's ranking signal and Direct36's calibration signal are complementary, a small fitted stack was defined as one research specialist:

`logit(P_home) = b0 + b_run * logit(P_run_raw) + b_direct * logit(P_direct36)`

Chronology was fixed before evaluation:

- fit only: **2026-08-28 through 2026-09-15**, n=252
- test only: **2026-09-16 through 2026-09-23**, n=90
- ridge: 0.001 on the two slope coefficients
- no test outcomes entered coefficient fitting

Converged fit coefficients:

- intercept: ~0.06406
- run-logit weight: ~3.33774
- Direct36-logit weight: ~0.85224

### Later test block

| Metric | Incumbent calibrated | Run raw | Direct36 | Two-logit stack |
|---|---:|---:|---:|---:|
| n | 90 | 90 | 90 | 90 |
| Brier | 0.244700 | 0.245372 | 0.243199 | **0.235334** |
| Log loss | 0.682489 | 0.683875 | 0.679437 | **0.662779** |
| Hit rate | 53.33% | 56.67% | 61.11% | **64.44%** |
| Probability stddev | 0.02038 | 0.02044 | 0.03881 | **0.09417** |
| Median selected P | 52.07% | 51.28% | 52.79% | **55.97%** |
| P75 selected P | 53.32% | 52.15% | 54.37% | **60.60%** |
| Max selected P | 59.96% | 57.40% | 62.28% | **81.55%** |
| ECE | 15.45% | 15.44% | **8.50%** | 9.75% |
| Max bin gap | 39.97% | 37.33% | **15.05%** | 17.27% |
| ROC-AUC | **0.66452** | **0.66452** | 0.61603 | 0.65116 |

The stack materially improves Brier, log loss, selected-side hit rate, probability resolution, ECE, and max-bin error versus the incumbent. However its ROC-AUC is lower than the incumbent on the held-out later block.

Calibration regression on the same test block reinforces the compression diagnosis:

- incumbent calibration intercept: ~**-0.523**
- incumbent calibration slope: ~**6.998**
- stack calibration intercept: ~**+0.0118**
- stack calibration slope: ~**1.353**

The stack fixes most of the observed calibration compression, but **discrimination does not improve**. Under the explicit acceptance contract this is sufficient to block promotion.

Exact promotion blocker: `DISCRIMINATION_NOT_IMPROVED`.

## 5. Full 74-feature paired classifier path closed safely

A detached paired classifier was initialized from the exact forward-native Run38 HOME/AWAY levels:

- trainer id: `7bc5738e-ea29-4442-8f1b-75facbe36b4a`
- 74 features = 37 HOME levels + 37 AWAY levels (excluding constant `is_home`)
- training games: 3,342
- training hash: `fae3b4e389357f04344d2d051889c85a9c5314b6751deb0feb14018c49ac3039`
- `can_execute=false`

The existing `wow_mlb_v2a_trainer_step` path exceeded the validation gateway even for one training step (connector 502). State verification showed iteration 0 after each failed attempt, so there was no partial model update. The trainer was explicitly moved from `INITIALIZED` to `STOPPED`, iteration 0.

Disposition for this sub-path: **BLOCKED_WITH_EXACT_REASON — validation trainer-step execution cost/gateway failure; no model-performance conclusion**.

## 6. Governance conclusion

No champion replacement is earned.

The two-logit stack is the strongest probability-quality challenger observed in this cycle, but the required discrimination improvement is absent. The Sep 16–23 block has now been inspected and must be treated as selection evidence for subsequent variants, not as a fresh certification set.

Next justified Class C hypothesis: a ranking-aware residual/pairwise challenger that targets the incumbent's ordering errors while preserving the demonstrated calibration gains. It requires a new later forward holdout before promotion can be considered.

Unchanged:

- frozen V17 MLB champion
- production probability rows
- publication/rank eligibility
- 4-point lower-bound-gap governor
- `V17_TERMINAL_REDUCER`
- typed failure semantics
- `can_execute=false`
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`
