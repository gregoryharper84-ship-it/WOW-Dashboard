# MLB Prequential Model-Selection Gate Rejection Receipt — 2026-09-23

Related: #777 / PR #778

Status: **EXPERIMENT_CREATED / REJECTED_FOR_PROMOTION**

This Class C research compares the frozen incumbent with the chronology-safe iteration-20 rolling-centered challenger. It does not change production model authority, publication rules, probability thresholds, terminal authority, or execution capability. `can_execute=false` throughout.

## Defect contract

Expected: if regime-aware model selection is viable, a chronology-safe gate should remain fail-closed in a season where the challenger is inferior, while activating in a season where strictly earlier evidence demonstrates a challenger advantage, without damaging global probability comparability, proper scoring, calibration, discrimination, or selected-side hit rate.

Observed: both tested gates remain fail-closed in hostile 2025 evidence. The less conservative cumulative-dominance gate activates in 2026, but mixing differently scaled probability mappings across dates creates a cross-date ranking discontinuity and materially damages ROC-AUC and hit rate.

## Candidate

Challenger: the existing research-only prequential centered-logit mapping over the iteration-20 run specialist. For each game date, its slope is fitted from strictly earlier settled outcomes only. Same-day and future outcomes are excluded. Minimum prior raw sample is 60. The map is monotone and side-preserving.

Incumbent: frozen calibrated V17 MLB champion.

All calibration diagnostics use the Calibration Health V2 equal-count decile contract.

## Gate A — paired one-sided 95% evidence

The first gate switches only when, using strictly earlier challenger-evaluable rows, the one-sided 95% upper bound for candidate-minus-incumbent paired loss is below zero for **both** Brier score and log loss:

`mean(diff) + 1.645 * sd(diff) / sqrt(n) < 0`

with at least 60 prior rows.

### 2026 replay

Eligible cohort: n=283.

- challenger rows activated: **0**
- maximum prior gate sample: 274

The gate therefore reproduces incumbent metrics exactly. It is safe but operationally inert on the available 2026 forward evidence despite the rolling-centered challenger being better in aggregate.

Disposition: **REJECTED_FOR_PROMOTION**.

Exact blocker: `EVIDENCE_GATE_NEVER_ACTIVATES_FORWARD_2026`.

## Gate B — cumulative three-metric dominance

A second fixed rule was tested without lowering a confidence threshold. After at least 60 strictly earlier challenger-evaluable rows, switch only when cumulative earlier evidence simultaneously shows:

1. challenger Brier < incumbent Brier;
2. challenger log loss < incumbent log loss;
3. challenger selected-side hit rate > incumbent hit rate.

### 2025 first-gate replay

Eligible rows: **657**.

- challenger rows activated: **0**
- maximum prior gate sample: 656

The gate correctly remains fail-closed throughout the season where the rolling-centered challenger is inferior on proper scoring and calibration.

### 2026 replay

Eligible rows: **283**.

- challenger rows activated: **57**
- first activation date: **2026-09-13**
- maximum prior gate sample: 274

| Metric | Incumbent calibrated | Rolling centered | Dominance gate |
|---|---:|---:|---:|
| Brier | 0.244827 | **0.239708** | 0.242723 |
| Log loss | 0.682760 | **0.672158** | 0.677997 |
| ROC-AUC | 0.622323 | **0.622574** | **0.599246** |
| Hit rate | 56.18% | **56.89%** | **54.06%** |
| Equal-count ECE | 10.73% | **8.35%** | 8.79% |
| Max equal-count bin gap | 23.75% | **13.67%** | 18.81% |
| Probability stddev | 0.01948 | 0.08465 | 0.04805 |

The gate improves Brier, log loss, ECE, and max-bin calibration relative to the incumbent, but materially damages global discrimination and selected-side hit rate.

## Root cause

The failure is structural rather than a threshold miss. The two models operate on materially different probability scales. A date-level or row-level switch preserves each model's within-model monotonic ordering but creates discontinuities between dates: a probability produced under one mapping is not globally comparable with a probability produced under the other mapping. As a result, the mixed output can have better local proper scores while worse cross-date ranking/AUC and classification performance.

The fix is **not** to loosen/tune the gate after observing 2026. A promotable regime-aware design must produce one globally comparable fitted probability scale, such as a multiseason/hierarchical specialist with temporal/regime structure inside the model rather than post-hoc route switching.

Exact blocker: `TEMPORAL_GATE_ROUTING_DISCRIMINATION_REGRESSION_2026`.

Disposition: **EXPERIMENT_CREATED / REJECTED_FOR_PROMOTION**.

## Governance unchanged

- frozen V17 MLB champion unchanged;
- no specialist/calibrator/gate promoted or registered;
- no production probability path changed;
- no qualification/publication/lower-bound threshold changed;
- no market/implied probability substituted;
- typed V17 failures unchanged;
- `V17_TERMINAL_REDUCER` remains sole terminal authority;
- `can_execute=false`;
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`.
