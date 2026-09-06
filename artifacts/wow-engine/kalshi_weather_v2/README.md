# Kalshi Weather V2

Governed analytical lane for the separate WOW Kalshi Engine.

## Fixed specialist chain

1. `ContractSettlementAgent`
   - owns exact contract identity, settlement source/station, timezone, units, rounding and rule snapshot
   - cannot create or modify weather probability
2. `WeatherProbabilityAgent`
   - owns evidence/provenance and validates the independent calibrated probability package
   - market price is prohibited as a probability input
3. `MarketCalibrationAuditor`
   - owns executable price, edge and EV arithmetic
   - cannot create or substitute weather probability
4. `KalshiWeatherTerminalGovernor`
   - deterministic lowest-ceiling reducer only
   - no voting, narrative override, or forecasting

`evaluate_weather_contract()` is the only orchestration entrypoint.

## Probability core

`WeatherProbabilityCore` uses machine-readable exact contract bounds and a station/lane/lead-time calibration profile. Integer-temperature contracts use continuity correction. Same-day daily-high markets condition the remaining distribution on the official maximum already observed.

The fixed 3.5F Gaussian from the legacy skill is not implemented here as production truth; it remains a future explicit research fallback only if separately gated.

## Approved data adapters

- NWS: primary U.S. forecast + official station observation acquisition
- Open-Meteo: secondary multi-model/model-disagreement evidence
- NOAA/NCEI: historical station calibration/reconciliation
- Xweather: optional corroboration only

None of these sources can override the settlement authority named by the frozen Kalshi contract rules.

## Safety/governance

- `can_execute=false`
- no order placement/cancel/modify interfaces
- no capital allocation
- stale/empty market data can block edge without erasing completed weather probability
- unresolved settlement identity fails closed
- market probability cannot substitute for model probability
