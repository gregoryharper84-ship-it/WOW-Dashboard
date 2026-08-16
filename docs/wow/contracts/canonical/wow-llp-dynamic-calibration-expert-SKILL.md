# Skill: wow.llp-dynamic-calibration-expert

## Purpose

Convert an independent raw win probability into a candidate-specific calibrated probability distribution and numerical lower bound. Replace universal fixed haircuts with evidence-based uncertainty.

## Governance

```text
lane_status=PROBABILITY_CALIBRATION
can_execute=false
```

## Required Inputs

```text
sport
market_type
raw_model_probability
independent_model_weight
market_prior_weight
historical_model_predictions
historical_outcomes
sample_size
lineup_or_starter_status
injury_status
source_conflicts
market_disagreement
model_timestamp
price_timestamp
```

## Calibration Methods

Preferred order:

```text
1. out_of_sample_isotonic
2. out_of_sample_logistic_or_Platt
3. hierarchical_Bayesian_shrinkage
4. sport_market_bucket_reliability_curve
5. conservative_empirical_error_band
```

A fixed percentage haircut may be displayed only as a sensitivity scenario, never as the sole calibration method for qualification.

## Required Components

```text
base_calibration_error
sport_volatility_penalty
sample_size_penalty
lineup_or_starter_uncertainty
injury_uncertainty
source_conflict_penalty
market_disagreement_penalty
freshness_penalty
```

## Required Outputs

```text
calibration_method
calibration_sample_size
raw_model_probability
calibrated_point_probability
calibrated_probability_lower_bound
calibrated_probability_upper_bound
confidence_interval_level
uncertainty_total
uncertainty_drivers
calibration_status
```

## Hard Rules

```text
no historical calibration evidence => UNCALIBRATED_MODEL
material starter/goalie/QB unresolved => highest tier prohibited
source conflict unresolved => lower bound widened or row rejected
market prior weight > 50% => MARKET_DEPENDENT_MODEL
```

## Output

| Raw | Calibrated Point | Lower Bound | Upper Bound | Method | Sample | Main Uncertainty |
|---:|---:|---:|---:|---|---:|---|

```text
calibration_status=
highest_allowed_tier=
can_execute=false
```
