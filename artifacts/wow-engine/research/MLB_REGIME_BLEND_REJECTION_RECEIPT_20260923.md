# MLB Prequential Season-Regime Blend Rejection Receipt — 2026-09-23

Related: #777 / PR #778

Status: **EXPERIMENT_CREATED / REJECTED_FOR_PROMOTION**

This is Class C research only. The frozen champion, specialist registry, publication rules, calibration policy, terminal authority, and execution capability are unchanged. `can_execute=false` throughout.

## Defect contract

Expected: a multiseason challenger should improve proper scores, quantitative calibration, discrimination, and selected-side hit rate across independent temporal/season evidence before any production review.

Observed: prior chronology-safe calibration and run-contrast challengers improve 2026 materially but fail 2025 replay, establishing a season/regime generalization defect.

Hypothesis: treat the existing independently fitted 2024 and 2025 run specialists as regime anchors and learn one same-season blend weight from strictly earlier settled HOME/AWAY run counts.

## Architecture

Anchor A:
- run trainer `76a28c25-f6f0-49bd-a642-bb6ca6604ac4` (2024 iteration 20)
- distribution `27423ed3-5dc7-4541-8cdf-3e0b3fd0086b`

Anchor B:
- run trainer `c09d6efe-f517-4832-9f83-16209c3e6302` (2025 iteration 40)
- distribution `2b9ef808-8f2f-4f0c-b834-4fbcad6067a1`

Both anchors use the identical frozen 38-feature contract and are research-only/non-publishable.

For each game date, with weight `w` constrained to `[0,1]`:

- `log(home_mu) = (1-w)*log(home_mu_2024) + w*log(home_mu_2025)`
- `log(away_mu) = (1-w)*log(away_mu_2024) + w*log(away_mu_2025)`

The weight is fitted by Poisson likelihood using only strictly earlier settled HOME/AWAY run counts. Same-day and future outcomes are excluded. Minimum prior sample is 60 games. Blended run means are passed through the existing shared-negative-binomial probability reducer; no sportsbook or market probability enters the fit.

The chronology and blend-domain invariants are source-controlled in `v17/mlb_prequential_regime_blend_challenger.py` and regression-tested.

## 2025 first-gate replay

Paired coverage is complete: 720/720 holdout games from Aug 10 through Nov 1, 2025 contain both anchors' fitted run means and actual HOME/AWAY run targets.

Evaluation begins after the 60-game warm-up and contains **657 games**. Observed prequential regime-weight range: **0.000 to 0.91397**; mean weight: **0.50165**.

Calibration diagnostics use the Calibration Health V2 equal-count decile contract.

| Metric | Incumbent calibrated | Iter20 raw | Regime blend |
|---|---:|---:|---:|
| Brier | **0.244369** | 0.245092 | 0.245761 |
| Log loss | **0.681856** | 0.683437 | 0.684714 |
| ROC-AUC | 0.586988 | **0.588021** | 0.585424 |
| Hit rate | 57.38% | **58.30%** | 57.99% |
| ECE | **4.68%** | 8.47% | 7.18% |
| Max equal-count bin gap | **10.90%** | 14.21% | 15.09% |
| Probability stddev | 0.05498 | 0.07433 | 0.05729 |

The regime blend fails the first required cross-season gate: it is worse than the incumbent on Brier, log loss, AUC, hit rate, ECE, and max calibration-bin gap.

Because the first independent replay failed, the experiment is stopped before any 2026 scoring. This avoids using 2026 forward outcomes to tune a hypothesis that already failed generalization.

Exact blocker: `CROSS_SEASON_REGIME_BLEND_GENERALIZATION_FAILED`.

Disposition: **EXPERIMENT_CREATED / REJECTED_FOR_PROMOTION**.

## Governance unchanged

- frozen V17 MLB champion unchanged;
- no trainer/distribution/calibration promoted;
- no production probability path changed;
- no threshold or lower-bound governor changed;
- no market/implied probability substituted;
- `V17_TERMINAL_REDUCER` remains sole terminal authority;
- typed V17 failures unchanged;
- `can_execute=false`;
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`.
