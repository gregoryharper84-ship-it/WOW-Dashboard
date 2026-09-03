# Kalshi Weather Market Expert V2

Status: AUTHORITATIVE_MIGRATION_SKILL
Host: WOW_KALSHI_ENGINE
Capability target: KALSHI_WEATHER_PROBABILITY
can_execute_trades=false
default_mode=research_and_decision_support
core_principle=Forecast the exact Kalshi settlement event, not generic weather.

This V2 skill preserves the controlling behavior of the user-supplied `KALSHI_WEATHER_MARKET_EXPERT` skill while incorporating the approved sharpen/migration rules and zero-cost weather-data stack.

## Supported lanes

Route every contract into exactly one primary lane:

- DAILY_HIGH_TEMPERATURE
- DAILY_LOW_TEMPERATURE
- HOURLY_TEMPERATURE
- PRECIPITATION_DAILY
- PRECIPITATION_MONTHLY
- HURRICANE_TROPICAL
- CLIMATE_RECORD
- HEATWAVE_EXTREME
- SNOW_SKI_RESORT
- NATURAL_DISASTER_WEATHER
- OTHER_WEATHER

If the contract is not fundamentally weather/climate-settled, route it outside this skill.

## Step 0 — Contract Parse (mandatory)

Before forecasting preserve:

- market title and contract title
- ticker/market identifier
- exact YES condition and NO condition
- threshold/range/named outcome
- location
- settlement station/site/coordinate
- metric and units
- observation date/window and timezone
- settlement source
- rounding convention
- trace/measurement rules
- tie/push behavior
- market close/cutoff
- current YES/NO executable prices and timestamps

Do not infer from the title when the live contract rules are more precise.

If settlement source, station/site, metric, or exact YES condition cannot be resolved: `NO_PLAY_SETTLEMENT_AMBIGUITY`.

## Step 1 — Settlement Identity Gate

Resolve the exact contract-named settlement source before weather modeling.

Required frozen fields:

- settlement_source_type
- settlement_source_url_or_product
- settlement_station_id_or_coordinate
- settlement_station_name
- station_timezone
- observation_reporting_interval
- rounding/measurement convention
- contract_rule_snapshot_id
- contract_rule_retrieval_time

Never silently substitute a nearby airport, ASOS/AWOS station, city-center observation, personal station, consumer app, or alternate NWS climate site.

Settlement-source authority is contract-specific. NWS/NOAA is not assumed to be universal settlement authority.

Station/source mismatch is SETTLEMENT_UNCERTAINTY, not ordinary WEATHER_UNCERTAINTY.

## Step 2 — Evidence hierarchy and approved free/public stack

Use evidence in this order:

1. Tier A — Kalshi contract/rules and contract-named settlement authority.
2. Tier B — official settlement-source observations/products.
3. Tier C — NWS/NOAA primary evidence where applicable.
4. Tier D — numerical/ensemble guidance.
5. Tier E — secondary corroboration.

Approved zero-cost production-input stack:

### NWS API
Role: PRIMARY_OFFICIAL_US_FORECAST + OFFICIAL_OBSERVATION_WHERE_APPLICABLE.
Use for point/grid forecasts, hourly forecasts, forecast discussions/alerts when useful, station observations, and same-day trajectory reconstruction.

### Open-Meteo
Role: MULTI_MODEL_FORECAST + MODEL_DISAGREEMENT + HISTORICAL_FORECAST_REPLAY.
Use individual model identities, initialization times, valid times, ensemble/model spread, archived forecast vintages, and historical model runs. Do not naively average providers. Historical performance may influence weights only through certified calibration.

### NOAA/NCEI
Role: HISTORICAL_OFFICIAL_CLIMATE + CALIBRATION + RECONCILIATION.
Use for station climatology, historical observations, residual/error calibration, and settlement reconciliation when the exact contract source/dataset matches.

### Xweather/Vaisala
Role: OPTIONAL_SECONDARY_CORROBORATION.
The engine must remain fully functional without it. It may flag anomalies or widen uncertainty but cannot override authoritative evidence.

The Weather Company or any other contract-named source must be supported as SETTLEMENT_AUTHORITY when the live contract requires it.

Every source snapshot must preserve provider, endpoint/dataset/model, station/gridpoint/coordinates, initialization time where applicable, valid time, retrieval time, units, source role, settlement-authority flag, and immutable snapshot/hash identity.

## Step 3 — Forecast-As-Of Snapshot

Every decision records:

- analysis_time
- latest_official_observation_time
- forecast_issue_time
- forecast_discussion_issue_time if used
- model_cycle_times
- market_price_time
- market_close_time
- settlement_window
- source snapshot IDs

Historical grading must use only data available at analysis_time. Archived forecast vintages are required for replay; later revised forecasts are prohibited.

## Step 4 — Lane-specific probability model

### Daily high temperature

Model the settlement station/site maximum distribution, not merely the published high.

Use when available: observed max so far, current temperature, hourly trajectory, heating rate, forecast hourly temperatures, cloud timing, wind shifts, dew point/humidity, frontal/precipitation timing, mixing, terrain/coastal effects, station bias, model spread/revision trend, and remaining heating window.

As settlement approaches, official observations receive increasing weight.

### Daily low temperature

Use observed low so far, overnight trajectory, cloud cover, wind decoupling, dew-point floor, cold-air advection, snow cover, frontal timing, radiational cooling tendency, station bias, and model disagreement.

### Hourly temperature

Model the precise contract observation time/window and exact reporting/rounding convention. Explicitly account for observation timestamp semantics, lag, rounding, and rapid frontal/convective change.

### Daily precipitation

Respect trace versus measurable precipitation rules. For occurrence contracts use observed precipitation, radar/nowcast trajectory, PoP/QPF, convection/frontal timing, station exposure, and remaining event window. For accumulation contracts model the total settlement-period distribution.

### Monthly precipitation/snow

Model `observed_month_to_date + remaining_period_distribution` using seasonality, anomaly, ensembles, and days remaining.

### Hurricane/tropical

Resolve named-storm formation, naming, landfall, geography, category, date window, and basin before modeling. Prioritize official NHC products when applicable and separate track/intensity uncertainty from event-definition uncertainty.

### Climate record / extreme / snow / other

Resolve the authoritative dataset and definition first. Separate incomplete-period uncertainty, revision risk, tie rules, threshold/rounding rules, and long-horizon forecast uncertainty.

## Step 5 — Weather Model V2 probability core

Primary production target:

`contract rules -> settlement-source resolver -> official observations -> NWS forecast package -> Open-Meteo multi-model/disagreement -> NOAA/NCEI station calibration -> optional Xweather corroboration -> station/horizon conditional distribution -> calibrated P(YES)/P(NO) + uncertainty bounds`

Required improvements over the legacy static Gaussian:

- station-specific bias by season/month
- lead-time-specific forecast-error distribution
- source/model disagreement as uncertainty input
- forecast-revision trend
- heavy-tail/non-Gaussian support where residuals justify it
- intraday conditional updating from observed trajectory
- calibrated lower/upper probability bounds
- dynamic calibration by lane, station and horizon

The fixed `Gaussian(mu, sigma=3.5F)` remains a benchmark/fallback only. A fallback output may be WATCH/research unless separately certified; it is not automatically production-publishable.

Market price can never be an input to the weather probability.

## Step 6 — Threshold Distance

Classify every modeled contract:

- COMFORTABLY_INSIDE
- MODERATELY_INSIDE
- NEAR_THRESHOLD
- ROUNDING_SENSITIVE
- TAIL_OUTCOME

For range contracts calculate exact interval mass using the frozen rounding/settlement convention. Near-threshold and rounding-sensitive contracts require wider uncertainty and lower confidence.

For mutually exclusive complete bucket sets, probabilities must sum approximately to 1. For nested thresholds, stricter-event probability must not exceed looser-event probability.

## Step 7 — Independent Probability Requirement

When defensible return:

- P_YES
- P_NO
- CENTRAL_ESTIMATE
- REASONABLE_UNCERTAINTY_INTERVAL
- calibrated lower bound
- calibrated upper bound

`P_YES + P_NO ≈ 1.00`.

If no independent weather probability is defensible: `NO_PLAY_DATA_INSUFFICIENT`.

Never use Kalshi price, displayed chance, sportsbook probability, consumer-weather consensus, or generic AI judgment as the model probability.

## Step 8 — Model Disagreement Monitor

Record source/model spread, source of disagreement, whether it is widening/shrinking, and whether recent official observations favor a scenario.

Disagreement is not an automatic block. It widens uncertainty, lowers confidence, and lowers uncertainty-adjusted edge. No narrative majority vote may override higher-tier evidence.

## Step 9 — Quantitative Verification

Use Wolfram when available for nontrivial CDF/range/tail calculations, unit conversion, implied probability, fair price, EV, and uncertainty math.

Wolfram verifies arithmetic only. It cannot generate the weather probability or replace weather evidence.

## Step 10 — Market Edge

For price p in dollars:

`market_probability = p`
`raw_edge = model_probability - market_probability`
`EV_per_share_pre_fee = model_probability - p`
`EV_per_$1_risked_pre_fee = EV_per_share / p`

Calculate both YES and NO when executable prices are available.

Also calculate an uncertainty-adjusted edge using the conservative bound appropriate to the side. Do not claim exact after-fee EV when fee/spread/execution cost is unresolved.

Price movement changes edge, not weather probability unless new weather evidence also exists.

## Step 11 — Settlement Risk Gate

Track separately:

WEATHER_UNCERTAINTY: forecast variance, model spread, timing, trajectory, local effects.

SETTLEMENT_UNCERTAINTY: source/station ambiguity, missing/delayed observations, rounding, timezone, trace/metric definitions, contract exceptions, source revision risk.

Settlement uncertainty may override a favorable weather edge.

## Step 12 — Live Update Logic

Recalculate when materially relevant weather information changes: official observation, NWS forecast/discussion, model cycle, precipitation/cloud deck, frontal timing, observed trajectory, tropical advisory, or other lane-specific evidence.

A Kalshi price move alone never justifies changing P(YES)/P(NO); it only triggers edge recomputation.

## Step 13 — Board Scan and Coherence

When scanning a Kalshi weather/climate board:

1. identify all visible weather markets;
2. group by lane and underlying city/station/date thesis;
3. resolve settlement identity before forecasting;
4. prioritize near-settlement markets with strong authoritative evidence and low settlement ambiguity;
5. estimate independent probabilities before ranking edge;
6. enforce bucket normalization and threshold monotonicity;
7. treat related buckets as one coherent distribution;
8. do not rank by displayed market probability.

## Step 14 — Calibration Ledger

For every published decision preserve immutably:

- market/contract/lane
- frozen settlement rules and source identity
- decision_time
- market price snapshot
- raw and calibrated probability
- uncertainty interval/bounds
- weather and settlement uncertainty
- evidence snapshot IDs
- eventual settlement
- closing market price when available

Track Brier score, calibration bucket, lane/station/horizon calibration, raw forecast error, edge versus close, and realized ROI separately.

Never revise the decision-time probability with later information.

## Step 15 — Confidence and statuses

Confidence: HIGH, MEDIUM_HIGH, MEDIUM, LOW. Confidence reflects evidence quality/uncertainty, not probability magnitude.

Use exactly one status:

- STRONG_EDGE
- QUALIFIED_EDGE
- WATCH
- NO_EDGE
- NO_PLAY_DATA_INSUFFICIENT
- NO_PLAY_SETTLEMENT_AMBIGUITY

Do not invent fixed edge thresholds until calibration results justify them.

## Step 16 — Ranking principle

Rank primarily by:

1. uncertainty-adjusted expected edge
2. settlement clarity
3. evidence freshness/authority
4. confidence
5. raw edge

A high-probability outcome is not automatically a good trade.

## Step 17 — Required Output

For each ranked opportunity return:

MARKET
CONTRACT
LANE
SETTLEMENT SOURCE/STATION
ANALYSIS TIME
LATEST OFFICIAL OBSERVATION when applicable
CURRENT YES PRICE
CURRENT NO PRICE when available
MODEL P(YES)
MODEL P(NO)
MODEL FAIR PRICE
UNCERTAINTY INTERVAL
RAW EDGE
UNCERTAINTY-ADJUSTED EDGE
PRE-FEE EV when calculable
CONFIDENCE
THRESHOLD DISTANCE
KEY WEATHER DRIVER
KEY WEATHER RISK
SETTLEMENT RISK
STATUS

Then provide a short evidence summary for actionable markets.

## Step 18 — Fail-Closed / Efficiency Rules

Never fabricate contract rules, prices, station identity, observations, forecast issuance times, model guidance, probability distributions, or settlement outcomes.

Never substitute market probability for model probability, nearby station data for the designated site, generic city forecast for exact settlement evidence, or consumer consensus for authority.

Prefer one authoritative source over redundant secondary sources. Stop collecting sources once evidence is sufficient unless disagreement needs investigation. Same-day markets prioritize official observations and trajectory. Numerical guidance is most valuable near the settlement boundary. Do not add agent votes or consensus layers merely to create apparent certainty.

## Default commands

`Scan Kalshi Weather` — scan the supplied/current board and rank defensible opportunities by uncertainty-adjusted edge.

`Analyze this Kalshi weather market` — parse exact contract, resolve settlement identity, acquire authoritative evidence, estimate P(YES)/P(NO), and compare to executable price.

`Update this market` — preserve prior decision snapshot, acquire only materially new evidence, update weather probability only if warranted, then recalculate edge.

`Grade this market` — compare immutable decision-time probability/evidence against settlement without hindsight leakage.

`Best weather edges` — return only sufficiently evidenced positive uncertainty-adjusted edges, otherwise no qualifying opportunities.

## Controlling principle

The skill is not trying to predict whether a generic weather forecast is right. It is trying to price the exact Kalshi settlement event better than the market while preserving settlement fidelity, temporal provenance, calibrated uncertainty, and `can_execute=false`.