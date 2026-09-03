# WOW Kalshi Weather — Sharpen & Migration Audit

Date: 2026-09-02
Status: MIGRATION_TARGET / NO_LIVE_EXECUTION
Host target: WOW_KALSHI_ENGINE
Runtime target: dedicated Render service + Supabase persistence
can_execute=false
capital_allocation=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true

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
8. Sporting/weather probability remains independent of market-implied probability.
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

## ADD

### Data / provenance

1. Contract-specific settlement-source resolver with versioned rule snapshot.
2. Source-role typing: SETTLEMENT_AUTHORITY, OFFICIAL_OBSERVATION, PRIMARY_FORECAST, SECONDARY_FORECAST, MARKET_PRICE, CONTEXT_ONLY.
3. Direct NWS API acquisition for points/grid forecasts, hourly forecasts and station observations.
4. The Weather Company settlement-source adapter when named by the Kalshi contract; weather.com/Kalshi archival verification where applicable.
5. NOAA/NCEI historical climate adapter for training, backtesting and reconciliation where applicable.
6. Full observation-series reconstruction for max/min-so-far rather than trusting a single daily-extreme field.
7. Evidence timestamp, retrieval timestamp, effective/valid time and source revision ID on every input.
8. Forecast-revision ledger: preserve successive forecast snapshots instead of overwriting.

### Probability model

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

### Runtime / persistence

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
10. Recovery-mode status from immutable Kalshi history; do not carry an old emergency mode forward blindly, and do not clear it without evidence.

## Current-source corrections discovered during this audit

- Kalshi states that each market's rules define the verification source; the engine must therefore resolve settlement authority per contract.
- Current Kalshi weather documentation distinguishes daily temperature markets from hourly temperature markets and names different authoritative settlement sources depending on the contract.
- Kalshi and The Weather Company announced a 2026 partnership under which The Weather Company can provide authoritative observation data for weather-market settlement. This makes the old universal-NWS settlement assumption unsafe.
- NWS API documentation warns that observations can be delayed and that some 24-hour max/min fields have known limitations, so official observation-series reconstruction is required for intraday state.
- Kalshi's API exposes public market/orderbook data. Evaluation should use executable bid/ask, not midpoint or displayed probability.

## Model hierarchy

Primary production target:

`contract rules -> source resolver -> forecast/observation feature package -> fitted station/horizon weather distribution -> calibrated bracket probabilities + bounds`

Fallback benchmark:

`NWS forecast high -> Gaussian(mu, sigma=3.5F) -> continuity-corrected bracket probabilities`

Fallback results may be research/watch output only unless separately certified. Market price must never be substituted for the sporting/weather model probability.

## Proposed workflow

`WOW_KALSHI_ENGINE`
`-> contract identity + rule snapshot`
`-> settlement-source resolver`
`-> weather acquisition and provenance freeze`
`-> exact station/coordinate audit`
`-> fitted weather distribution`
`-> calibration + uncertainty bounds`
`-> probability-claim audit`
`-> live Kalshi market/orderbook acquisition`
`-> price/fee/edge audit`
`-> individual weather decision`
`-> portfolio risk & combo governor`
`-> immutable prediction/recommendation record`
`-> terminal reducer`

## Acceptance gates

1. Wrong or unresolved settlement source fails closed.
2. Wrong station/coordinate fails closed.
3. A screenshot/displayed chance cannot become model probability or fresh executable price.
4. Full bracket probabilities normalize within tolerance.
5. Intraday impossible brackets receive zero conditional probability before normalization.
6. A model probability must be produced without reference to Kalshi price.
7. Calibration package includes point probability and uncertainty bounds.
8. Stale/empty/closed orderbook blocks edge publication but does not erase a completed weather probability.
9. Same city/date multi-bracket structures trigger duplicate/concentration governance.
10. Shared weather-system combinations never default to independence.
11. Settlement grading uses the exact source named in the frozen contract rule snapshot.
12. Postmortem cannot use later data to change the immutable pre-settlement prediction.
13. can_execute=false in every terminal state.
14. No GPT Action exposes order placement, cancellation or modification.

## Migration phases

### Phase 0 — Recover and freeze
- Preserve existing skills, patches and known route contracts as historical inputs.
- Inventory any recoverable old Kalshi backend code in GitHub.
- Freeze the new contract in this document before porting runtime behavior.

### Phase 1 — Weather core v2
- Implement contract/source resolver.
- Implement NWS + Weather Company/settlement adapters.
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

## Weather-plugin decision

No suitable dedicated NWS/NOAA weather plugin was found in the currently installable plugin catalog during this audit. The built-in ChatGPT weather surface can remain a secondary human-readable cross-check, but it is not the governed source for exact Kalshi settlement or fitted weather probability. Prefer direct backend integrations with authoritative, timestamped sources.

## Terminal migration status

KALSHI_WEATHER_LEGACY_SPEC=RECOVERED
KALSHI_WEATHER_LEGACY_RUNTIME=ORPHANED_REPLIT
KALSHI_WEATHER_SHARPEN_AUDIT=COMPLETE_V1
KALSHI_WEATHER_NEW_RUNTIME=NOT_YET_IMPLEMENTED
KALSHI_WEATHER_MODEL_V2=DESIGN_APPROVED_FOR_IMPLEMENTATION
can_execute=false
