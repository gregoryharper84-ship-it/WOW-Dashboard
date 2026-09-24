# MLB Multiseason Run-Regime Challenger Rejection Receipt — 2026-09-23

Related: #777 / PR #778

Status: **EXPERIMENT_CREATED / REJECTED_FOR_PROMOTION**

This Class C experiment tested whether one globally comparable multiseason fitted run specialist could resolve the season-dependent calibration behavior seen in earlier challengers. No production model, registry, calibration policy, threshold, terminal authority, or execution capability changed. `can_execute=false` throughout.

## Defect contract

Expected: one fitted multiseason specialist should improve proper scores, calibration, discrimination, and selected-side performance on an independent later-season holdout before any 2026 forward screen.

Observed: pooled fitting modestly improves some ranking/classification measures, but loses proper scoring and calibration to the frozen incumbent. Adding chronology-safe league-regime covariates does not repair the deficit and slightly worsens the pooled baseline.

## Training design

Both challengers use the exact frozen Run38 feature contract across 2024 and 2025. Contract equality was verified before fitting.

Training rows:
- 2024 through Aug 9: 3,330 side observations / 1,665 games
- 2025 through Aug 9: 3,354 side observations / 1,677 games
- pooled total: **6,684 side observations**

Common fit settings:
- learning rate: 0.005
- ridge: 0.003
- fixed training horizon: 40 steps
- detached research trainers
- `can_execute=false`

### Pooled Run38 baseline

Trainer: `0815243b-e9ec-4a91-b170-e4b1c7d8684d`

Training SHA-256: `274b91cd0739056046e04a157e633c43fdac70d26d8219d5e2b4505b35a3fd4f`

Detached distribution: `3aaf6779-67b7-440a-b8f0-383a0c6044c9`

Distribution family: `MLB_V17_POOLED_2024_2025_RUN38_R1`

### Pooled Run43 regime model

Trainer: `46330350-7c0f-4ef3-932d-5fcb059f4b76`

Training SHA-256: `b1462216befa289e54a5e8025b3799ba29e86e7ea7f72aafd80b23e07c6e07da`

Detached distribution: `bc4e9a08-6009-45d1-b5ef-340fee44f2bf`

Distribution family: `MLB_V17_POOLED_2024_2025_REGIME43_R1`

The five added regime covariates are all available pregame and use only strictly earlier same-season league outcomes:

1. prior league runs per team-game, with neutral 4.4 fallback before 30 prior games;
2. prior league HOME run advantage signed by scoring side, with neutral zero fallback before 30 prior games;
3. season progress;
4. season progress signed by HOME/AWAY side;
5. log prior league-game depth.

No season-ID dummy, future result, sportsbook signal, market probability, or generic LLM estimate enters the model.

The motivating historical signal was real: pre-August prior HOME run advantage averaged about -0.03 runs in 2024 versus +0.20 in 2025, while league scoring environment was much closer across seasons.

## Distribution fit

For fairness, both challenger distributions were fitted from their actual pooled 2024–2025 training residuals using the existing shared-negative-binomial semantics.

Estimated shared multiplier variance was **0** for both challengers. No latent shared-scoring variance was forced.

## 2025 holdout — Aug 10 through Nov 1, n=720

Calibration diagnostics use the Calibration Health V2 equal-count decile contract.

| Metric | Incumbent calibrated | Pooled Run38 | Pooled Run43 |
|---|---:|---:|---:|
| Brier | **0.244517** | 0.245367 | 0.245457 |
| Log loss | **0.682125** | 0.683859 | 0.684041 |
| ROC-AUC | 0.581498 | **0.582646** | 0.582110 |
| Hit rate | 56.39% | **57.50%** | **57.50%** |
| Equal-count ECE | **4.37%** | 4.75% | 5.84% |
| Max equal-count bin gap | 14.06% | **13.81%** | 13.84% |
| Probability stddev | 0.05508 | 0.04895 | 0.04928 |

The pooled baseline gains a small amount of AUC/hit rate but loses proper scoring and ECE. The regime additions do not repair that tradeoff and slightly regress the pooled baseline.

The five standardized regime coefficients after 40 steps were all small (roughly absolute beta <= 0.01), with no evidence of a dominant stable regime term.

## Centered calibration rescue test for Pooled Run38

Because Pooled Run38 showed a small ranking advantage, it received one chronology-fixed monotone calibration test:

- centered slope fit: Aug 10–Sep 15, 2025
- locked evaluation: Sep 16–Nov 1, 2025
- fitted centered slope: **1.305352**
- the map preserves ROC ordering and the 50% decision boundary.

Locked later block, n=224:

| Metric | Incumbent calibrated | Pooled Run38 raw | Pooled Run38 centered |
|---|---:|---:|---:|
| Brier | **0.242067** | 0.243495 | 0.242564 |
| Log loss | **0.677187** | 0.680093 | 0.678234 |
| ROC-AUC | **0.616354** | 0.605983 | 0.605983 |
| Hit rate | **59.82%** | 56.25% | 56.25% |
| Equal-count ECE | **6.19%** | 6.65% | 6.36% |
| Max equal-count bin gap | **10.23%** | 14.91% | 13.50% |

The calibration map improves the pooled model's own proper scoring but still fails the incumbent on every tracked acceptance dimension.

## Disposition

Both trainers were explicitly moved to `STOPPED` at iteration 40. No 2026 forward scoring was performed after the required 2025 holdout gate failed.

Exact blocker: `MULTISEASON_LINEAR_RUN_SPECIALIST_GENERALIZATION_FAILED`.

This result rejects two shortcuts:
- pooling the same linear Run38 architecture across seasons is insufficient;
- adding a small set of linear prior-league regime covariates is insufficient.

The next earned model-family investigation should add genuinely nonlinear or hierarchical structure while retaining one globally comparable probability scale, and must be developed on historical chronological evidence before any reserved future forward certification.

## Governance unchanged

- frozen V17 MLB champion unchanged;
- detached challenger trainers/distributions remain research-only;
- no registry or production probability path changed;
- no calibration/qualification/publication threshold changed;
- no lower-bound governor changed;
- typed V17 failures unchanged;
- `V17_TERMINAL_REDUCER` remains sole terminal authority;
- `can_execute=false`;
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`.
