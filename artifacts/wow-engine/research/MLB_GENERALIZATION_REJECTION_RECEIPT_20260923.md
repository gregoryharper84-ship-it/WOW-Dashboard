# MLB probability-quality generalization / rejection receipt — 2026-09-23

Status: **EXPERIMENT_CREATED / NO_CHAMPION_REPLACEMENT**

Permanent governance invariants remain unchanged:

- `custom_gpt_identity=WOW_BETTING_ENGINE`
- `runtime_generation=V17_ACTIVE`
- `terminal_authority=V17_TERMINAL_REDUCER`
- `can_execute=false`
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`

This receipt exists to prevent selective promotion of a challenger from a favorable slice. The incumbent remains frozen because no tested challenger improves every required out-of-sample dimension.

## 1. 2024 chronological result that initially looked promising

On the Sep 16–30, 2024 holdout, iteration-20 run scoring plus a centered logit-slope calibration improved the incumbent on several metrics:

- mean absolute fitted run difference: ~0.237 -> ~0.334
- Brier: ~0.24746 -> ~0.24310
- log loss: ~0.68803 -> ~0.67879
- selected-side hit rate: ~54.59% -> ~56.76%
- ECE: ~9.69% -> ~8.81%
- max calibration-bin gap: ~21.24% -> ~15.95%
- calibration intercept: ~-0.395 -> ~-0.024
- calibration slope: ~3.573 -> ~1.116

However ROC-AUC slipped slightly (~0.58006 -> ~0.57889). Therefore this challenger did **not** satisfy the required discrimination-improvement criterion.

The 2024 Sep 16–30 block has since been inspected repeatedly during challenger development and is now treated as selection evidence, not untouched certification evidence.

## 2. Narrow iteration sweep (11–14)

Same learning rate/ridge; only training iteration changed. Each state remained research-only and `can_execute=false`.

Centered calibration was fit before the Sep 16–30, 2024 evaluation window.

| State | Brier | Log loss | Hit rate | ECE | Max bin gap | ROC-AUC |
|---|---:|---:|---:|---:|---:|---:|
| Iter 11 | 0.243250 | 0.679124 | 55.68% | 8.28% | 24.30% | 0.58018 |
| Iter 12 | 0.243222 | 0.679062 | 56.22% | 8.28% | 19.03% | 0.57959 |
| Iter 13 | 0.243198 | 0.679007 | 56.22% | 9.36% | 24.27% | 0.57901 |
| Iter 14 | 0.243176 | 0.678958 | 56.22% | 9.37% | 24.26% | 0.57878 |

Iteration 11 produced only a microscopic AUC increase and worsened the worst-bin calibration gap. Iteration 12 improved bin calibration but lost AUC. No state clears the full acceptance bar.

## 3. Feature-ablation rejection

A research-only iteration-20 state zeroed only five coefficients previously observed to reverse train-to-holdout sign. It did not earn adoption:

- Brier: ~0.24274
- log loss: ~0.67805
- hit rate: ~55.68%
- ROC-AUC: ~0.57983

It improved proper scores but still failed to beat incumbent discrimination. The ablation is rejected.

## 4. 2025 later-season generalization check

A later 2025 window (Sep 16–Nov 1, 224 games) was used to test whether the apparent 2024 de-compression generalized.

### Incumbent

- Brier: 0.242067
- log loss: 0.677187
- hit rate: 59.82%
- ECE: 6.19%
- max bin gap: 10.23%
- ROC-AUC: 0.61635

### Iteration-20 with the 2024-fitted centered slope

The 2024 slope did **not** transport:

- Brier: 0.246184
- log loss: 0.690263
- hit rate: 59.38%
- ECE: 7.45%
- max bin gap: 16.34%
- ROC-AUC: 0.62026

Ranking improved, but probability quality degraded materially. A permanently hard-coded de-compression slope is rejected.

## 5. Same-season rolling calibration

Centered logit slopes were re-fit only on Aug 10–Sep 15, 2025, then evaluated on Sep 16–Nov 1.

The fitted slopes changed dramatically versus 2024:

- iter 10: ~0.985
- iter 11: ~0.937
- iter 12: ~0.897
- iter 20: ~0.735

This is direct evidence of temporal/season calibration drift. The 2024 model appeared strongly underconfident; the 2025 calibration window did not support the same de-compression.

Iteration-20 rolling-centered result:

- Brier: 0.241962 (slightly better than incumbent)
- log loss: 0.676990 (slightly better)
- hit rate: 59.38% (worse)
- ECE: 6.61% (worse)
- max bin gap: 13.41% (worse)
- ROC-AUC: 0.62026 (better)

Again: no full acceptance.

## 6. Same-season full Platt

A full affine Platt calibrator was fit on the pre-test 2025 calibration window for the stronger iteration-20 raw ranking.

Late-window result:

- Brier: 0.242081
- log loss: 0.677212
- hit rate: 59.38%
- ECE: 6.46%
- max bin gap: 16.53%
- ROC-AUC: 0.62026

It retains ranking improvement but still fails the proper-score / calibration / hit-rate acceptance package versus incumbent. Not promotable.

## 7. Direct-win logistic specialist rejected as standalone

The existing research-only direct MLB win logistic specialist was trained through Aug 9, 2025 and calibrated on Aug 10–Sep 5. Its supported iteration-30 + Platt path was evaluated on its locked Sep 6–Nov 1 test window (358 games):

- Brier: 0.244524
- log loss: 0.682170
- hit rate: 58.94%
- ECE: 6.55%
- max bin gap: 14.36%
- ROC-AUC: 0.58603

On the same Sep 6–Nov 1 window the run-model incumbent was:

- Brier: 0.242465
- log loss: 0.677999
- hit rate: 59.22%
- ECE: 4.92%
- max bin gap: 15.26%
- ROC-AUC: 0.60223

The direct-win specialist is not a superior standalone replacement.

A stale/parallel `wow_mlb_v2a_logistic_state` table also exists, while the supported training/calibration functions use `wow_mlb_v2a_trainer_state`. The FK correctly prevented attaching a calibrator across those state families. Do not bypass that constraint.

## 8. Stacked-specialist experiment rejected

A single research stacked probability was tested using:

`P_stack = w * P_run_iter20 + (1-w) * P_direct_logistic`

The blend weight was selected only on Aug 10–Sep 5, 2025; the best 0.05-grid value was `w=0.55`.

Locked Sep 6–Nov 1 result:

- Brier: 0.242537
- log loss: 0.678182
- hit rate: 59.22%
- ECE: 6.21%
- max bin gap: 15.96%
- ROC-AUC: 0.60120

It does not beat the incumbent and is rejected. No ensemble complexity should be added from this experiment.

## 9. Confirmed governance defect: legacy Calibration Health PASS

The production-shaped `wow_mlb_v2d_calibration_health` schema contains lifecycle/provenance counts but no fields for:

- Brier
- log loss
- ECE
- calibration intercept
- calibration slope

The current assessor can therefore emit `PASS` after lifecycle/provenance completion without measuring calibration quality. This is a governance defect independent of whether any challenger is eventually promoted.

The safe repair direction is a parallel/fail-closed Calibration Health V2 surface that cannot emit a quantitative PASS until the metrics and governed policy are actually evaluated. Do not weaken publication or model thresholds to compensate.

## Terminal disposition

- Threshold-only repair: **REJECTED**
- Blind train-to-convergence: **REJECTED**
- Forced non-degenerate shared regimes: **REJECTED**
- Increased ridge alone: **REJECTED**
- Sign-reversal feature ablation: **REJECTED**
- Hard-coded 2024 centered slope: **REJECTED**
- Same-season full Platt as champion replacement: **REJECTED**
- Direct-win specialist as standalone: **REJECTED**
- Simple stacked specialist: **REJECTED**
- Current champion replacement: **NOT EARNED**
- Calibration-health governance repair: **CONFIRMED / CONTINUE**
- PR #778: **PR_CREATED / EXPERIMENT_CREATED**
