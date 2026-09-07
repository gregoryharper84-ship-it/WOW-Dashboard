# WOW V17 — Kalshi Weather Forecast Intelligence Implementation Packet

**Status:** DESIGN_AND_IMPLEMENTATION_PACKET  
**Date:** 2026-09-06  
**Runtime target:** V17_ACTIVE  
**Host:** WOW_BETTING_ENGINE  
**Terminal authority:** V17_TERMINAL_REDUCER  
**Execution:** `can_execute=false`  
**Safety:** `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`

---

## 1. Objective

Upgrade the existing Kalshi Weather lane from a settlement-first Gaussian weather-audit lane into a station-specific, multi-model, observation-assimilating, continuously calibrated probability system for daily-high temperature contracts.

This packet strengthens the **sporting/weather probability package**. It does not weaken or bypass downstream settlement, price, edge, portfolio, exposure, final-refresh, or terminal-reducer governance.

The target system must answer:

```text
Given the exact Kalshi settlement station, date, observed weather state, forecast-model ensemble, local station behavior, and remaining heating window:

What is the calibrated probability distribution of the final official daily maximum temperature?

What is the exact probability of each Kalshi bracket/threshold on the same settlement basis?
```

The probability engine must remain independent from Kalshi price. Price is consumed only downstream after a valid weather probability package exists.

---

## 2. Existing capabilities to preserve

The existing Kalshi Weather lane already provides important protections and should remain authoritative for these concerns:

- exact Kalshi contract and settlement identity;
- exact official observing station;
- station regression bans;
- official weather-source priority;
- distinction between current temperature and maximum observed so far;
- monotonic daily-maximum logic;
- correct YES/NO bracket interpretation;
- Gaussian bracket probability with continuity correction as a fallback method;
- intraday truncation/reconditioning on the observed maximum;
- full-bracket probability normalization;
- market-open and price-freshness controls;
- settlement audit;
- dry-run-only output.

The V17 upgrade does **not** remove the Gaussian pathway. It demotes it to a governed fallback when a richer calibrated distribution is unavailable.

---

## 3. V17 architecture

```text
KALSHI CONTRACT / SERIES
        |
        v
1. Contract + settlement resolver
        |
        v
2. Station registry + station profile
        |
        +-----------------------------+
        |                             |
        v                             v
3A. Forecast ingestion            3B. Observation ingestion
        |                             |
        +-------------+---------------+
                      |
                      v
4. Feature + regime builder
                      |
                      v
5. Station/horizon ensemble model
                      |
                      v
6. Intraday sequential updater
                      |
                      v
7. Final-high probability distribution
                      |
                      v
8. Exact bracket / threshold projector
                      |
                      v
9. Failure-path / regime mixture audit
                      |
                      v
10. Dynamic calibration + bounds
                      |
                      v
11. Governed weather probability package
                      |
         +------------+------------+
         |                         |
         v                         v
12A. Market/edge audit      12B. Prediction ledger
         |                         |
         v                         v
13. Portfolio governor      Outcome / calibration loop
         |
         v
14. Final refresh
         |
         v
15. V17_TERMINAL_REDUCER
```

Exactly one controlling weather model owns the weather probability. Market, Scout, Research, settlement auditors, and portfolio governors may not substitute a price-derived or narrative probability.

---

## 4. Core services/modules

Recommended backend package:

```text
weather_v17/
  contract_resolver.py
  station_registry.py
  station_profile.py
  forecast_ingestion.py
  observation_ingestion.py
  feature_builder.py
  regime_classifier.py
  ensemble_model.py
  intraday_updater.py
  final_high_distribution.py
  bracket_projector.py
  failure_paths.py
  calibration.py
  probability_package.py
  outcome_reconciler.py
  calibration_health.py
  schemas.py
  reconciliation.py
  tests/
```

Use existing repository primitives when equivalent functionality already exists. Do not duplicate an active settlement resolver, orderbook adapter, calibration service, or immutable ledger merely to create weather-specific copies.

---

## 5. Contract and settlement resolver

### Required input

```text
platform=KALSHI
series
contract_id_or_ticker
contract_title
market_date
city
side
bracket_or_threshold
contract_rules
settlement_source
settlement_operator
rounding_rule
correction_policy
market_timezone
```

### Required output

```text
canonical_contract_id
series
station_id
station_timezone
settlement_date_local
measurement=DAILY_MAX_TEMPERATURE
boundary_operator
integer_bracket_lower
integer_bracket_upper
settlement_source
rounding_rule
correction_policy
identity_status
```

### Hard rules

- No probability scoring before exact settlement identity resolves.
- Nearby airport substitution is prohibited.
- City labels are not station identifiers.
- A station mapping must be versioned and auditable.
- Contract boundary wording must be translated into explicit mathematical operators.
- Date/timezone mismatch blocks the row.

---

## 6. Station registry

Create a canonical station registry. Minimum schema:

```text
station_id
kalshi_series
city_label
station_name
latitude
longitude
elevation_m
timezone
settlement_source_family
observation_source
climate_product_source
observation_cadence_minutes
special_observation_possible
rounding_notes
coastal_flag
marine_layer_sensitive
urban_heat_flag
terrain_class
known_microclimate_notes
active_from
active_to
registry_version
verified_at
verified_by_source
```

Seed the currently verified mappings and preserve existing regression bans.

### Station-profile extension

For each station maintain learned model behavior:

```text
station_id
model_source
forecast_horizon_bucket
season_bucket
local_hour_bucket
weather_regime
sample_size
mean_error
median_error
mae
rmse
p10_error
p25_error
p75_error
p90_error
skew
heavy_tail_indicator
last_calibrated_at
profile_version
```

This profile is the mechanism by which the system learns that a given model source runs hot/cold at a specific station, horizon, season, or weather regime.

---

## 7. Forecast ingestion

The V17 weather engine should support multiple independent forecast families when available rather than treating one point forecast as truth.

### Logical forecast-source classes

```text
OFFICIAL_GRIDPOINT
NATIONAL_BLEND
HIGH_RESOLUTION_RAPID_REFRESH
RAPID_REFRESH
GLOBAL_DETERMINISTIC
GLOBAL_ENSEMBLE
LOCAL_OFFICIAL_FORECAST
APPROVED_SECONDARY_CONTEXT
```

The exact provider/model names are runtime configuration, not hardcoded governance.

### Required forecast snapshot schema

```text
snapshot_id
station_id
source_family
model_name
model_run_time
forecast_valid_date
retrieved_at
forecast_horizon_hours
hourly_temperature[]
hourly_dewpoint[]
hourly_cloud_cover[]
hourly_wind_speed[]
hourly_wind_direction[]
hourly_precip_probability[]
hourly_precip_amount[]
forecast_high_if_supplied
quantiles_if_supplied
ensemble_members_if_supplied
source_quality
freshness_status
```

### Rules

- Preserve source/model run timestamps.
- Never overwrite one model with another under a generic `forecast_high` field.
- Stale model runs remain visible but lose modeling weight according to configured freshness logic.
- Forecast disagreement is a numerical uncertainty input, not a prose warning.
- Missing one optional forecast family does not create a global failure when the controlling model has sufficient certified alternatives.

---

## 8. Observation ingestion

### Required official observation state

```text
station_id
observed_at
current_temperature
maximum_observed_so_far
maximum_time_if_known
dewpoint
wind_speed
wind_direction
cloud_cover_or_ceiling
precipitation_state
observation_type
routine_or_special
source
retrieved_at
quality_flag
correction_flag
```

### Derived intraday fields

```text
minutes_since_last_obs
degrees_from_daily_max_so_far
three_obs_temperature_slope
six_obs_temperature_slope
observed_vs_forecast_residual
observed_vs_ensemble_mean_residual
max_so_far_vs_expected_trajectory
remaining_heating_minutes
remaining_daylight_minutes
cloud_trend
wind_shift_indicator
dewpoint_trend
precip_onset_or_exit_indicator
```

### Hard invariants

```text
final_high >= maximum_observed_so_far
```

No posterior distribution may retain probability mass below the official maximum observed so far.

A corrected official observation must invalidate dependent cached probabilities and trigger a rerun.

---

## 9. Weather-regime classifier

Weather risks that materially alter the final high must enter the numeric package.

Minimum regime vocabulary:

```text
CLEAR_MIXING
CLOUD_LIMITED
PRECIP_LIMITED
FRONTAL_PREPASSAGE
FRONTAL_POSTPASSAGE
CONVECTIVE_OUTFLOW_RISK
MARINE_LAYER_ACTIVE
MARINE_LAYER_ERODING
SEA_BREEZE_INTRUSION
DOWNSLOPE_WARMING
DRY_MIXING_OVERSHOOT
HIGH_WIND_MIXING
STABLE_LOW_VARIANCE
TRANSITIONAL_HIGH_UNCERTAINTY
OTHER
```

The classifier may be deterministic, probabilistic, or model-based. The output must be probabilities when more than one regime is plausible:

```text
regime_probabilities = {
  CLEAR_MIXING: 0.55,
  CLOUD_LIMITED: 0.25,
  CONVECTIVE_OUTFLOW_RISK: 0.20
}
```

Required identity:

```text
sum(regime_probabilities) = 1
```

Narrative-only regime warnings are insufficient when the regime is materially load-bearing.

---

## 10. Station/horizon ensemble model

### Primary target

Model the distribution of:

```text
H = final official settled daily maximum temperature
```

Do not model only `P(bracket)` directly. First model the complete final-high distribution, then project it onto every exact contract.

### Features

At minimum:

```text
station_id
calendar_day_or_season
forecast_horizon
model_forecast_highs
model_hourly_peak_values
ensemble_spread
inter_model_spread
station/model historical bias
station/model historical error quantiles
observed_temperature_path if intraday
observed_maximum_so_far
current_temperature
hour_of_day
remaining_heating_window
dewpoint
cloud_cover
wind_speed
wind_direction
precipitation
forecast regime probabilities
recent model-vs-observation residual
```

### Preferred distribution forms

The production implementation may use one or more of:

```text
quantile regression
quantile gradient boosting
probabilistic gradient boosting
mixture density model
Bayesian hierarchical model
distributional regression
ensemble member calibration
empirical error-kernel mixture
```

The exact algorithm is an implementation choice. The governing requirement is a calibrated predictive distribution, not a particular ML library.

### Hierarchical shrinkage

Thin stations/regimes must shrink toward broader pools:

```text
station + regime + horizon
    -> station + horizon
    -> regional/climate class + horizon
    -> global weather-lane baseline
```

No thin bucket may claim station-specific calibration merely because a point estimate exists.

---

## 11. Gaussian fallback

Retain the existing Gaussian model as a certified fallback:

```text
H ~ Normal(mu, sigma)
```

with continuity correction for integer brackets.

Fallback output must explicitly include:

```text
distribution_method=GAUSSIAN_FALLBACK
mu_source
sigma_source
sigma_calibration_status
fallback_reason
```

A fixed generic sigma may be used only when the station/horizon-specific uncertainty model is unavailable and current governance permits the fallback. It must not masquerade as station-calibrated uncertainty.

---

## 12. Intraday sequential updater

This is the major V17 improvement.

At every new official observation, update the final-high distribution using the realized temperature trajectory.

### State

```text
posterior_at_t = P(H | forecast ensemble, station profile, observations through t, regime state)
```

### Required update effects

1. **Monotonic truncation**

```text
P(H < M_t) = 0
```

where `M_t` is the official maximum observed so far.

2. **Trajectory assimilation**

Observed-vs-forecast residuals alter the center and/or model weights.

3. **Heating-window compression**

Uncertainty generally changes as the remaining heating opportunity decreases; do not apply a universal linear shrink.

4. **Regime transition**

Cloud clearing, front passage, marine-layer erosion, convective outflow, wind shifts, or precipitation onset can change both the mean path and tail risk.

5. **Special observation handling**

A special observation that sets a new maximum immediately updates support.

### Suggested sequential method

A pragmatic first production version can use Bayesian model averaging / likelihood reweighting:

```text
weight_i(t)
∝ prior_weight_i
  × likelihood(observed trajectory | model_i, station_error_profile)
```

Then construct the posterior final-high distribution from the reweighted forecast/error mixture, truncate on `H >= M_t`, and recalibrate.

A later version can replace this with a trained state-space or sequence model without changing the surrounding contract.

---

## 13. Final-high discrete probability mass function

For exact Kalshi bins, publish a discrete integer PMF whenever possible:

```text
P(H = 70)
P(H = 71)
P(H = 72)
...
```

Required checks:

```text
0 <= p_h <= 1
sum_h p_h = 1 within numeric tolerance
no support below observed maximum
```

If a continuous distribution is used internally, convert it to integer settlement probabilities using the exact Kalshi rounding/measurement contract.

---

## 14. Exact bracket projector

For inclusive bracket `[L,U]`:

```text
P_yes = sum_{h=L..U} P(H=h)
P_no  = 1 - P_yes - P(void_or_cancel_if_applicable)
```

Threshold markets use the exact boundary operator.

### Position-state logic

Preserve existing states, including:

```text
LIVE_INSIDE_BRACKET
LIVE_BELOW_BRACKET
YES_ELIMINATED_HIGH_EXCEEDED
NO_CURRENTLY_LOSING_BUT_REVERSIBLE
NO_HIGH_SIDE_WIN_LOCKED
YES_CURRENTLY_WINNING_NOT_FINAL
LOW_SIDE_ONLY_REMAINS
SETTLEMENT_PENDING
SETTLED_WIN
SETTLED_LOSS
DATA_UNOBTAINABLE
```

If `M_t > U`:

```text
P(YES bracket)=0
P(NO bracket)=1
```

subject only to explicit void/cancel/correction states in the settlement contract.

---

## 15. Failure-path mixture

Weather failure paths must numerically enter the distribution when material.

Required conceptual identity:

```text
P(H) = Σ_r P(regime_r) × P(H | regime_r)
```

Candidate-level probability:

```text
P(candidate) = Σ_r P(regime_r) × P(candidate | regime_r)
```

Possible failure regimes:

```text
UNEXPECTED_CLOUD_PERSISTENCE
UNEXPECTED_CLOUD_CLEARING
FRONT_EARLY
FRONT_LATE
CONVECTIVE_OUTFLOW
SEA_BREEZE_EARLY
SEA_BREEZE_LATE
MARINE_LAYER_FAILS_TO_ERODE
MARINE_LAYER_ERODES_EARLY
DOWNSLOPE_OVERSHOOT
OBSERVATION_GAP
MODEL_CLUSTER_COMMON_MODE_ERROR
STATION_CORRECTION_RISK
```

Required outputs:

```text
failure_regimes
probability_each_regime
candidate_probability_each_regime
unconditional_candidate_probability
largest_failure_path
largest_failure_contribution
failure_path_score
```

Do not double-penalize a regime already numerically incorporated by applying a second narrative haircut for the same cause.

---

## 16. Dynamic calibration

Use candidate-specific calibration. Universal fixed haircuts are prohibited.

### Weather-specific calibration dimensions

```text
station_id
forecast_horizon_bucket
intraday_or_premarket
season_bucket
regime_class
distribution_method
model_version
sample_size
effective_sample_size
forecast_disagreement
observation_freshness
regime_uncertainty
station_profile_quality
```

### Required outputs

```text
raw_probability
calibrated_probability
calibrated_lower_bound
calibrated_upper_bound
confidence_level
calibration_method
calibration_bucket
calibration_sample_size
uncertainty_components
model_timestamp
```

### Calibration methods

Permitted methods include:

```text
isotonic
Platt/logistic
beta calibration
hierarchical logistic calibration
quantile calibration
conformal or empirical interval calibration
```

Method selection must be validated out of sample.

---

## 17. Calibration health

Maintain weather-specific health metrics by model version and useful cohorts.

Required metrics:

```text
Brier score
log loss
expected calibration error
calibration bias
lower-bound reliability
coverage of predictive intervals
mean absolute final-high error
exact-bin accuracy
within-1F accuracy
within-2F accuracy
```

Market-derived CLV may be recorded downstream, but it must not become the weather ground truth.

### Health states

Recommended states:

```text
HEALTHY
WATCH
DEGRADED
INSUFFICIENT_SAMPLE
BLOCKED
```

Exact production thresholds must be calibrated from historical/replay evidence and approved in configuration. This packet deliberately does not hardcode arbitrary certification thresholds.

---

## 18. Learning loop

The system must learn from immutable pre-event predictions and official outcomes without rewriting history.

### Pregame/intraday prediction record

```text
prediction_id
research_run_id
contract_id
station_id
settlement_date
as_of
model_version
feature_version
station_profile_version
forecast_snapshot_ids
observation_snapshot_ids
regime_probabilities
raw_distribution
raw_probability
calibrated_probability
lower_bound
upper_bound
failure_path_score
terminal_label
source_timestamps
created_at
```

### Outcome record

```text
prediction_id
official_final_high
official_result
settlement_source
settlement_timestamp
closing_market_probability_if_available
observed_path
process_classification
```

### Training rule

Do not allow silent online self-modification.

Use versioned retraining/recalibration:

```text
settled outcomes
 -> offline feature build
 -> backtest / rolling validation
 -> calibration review
 -> regression suite
 -> candidate model version
 -> shadow deployment
 -> promotion decision
```

Every promoted model must be reproducible from a versioned dataset/query, feature definition, training configuration, and code commit.

---

## 19. Temporal validation

Weather is time-series data. Random train/test splitting across adjacent forecast dates can leak station/model behavior.

Use rolling or expanding-window validation, for example:

```text
train: historical dates before T
validate: subsequent block
advance T
repeat
```

Backtests should be segmented by:

```text
station
forecast horizon
premarket vs intraday
season
regime
model version
bracket width/type
```

Report aggregate metrics and cohort metrics. Do not hide a failing station behind a strong global average.

---

## 20. Forecast-source disagreement

Compute explicit disagreement features:

```text
range_of_model_highs
std_of_model_highs
IQR_of_model_highs
max_pairwise_difference
ensemble_spread
forecast_trend_between_model_runs
```

Large disagreement must widen the predictive distribution or alter mixture weights through the fitted model/calibration layer.

A prose label such as `MODELS_DISAGREE` is not sufficient if disagreement is materially predictive of error.

---

## 21. Common-mode error protection

Multiple forecast models are not necessarily independent.

Add a source-family graph so related model products do not receive artificial confidence simply because several correlated products agree.

Required fields:

```text
source_family
parent_model_family
shared_initialization_family
shared_postprocessing_family
independence_group
```

Ensemble weighting should account for redundant information. Five closely related forecasts must not automatically count as five independent votes.

---

## 22. Premarket vs intraday modes

### Premarket

Primary evidence:

```text
multi-model forecast distribution
station/horizon historical error
season/regime
forecast disagreement
```

### Early intraday

Add:

```text
morning observation residuals
cloud/dew/wind evolution
heating trajectory
```

### Late intraday

Shift weighting toward:

```text
official maximum observed so far
current temperature trajectory
remaining heating window
observed regime realization
short-range model updates
```

The exact weighting transition must be learned/configured, not a narrative rule.

---

## 23. Data freshness contract

Every material input must carry:

```text
source
source_timestamp
retrieved_at
age_minutes
freshness_status
```

Define configuration-driven TTLs by data class:

```text
official_observation_ttl
forecast_model_ttl
station_registry_ttl
contract_rules_ttl
market_price_ttl
```

The market-price freshness gate remains downstream and separate from weather probability freshness.

A stale price must not erase a completed weather probability package.

---

## 24. Weather probability package

The controlling weather specialist should return at minimum:

```json
{
  "model_family": "KALSHI_WEATHER_V17",
  "model_version": "...",
  "contract_id": "...",
  "station_id": "...",
  "settlement_date": "...",
  "as_of": "...",
  "mode": "PREMARKET|INTRADAY",
  "distribution_method": "...",
  "observed_maximum_so_far": 0.0,
  "forecast_mean": 0.0,
  "forecast_median": 0.0,
  "forecast_p10": 0.0,
  "forecast_p25": 0.0,
  "forecast_p75": 0.0,
  "forecast_p90": 0.0,
  "integer_pmf": {},
  "regime_probabilities": {},
  "raw_probability": 0.0,
  "calibrated_probability": 0.0,
  "calibrated_lower_bound": 0.0,
  "calibrated_upper_bound": 0.0,
  "failure_path_score": 0.0,
  "largest_failure_path": "...",
  "calibration_method": "...",
  "calibration_sample_size": 0,
  "source_freshness": {},
  "model_timestamp": "...",
  "probability_status": "PASS|HOLD|BLOCKED",
  "blockers": []
}
```

No Kalshi price, payout, market implied probability, or edge is an input to the core weather probability unless an explicitly governed model-prior feature is later certified. The default is **no price leakage**.

---

## 25. Market and edge handoff

After a valid weather probability package exists, hand off to the existing exact-line/payout/fee/orderbook audit.

Required separation:

```text
WEATHER_PROBABILITY_STATUS
MARKET_PRICE_STATUS
SETTLEMENT_STATUS
EDGE_STATUS
PORTFOLIO_STATUS
TERMINAL_STATUS
```

Allowed pattern:

```text
WEATHER_PROBABILITY_STATUS=PASS
MARKET_PRICE_STATUS=DATA_UNOBTAINABLE
EDGE_STATUS=DATA_UNOBTAINABLE
```

This must not be rewritten as `MODEL_UNAVAILABLE` when the weather model successfully produced a valid package.

---

## 26. Portfolio handoff

After individual market qualification, call the active Kalshi portfolio governor.

Weather probability must not be reduced merely because two markets share a weather thesis. Shared-system or same-city exposure is a portfolio/dependence problem.

Preserve:

```text
model_probability
calibrated_probability
calibrated_lower_bound
```

and apply concentration/duplicate-thesis governance separately.

Any active Recovery/Watch/HARD_STOP state comes from current portfolio-state evidence and the active governance layer, not from this forecasting model.

---

## 27. Final refresh

Immediately before user-facing publication, recheck at minimum:

```text
governance hash
current time / timezone
contract status
settlement identity
official maximum observed so far
latest observation timestamp
latest material forecast update
latest regime-changing weather evidence
market-open status when market output requested
exact executable price + timestamp when market output requested
source conflicts
```

Any material weather update after scoring requires a probability rerun, not a prose amendment.

---

## 28. Reconciliation

Every weather run must reconcile all rows and data subobjects.

### Candidate reconciliation

```text
rows_in = rows_scored + rows_blocked + rows_removed
```

### Forecast-source reconciliation

```text
forecast_sources_requested
= retrieved
+ stale
+ unavailable
+ not_applicable
```

### Observation reconciliation

```text
observations_retrieved
= routine_observations
+ special_observations
+ corrected_observations
```

### PMF reconciliation

```text
sum(integer_pmf.values()) = 1 within numeric tolerance
```

### Regime reconciliation

```text
sum(regime_probabilities.values()) = 1 within numeric tolerance
```

Any reconciliation failure invalidates the dependent probability package.

---

## 29. Storage tables

Recommended logical tables; reuse existing generic ledgers where possible.

### `weather_station_registry`

Canonical station and Kalshi mapping.

### `weather_station_model_profile`

Station/model/horizon/regime error distributions.

### `weather_forecast_snapshot`

Immutable forecast-model inputs.

### `weather_observation_snapshot`

Immutable official observations.

### `weather_model_run`

Feature/model/calibration versions and distribution output.

### `weather_prediction`

Immutable contract-level pre-settlement probability.

### `weather_outcome`

Official settlement and realized high.

### `weather_calibration_bucket`

Historical calibration diagnostics and current health state.

### `weather_model_registry`

Model versions, training ranges, feature versions, promotion status.

---

## 30. Internal interfaces

Prefer keeping the public Custom GPT action surface small. Screenshot/PDF/pasted-board workflows should continue to route through the canonical governed pick-request action when that is the active V17 contract.

Internally expose functions/services equivalent to:

```text
resolve_weather_contract(...)
get_station_profile(...)
get_forecast_bundle(...)
get_official_observation_state(...)
classify_weather_regimes(...)
score_final_high_distribution(...)
update_intraday_distribution(...)
project_contract_probability(...)
calibrate_weather_probability(...)
write_weather_prediction(...)
reconcile_weather_outcome(...)
get_weather_calibration_health(...)
```

Do not add public endpoints merely because these internal module boundaries exist.

---

## 31. Testing matrix

### A. Contract/station regression

1. NYC series resolves to KNYC/Central Park when defined by active Kalshi rules.
2. LAX resolves to KLAX, not BUR.
3. Miami resolves to KMIA, not PBI.
4. Chicago resolves to KMDW, not KORD.
5. Unsupported city requires rule-based station verification.
6. Wrong settlement date is blocked.
7. Boundary operator mismatch is blocked.

### B. Monotonic maximum

8. Posterior mass below observed maximum equals zero.
9. YES bracket above-exceeded returns zero probability subject to correction/void state.
10. NO bracket high-side escape is preserved while max is inside the excluded bracket.
11. NO high-side lock occurs after official max exceeds upper bracket bound.

### C. Distribution

12. Integer PMF sums to one.
13. Every probability lies in `[0,1]`.
14. Full bracket set normalizes.
15. Continuous-to-discrete conversion respects exact rounding/settlement rules.
16. Gaussian fallback matches continuity-correction fixtures.

### D. Ensemble

17. Duplicate/correlated forecast sources do not create artificial confidence.
18. Large inter-model disagreement widens/changes the fitted distribution according to trained/configured logic.
19. Station-specific bias changes predictions only when supported by a valid profile.
20. Thin station buckets shrink hierarchically.

### E. Intraday

21. A hotter-than-forecast observation trajectory updates posterior upward when supported by the model.
22. A cloud-limited trajectory updates posterior appropriately.
23. A new official maximum immediately truncates lower outcomes.
24. A special observation is accepted and timestamped.
25. A corrected observation invalidates dependent cached runs.
26. Late-day remaining-heating features are used.

### F. Failure paths

27. Material marine-layer uncertainty enters regime mixture numerically.
28. Convective-outflow risk enters the distribution numerically.
29. No second narrative haircut duplicates a regime already included in the model.

### G. Calibration

30. Raw point probability is not mislabeled as lower bound.
31. Thin calibration sample widens uncertainty.
32. Calibration health is segmented by station/horizon when sample permits.
33. Rolling validation is used; random leakage regression test fails if enabled.
34. Versioned calibrator reproduces historical result.

### H. Market separation

35. Kalshi price is absent from core weather-model features by default.
36. Stale orderbook blocks price/edge but preserves valid weather probability.
37. Missing fee data blocks downstream net edge but not weather probability.
38. Screenshot price cannot become executable price.

### I. Governance

39. Portfolio rejection does not mutate weather model probability.
40. Final refresh forces rerun after material weather change.
41. No specialist can override V17_TERMINAL_REDUCER.
42. `can_execute=false` always.

---

## 32. Backtest and forward-test program

### Baseline

Use the current certified Gaussian pathway as the baseline comparator where available.

### Challenger

V17 station/horizon ensemble + sequential intraday updater.

### Compare

```text
final-high MAE
Brier score by contract
log loss
ECE
calibration bias
lower-bound reliability
interval coverage
exact-bin accuracy
within-1F accuracy
within-2F accuracy
```

### Cohorts

```text
station
forecast horizon
premarket vs intraday
season
weather regime
bracket width
model version
```

A challenger should not be promoted solely on one aggregate accuracy number. Review calibration, tail behavior, station regressions, and lower-bound reliability.

---

## 33. Rollout stages

### Stage W0 — Inventory and station truth

- verify all supported Kalshi weather series;
- build versioned station registry;
- map active settlement source/rules;
- add regression tests.

### Stage W1 — Immutable data capture

- store forecast snapshots;
- store official observations;
- store final official highs;
- create source freshness/reconciliation.

### Stage W2 — Baseline replay

- replay Gaussian fallback on historical dates;
- establish Brier/log-loss/MAE/calibration baseline.

### Stage W3 — Multi-model challenger

- build station/horizon error profiles;
- train ensemble/distribution model;
- run rolling validation;
- publish shadow probabilities only.

### Stage W4 — Intraday updater

- assimilate station observations sequentially;
- implement monotonic truncation;
- build regime transition inputs;
- replay historical intraday sequences where data exists.

### Stage W5 — Dynamic calibration

- fit station/horizon-aware calibrator;
- add hierarchical shrinkage;
- expose lower/upper bounds and calibration health.

### Stage W6 — Governed probability integration

- integrate with V17 Full Model/Gatekeeper path;
- preserve probability/market separation;
- add immutable prediction write;
- add final refresh rerun trigger.

### Stage W7 — Downstream market integration

- send completed probability package to exact-line/orderbook/fee audit;
- preserve row-level market blockers separately;
- route final set through portfolio governor and terminal reducer.

### Stage W8 — Forward-test review

- accumulate settled immutable predictions;
- review calibration and failure paths;
- compare baseline vs challenger by cohort;
- promote only model versions that pass approved regression and calibration review.

---

## 34. Definition of done

The V17 Weather Forecast Intelligence layer is complete when:

```text
1. exact Kalshi settlement station and rules are versioned and regression-tested;
2. multiple forecast sources can be ingested with provenance and timestamps;
3. official observations are ingested and maximum-so-far is maintained;
4. final-high distribution is modeled before contract projection;
5. intraday posterior contains zero mass below observed maximum;
6. weather regimes that materially matter enter the numeric distribution;
7. station/horizon forecast-error profiles are learned from settled data;
8. thin cohorts use hierarchical shrinkage;
9. dynamic calibration produces calibrated probability and numerical bounds;
10. raw probability is never relabeled as a lower bound;
11. exact bracket probabilities derive from the full settled-high distribution;
12. Gaussian remains a transparent fallback, not the default when richer certified inference is available;
13. price/edge inputs remain downstream of weather probability;
14. stale/missing market data cannot erase completed weather probability;
15. immutable predictions and official outcomes support Brier/log-loss/ECE/bias/lower-bound reliability review;
16. rolling/temporal validation prevents time leakage;
17. all source/row/PMF/regime reconciliation checks pass;
18. final refresh reruns after a material observation/forecast/regime change;
19. portfolio governance remains independent from sporting/weather probability;
20. V17_TERMINAL_REDUCER remains sole final authority;
21. can_execute=false;
22. DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true.
```

---

## 35. Required implementation return packet

Backend implementation should return:

```text
commit_hash
files_changed
schema_changes
migration_status
station_registry_status
forecast_ingestion_status
observation_ingestion_status
forecast_source_reconciliation_status
observation_reconciliation_status
regime_classifier_status
ensemble_distribution_status
gaussian_fallback_status
intraday_updater_status
monotonic_truncation_status
integer_pmf_status
bracket_projector_status
failure_path_mixture_status
dynamic_calibration_status
calibration_health_status
immutable_prediction_write_status
outcome_reconciliation_status
rolling_backtest_status
baseline_vs_challenger_metrics
market_probability_separation_status
portfolio_handoff_status
final_refresh_status
terminal_reducer_status
focused_test_result
full_regression_result
real_data_replay_examples
can_execute=false
```

At least one real-data replay should show, for a supported city/date:

```text
contract identity
station identity
all forecast-source timestamps
all observations used
observed maximum at each scoring time
regime probabilities
raw final-high distribution
integer PMF
exact bracket probability
failure-path mixture
raw probability
calibrated probability
calibrated lower bound
calibrated upper bound
final official high
Brier/log-loss contribution
process classification
```

Synthetic fixtures may support tests but are not sufficient as the only production verification.

---

## 36. Non-goals

This packet does not:

- authorize live trading;
- prescribe stake sizing;
- remove active drawdown/portfolio governance;
- treat market price as weather truth;
- guarantee profitability;
- replace Kalshi settlement rules with generic weather conventions;
- require a nested Custom GPT;
- require a new public Action endpoint when the existing canonical V17 action can route the lane.

---

## 37. One-line definition

**WOW V17 Kalshi Weather Forecast Intelligence is a station-specific, multi-model, observation-assimilating, regime-aware, dynamically calibrated final-high distribution engine that produces governed exact-contract probabilities while keeping settlement, market price, portfolio risk, and terminal publication as separate downstream contracts.**
