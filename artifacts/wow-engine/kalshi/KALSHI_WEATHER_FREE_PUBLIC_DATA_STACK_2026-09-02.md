# WOW Kalshi Weather — Free/Public Data Stack

Date: 2026-09-02
Status: APPROVED_MIGRATION_INPUT
Host: WOW_KALSHI_ENGINE
can_execute=false

## Decision

Adopt a zero-cost weather-data core for Weather Model V2. These sources are model/evidence inputs only. They never override the exact settlement source named by the live Kalshi contract.

## Approved zero-cost core

### 1. National Weather Service API (`api.weather.gov`)
Role: PRIMARY_OFFICIAL_US_FORECAST + OFFICIAL_OBSERVATION_WHERE_APPLICABLE
Access: public/open U.S. government data; no usage fee; reasonable rate limits.
Use for:
- points/gridpoint metadata
- grid forecasts
- hourly forecasts
- forecast discussions/alerts where useful
- station observations
- observation trajectory and same-day max/min reconstruction

Rules:
- Preserve station/gridpoint identity and timestamps.
- Do not treat a single daily-max field as authoritative when the contract requires another source or when observation-series reconstruction is needed.
- NWS evidence cannot override a contract-named non-NWS settlement authority.

### 2. Open-Meteo
Role: MULTI_MODEL_FORECAST + MODEL_DISAGREEMENT + HISTORICAL_FORECAST_REPLAY
Access: public API; no API key/signup/credit card for the free non-commercial endpoint; current free limit is up to 10,000 API calls/day under its terms.
Use for:
- individual numerical model comparisons
- ECMWF/NOAA/DWD and other model-family context where exposed
- ensemble/model spread
- historical forecast archive
- archived model runs by initialization time
- bias-correction and no-look-ahead replay research

Rules:
- Never label Open-Meteo as settlement authority unless the exact contract names it.
- Preserve model identity, initialization time, valid time, retrieval time and source revision.
- Do not average models naively; disagreement is an uncertainty feature and historical performance may drive weights only through certified calibration.
- Production/commercial usage must comply with the then-current Open-Meteo licence/plan; free endpoint eligibility must be rechecked before deployment.

### 3. NOAA/NCEI Climate Data Online
Role: HISTORICAL_OFFICIAL_CLIMATE + CALIBRATION + RECONCILIATION
Access: free; API token required; documented limits currently 5 requests/second and 10,000 requests/day per token.
Use for:
- historical station observations
- station climatology
- residual/error calibration
- settlement reconciliation where the dataset/source matches the contract
- station-specific bias and seasonal error studies

Rules:
- NCEI historical data is a calibration source, not automatically the settlement source.
- Freeze station ID, dataset ID, data type, retrieval time and observation date.

## Optional free-tier corroboration

### 4. Xweather / Vaisala
Role: SECONDARY_FORECAST + INDEPENDENT_OBSERVATION_CORROBORATION
Access: free account/API key required; current public pricing advertises the first 15,000 API accesses per month free with no credit card required.
Use for:
- independent live-condition checks
- forecast corroboration
- anomaly detection against NWS/Open-Meteo
- optional observation-quality comparison

Rules:
- Optional only; the engine must remain functional without Xweather.
- Never override contract settlement authority.
- Never convert Xweather forecast confidence into governed Kalshi probability directly.
- Persist source timestamps and endpoint identity.
- Recheck free-tier and licensing terms before production deployment.

## Source hierarchy

1. EXACT_KALSHI_CONTRACT_RULES / CONTRACT_NAMED_SETTLEMENT_SOURCE
2. CONTRACT_NAMED_OFFICIAL_OBSERVATION
3. NWS/NOAA authoritative U.S. observations and forecasts where applicable
4. Open-Meteo numerical-model and historical-forecast evidence
5. Xweather optional corroboration
6. Other consumer forecasts as CONTEXT_ONLY unless specifically approved

Lower-tier sources may widen uncertainty or trigger review, but may not silently override higher-tier settlement evidence.

## Weather Model V2 usage

The zero-cost stack feeds one governed probability engine:

`contract rules -> exact source/station identity -> NWS/official observations -> Open-Meteo model ensemble/disagreement -> NOAA/NCEI station calibration -> optional Xweather corroboration -> station/horizon distribution -> calibrated P(YES)/P(NO) + uncertainty bounds`

The model must estimate the exact settlement event independently of Kalshi price.

## Required provenance per source snapshot

- provider
- endpoint/dataset/model
- station/gridpoint/coordinates
- model initialization time where applicable
- observation/forecast valid time
- retrieval time
- units
- source role
- contract settlement-authority flag
- immutable raw payload hash or equivalent snapshot identity

## Acceptance gates

- Removing Xweather does not prevent probability generation.
- Open-Meteo failure does not erase valid official observations; uncertainty widens and model follows typed fallback policy.
- NWS outage does not permit silent nearby-station substitution.
- Historical replay uses archived forecast vintages available at decision time, never today's revised forecast.
- All model-source disagreement is recorded and incorporated as uncertainty rather than resolved by narrative judgment.
- Settlement uses the frozen exact contract source, not a majority vote among weather providers.
- can_execute=false always.

## Implementation priority

P0: NWS API
P0: Open-Meteo forecast + historical forecast/archive adapters
P0: NOAA/NCEI historical/calibration adapter
P1: Xweather optional corroboration adapter

Paid providers such as Meteomatics, Tomorrow.io paid capabilities, or AccuWeather are not required for Weather V2 launch. They may be evaluated later only if forward calibration demonstrates measurable incremental value over the zero-cost core.
