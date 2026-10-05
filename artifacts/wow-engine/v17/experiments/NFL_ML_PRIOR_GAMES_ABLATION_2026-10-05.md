# NFL ML Prior-Games Ablation — Step 1 Replay Receipt

Date: 2026-10-05  
Issue: #1342  
Class: C challenger research only  
Production behavior changed: **No**

## Hypothesis

The cumulative `home_prior_games` and `away_prior_games` features are
non-stationary across seasons and materially out of distribution for 2026
inference. Step 1 removes only those two features, then refits the same model
family and Platt calibration on the same chronological split.

This is an ablation experiment, not coefficient surgery and not a production
promotion.

## Corpus reconstruction

The canonical NFL training and team-summary ledgers reconstruct the exact sample
sizes carried by the champion artifact:

- Train: 2021–2023, n=787
- Calibration: 2024, n=285
- Validation: 2025, n=284

The reconstructed incumbent scaler means for the first three features match the
promoted artifact to floating-point precision, including:
- week mean 10.5679796696
- home_prior_games mean 28.2884371029
- away_prior_games mean 28.2973316391

## Current-season OOD evidence

Across 24 distinct 2026 NFL events already scored:
- 24/24 have both cumulative prior-game features beyond |z| > 3;
- observed home prior-game z range is approximately +4.08 to +4.92;
- zeroing only the two incumbent count contributions as a diagnostic changes
  calibrated probability by 11.13 percentage points on average;
- 14/24 shift by at least 10 points;
- 5/24 shift by at least 15 points;
- 7/24 cross the 50% selected-side boundary.

That diagnostic does not establish that zeroing is a valid model. It establishes
material sensitivity and justifies a true refit.

## Step 1 chronological refit

Independent reconstruction of the incumbent and the 29-feature ablation:

| Metric | Incumbent reconstruction | Prior-count ablation | Delta |
|---|---:|---:|---:|
| 2025 Brier | 0.231161 | 0.226150 | -0.005011 |
| 2025 Log loss | 0.652913 | 0.641336 | -0.011577 |
| 2025 AUC | 0.655602 | 0.669458 | +0.013856 |
| 2025 ECE-10 | 0.041981 | 0.057728 | +0.015747 |
| Raw Brier | 0.232756 | 0.227949 | -0.004806 |

The incumbent reconstruction reproduces the promoted artifact's holdout metrics
closely enough to validate the replay path.

The ablation improves Brier, log loss, raw Brier, and AUC. ECE worsens, although
it remains below the incumbent retrospective ceiling of 0.12.

## Paired bootstrap uncertainty

Deterministic paired bootstrap over the 284 validation games (4,000 resamples):

- Brier delta (challenger - incumbent) 95% interval:
  [-0.01271, +0.00216]
- Log-loss delta 95% interval:
  [-0.02872, +0.00402]

Both intervals cross zero. The first replay is promising but not statistically
decisive and cannot justify production replacement.

## Decision

**CONTINUE_CHALLENGER_RESEARCH**

Next controlled stages:
1. run the exact repository Python/scikit-learn harness;
2. compare stationary replacements (season count, capped/log count, sufficiency);
3. evaluate early/mid/late-season and OOD calibration cohorts;
4. collect untouched forward-shadow evidence before any governed promotion review.

The production champion remains unchanged.

`V17_TERMINAL_REDUCER` remains authoritative.  
`can_execute=false`.
