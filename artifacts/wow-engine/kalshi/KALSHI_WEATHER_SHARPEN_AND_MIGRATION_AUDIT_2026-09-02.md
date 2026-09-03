# WOW Kalshi Weather — Sharpen & Migration Audit

Date: 2026-09-02
Status: MIGRATION_TARGET / NO_LIVE_EXECUTION
Host target: WOW_KALSHI_ENGINE
Runtime target: dedicated Render service + Supabase persistence
can_execute=false
capital_allocation=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true

## Authoritative contracts

Functional skill contract: `KALSHI_WEATHER_MARKET_EXPERT_V2.md`
Zero-cost source contract: `KALSHI_WEATHER_FREE_PUBLIC_DATA_STACK_2026-09-02.md`

The V2 skill preserves the user-supplied Kalshi Weather Market Expert behavior: lane routing, mandatory contract parse, settlement identity gate, forecast-as-of discipline, lane-specific probability modeling, threshold-distance analysis, independent P(YES)/P(NO), model-disagreement handling, Wolfram math verification, market edge, settlement-risk separation, live updates, coherence rules, calibration ledger, confidence/status framework, ranking rules, required output, fail-closed behavior, and efficiency rules.

## Executive decision

Do not lift-and-shift the orphaned Replit runtime. Preserve the strong weather-market contracts, remove legacy/runtime coupling and non-numeric reasoning layers, update settlement-source assumptions, sharpen the probability model, and rebuild the lane as a separate governed Kalshi service on infrastructure controlled by WOW.

The weather lane remains separate from the sports WOW V17 host. The Kalshi Weather Market Expert owns weather-event probability. Market price/edge auditing and the Kalshi Portfolio Risk & Combo Governor remain downstream.

## KEEP

1. Contract-first identity and exact settlement-source verification.
2. Exact station/coordinate mapping; nearby-station substitution is prohibited.
3. Separation of current temperature, maximum-observed-so-far, preliminary observations, and final settlement value.
4. Inclusive-bracket continuity correction and mutually-exclusive bracket normalization.
5. Intraday elimination/truncation logic: final daily maximum cannot fall below the official maximum already observed.
6. Live executable-price requirement. Screenshot/displayed percentages are context only.
7. Price freshness, open-market, non-empty-orderbook, fee and settlement audits.
8. Weather probability remains independent of market-implied probability.
9. Settlement auditor and immutable pre-settlement prediction record.
10. Kalshi Portfolio Risk & Combo Governor after individual-market qualification.
11. Duplicate-thesis, same-city/date and shared-weather-system concentration controls.
12. Postmortem separation: component selection, portfolio structure, pricing quality and realized outcome.
13. Typed fail-closed outcomes and can_execute=false.

## REMOVE / CONSOLIDATE

1. All Replit-specific host, scheduler, routing and deployment assumptions.
2. Any rule declaring NWS CLI the universal settlement authority. Settlement authority must be resolved from the exact live Kalshi contract.
3. A fixed 3.5 F Gaussian sigma as the sole production uncertainty model. Retain it only as fallback/benchmark when no certified station/lead-time calibration exists.
4. Narrative AI probability adjustments that do not flow through fitted/certified numeric coefficients.
5. Duplicate weather Scout/Research/agent layers that only restate forecast context and do not change governed inputs or failure classification.
6. Legacy route aliases once the new Action contract is live.
7. Redundant fallback sources without explicit provenance, timestamp and role typing.
8. Any use of midpoint/displayed chance as executable entry price.
9. Any assumption that a single NWS 24-hour max field is sufficient for max-so-far reconstruction.

## ADD — Data / provenance

1. Contract-specific settlement-source resolver with versioned rule snapshot.
2. Source-role typing: SETTLEMENT_AUTHORITY, OFFICIAL_OBSERVATION, PRIMARY_FORECAST, SECONDARY_FORECAST, MARKET_PRICE, CONTEXT_ONLY.
3. Direct NWS API acquisition for points/grid forecasts, hourly forecasts and station observations.
4. The Weather Company settlement-source adapter when named by the Kalshi contract; weather.com/Kalshi archival verification where applicable.
5. NOAA/NCEI historical climate adapter for training, backtesting and reconciliation where applicable.
6. Open-Meteo adapter for multi-model comparison, ensemble/model disagreement and historical forecast/archive replay.
7. Optional Xweather/Vaisala corroboration adapter under the free tier; the engine must remain functional without it.
8. Full observation-series reconstruction for max/min-so-far rather than trusting a single daily-extreme field.
9. Evidence timestamp, retrieval timestamp, effective/valid time and source revision ID on every input.
10. Forecast-revision ledger: preserve successive forecast snapshots instead of overwriting.

## ADD — Probability model

1. Station-specific bias correction by season/month and forecast lead time.
2. Station- and lead-time-specific forecast error distributions.
3. Forecast-source disagreement / ensemble spread as an uncertainty input.
4. Heavy-tail/non-Gaussian support when empirical residuals reject a Gaussian assumption.
5. Intraday conditional model using observed maximum, recent trend, remaining heating window and certified weather features.
6. Calibrated probability plus lower/upper uncertainty bounds.
7. Dynamic calibration by station, market family and horizon.
8. Model-disagreement monitor: governed probability vs decision-time market and subsequent close; review only, never automatic probability suppression.
9. Temporal feature provenance to prevent post-outcome or later-revision leakage.
10. Hypothesis-change ledger for every model change with rationale, affected feature, expected direction, untouched holdout and before/after calibration.

## ADD — Runtime / persistence

1. Dedicated `WOW_KALSHI_ENGINE` Render service.
2. Supabase tables for contract rules, source snapshots, weather predictions, calibration state, price snapshots, recommendation records and outcomes.
3. Immutable pre-settlement write-before-display.
4. Deterministic settlement/reconciliation grader.
5. Capability registry for `KALSHI_WEATHER_PROBABILITY`.
6. Health surface distinguishing DATA_ACQUISITION, MODEL_CAPABILITY, CALIBRATION, MARKET_DATA, SETTLEMENT and GPT_ACTION_SYNC.
7. Separate read-only Kalshi market/orderbook credentials and routes; no order-placement routes in the GPT Action schema.
8. New Custom GPT Action schema with only analytical/read/evaluate/record/settle operations.
9. Terminal reducer that preserves the lowest applicable ceiling from identity, settlement, model, calibration, price and portfolio governance.

## VERIFY BEFORE IMPLEMENTATION

1. Current settlement source for every active weather series. Never rely on a historical station table alone.
2. Daily-high/daily-low versus hourly-temperature market families have different settlement contracts and must not share an assumed source.
3. Exact station/coordinate identity and daylight-saving reporting window for each daily market.
4. Rounding, conversion and inclusive-boundary rules from the live market contract.
5. Current Kalshi API fields and best executable bid/ask semantics.
6. Current fee calculation required for fee-adjusted break-even probability.
7. Weather Company access method and whether public weather.com/Kalshi data is sufficient for settlement verification or a licensed feed is required for automated production ingestion.
8. NWS observation delays/null behavior and station-specific reporting cadence.
9. Historical data availability sufficient to estimate station/horizon residual distributions without leakage.
10. Current Open-Meteo free/non-commercial licence and rate limits before deployment.
11. Current Xweather free-tier and licensing terms before enabling the optional adapter.
12. Recovery-mode status from immutable Kalshi history; do not carry an old emergency mode forward blindly, and do not clear it without evidence.

## Model hierarchy

Primary production target:

`contract rules -> settlement-source resolver -> official observations -> NWS forecast package -> Open-Meteo multi-model/disagreement -> NOAA/NCEI station calibration -> optional Xweather corroboration -> station/horizon conditional distribution -> calibrated P(YES)/P(NO) + bounds`

Fallback benchmark:

`NWS forecast high -> Gaussian(mu, sigma=3.5F) -> continuity-corrected bracket probabilities`

Fallback results may be research/watch output only unless separately certified. Market price must never be substituted for the weather model probability.

## Acceptance gates

1. Wrong or unresolved settlement source fails closed.
2. Wrong station/coordinate fails closed.
3. Screenshot/displayed chance cannot become model probability or fresh executable price.
4. Complete bucket probabilities normalize within tolerance; nested thresholds are monotonic.
5. Intraday impossible brackets receive zero conditional probability before normalization.
6. Model probability is produced independently of Kalshi price.
7. Calibration package includes point probability and uncertainty bounds.
8. Stale/empty/closed orderbook blocks edge publication but does not erase completed weather probability.
9. Same city/date multi-bracket structures trigger duplicate/concentration governance.
10. Shared weather-system combinations never default to independence.
11. Settlement grading uses the exact source named in the frozen contract rule snapshot.
12. Historical/postmortem evaluation cannot use later data to change decision-time probability.
13. Open-Meteo/Xweather disagreement can widen uncertainty but cannot override settlement evidence by majority vote.
14. Xweather outage cannot make the core weather model unavailable.
15. Wolfram verifies math only and cannot create probability.
16. can_execute=false in every terminal state.
17. No GPT Action exposes order placement, cancellation or modification.

## Migration phases

### Phase 0 — Recover and freeze
- Preserve existing skills, patches and known route contracts as historical inputs.
- Adopt `KALSHI_WEATHER_MARKET_EXPERT_V2.md` as the implementation contract.
- Inventory recoverable old Kalshi backend code in GitHub.

### Phase 1 — Weather core V2
- Implement contract/source resolver.
- Implement NWS + contract-named settlement adapters.
- Implement Open-Meteo forecast/historical/archive adapters.
- Implement NOAA/NCEI historical/calibration adapter.
- Implement optional Xweather corroboration adapter.
- Implement observation-series reconstruction.
- Implement calibrated station/horizon probability engine while retaining Gaussian 3.5F as benchmark fallback.
- Add deterministic tests and historical replay fixtures.

### Phase 2 — Governed persistence
- Add Supabase schemas, immutable snapshots, prediction ledger, calibrator state and outcome grader.
- Add forward-test cohort and calibration reporting.

### Phase 3 — Render runtime
- Deploy a dedicated Kalshi analytical service.
- Add health/capability/evaluate/market-data routes.
- Keep execution routes absent.

### Phase 4 — Shadow acceptance
- Replay settled historical markets without leakage.
- Run forward shadow weather predictions.
- Require calibration and settlement reconciliation before production probability publication.

### Phase 5 — Custom GPT sync
- Generate `WOW_KALSHI_ENGINE` instructions and Action OpenAPI schema for the new Render origin.
- Save/verify in a fresh Custom GPT session.
- Run end-to-end screenshot, daily-high and hourly-temperature acceptance tests.

## Terminal migration status

KALSHI_WEATHER_LEGACY_SPEC=RECOVERED
KALSHI_WEATHER_LEGACY_RUNTIME=ORPHANED_REPLIT
KALSHI_WEATHER_SKILL_V2=AUTHORITATIVE_MIGRATION_CONTRACT
KALSHI_WEATHER_SHARPEN_AUDIT=COMPLETE_V3_SKILL_ALIGNED
KALSHI_WEATHER_NEW_RUNTIME=NOT_YET_IMPLEMENTED
KALSHI_WEATHER_MODEL_V2=DESIGN_APPROVED_FOR_IMPLEMENTATION
KALSHI_WEATHER_ZERO_COST_DATA_STACK=APPROVED
can_execute=false
