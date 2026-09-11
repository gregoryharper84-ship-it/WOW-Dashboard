# Kalshi Weather V2 — empirical certification thresholds

These thresholds are fixed before the first certification decision so they cannot be tuned after seeing validation results.

## Candidate-fit minimum

For one exact `(settlement_location_code, lane, lead_time_bucket, model_version)` profile:

- at least **30 unique weather-target residuals** are required before a research calibration candidate can be fit;
- threshold-sibling contracts for the same target timestamp/bucket count as one residual sample;
- every forecast timestamp must precede the corresponding settlement timestamp;
- settlement must come from the exact contract-governed source/location.

A 30-sample fit is **not** sufficient for certification.

## Forward-validation minimum

Certification review is not eligible until the profile has:

- at least **50 unique weather-target residuals total**;
- at least **30 chronologically earlier training residuals**;
- at least **20 chronologically later holdout residuals** that were not used to fit bias/sigma.

The holdout window is chronological, never random-shuffled.

## Residual acceptance gates

On the chronological holdout, after applying training-set bias correction:

- absolute holdout mean error must be **<= 0.75°F**;
- holdout MAE must be **<= 2.5°F**;
- empirical one-sigma coverage must be **between 50% and 85%**;
- no look-ahead, source-identity, or settlement-quality blocker may be present.

These are initial conservative engineering gates for an hourly temperature residual model, not a claim that passing them proves perfect probability calibration.

## Probability-level acceptance gate

Before `KALSHI_WEATHER_PROBABILITY` can be promoted to `AVAILABLE`, the candidate profile must also be replayed across the exact threshold predicates that existed in the immutable holdout contracts. Probability-level review must report at minimum:

- Brier score on the chronological holdout;
- calibration by probability bucket;
- central forecast residual metrics;
- comparison against a simple uncalibrated baseline;
- evidence that market price was not used as a model input.

Probability certification requires the governed review to show **no material degradation versus the uncalibrated baseline** and no severe probability-bucket miscalibration. If the evidence is inconclusive, the capability remains unavailable.

## Promotion authority

The cohort collector, fitter, and report generator are not allowed to set:

- `certified=true`;
- `KALSHI_WEATHER_PROBABILITY=AVAILABLE`;
- `probability_publishable=true`;
- `can_execute=true`.

Promotion is a separate V17 governance action after the empirical acceptance report passes. Until then, fail closed.
