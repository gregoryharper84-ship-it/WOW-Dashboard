# Kalshi Weather V2 — Phase 1 acceptance

Phase 1 is accepted only when all of the following are true:

1. Contract resolution fails closed on missing settlement source, station/coordinate identity, thresholds, timezone, rounding, or unsupported units/lane.
2. NWS/Open-Meteo/NOAA-NCEI adapters are GET-only and preserve retrieval/source timestamps.
3. Optional Xweather cannot be required for probability availability.
4. Weather probability is computable without Kalshi price and explicitly rejects market-price substitution.
5. Daily-high intraday conditioning cannot assign positive probability below the observed official max-so-far.
6. Probability package includes P(YES), P(NO), central estimate, uncertainty bounds, threshold distance, calibration method, and source identity.
7. Market audit uses executable side data and verified fee/friction break-even; missing market data holds edge publication but does not erase a valid weather probability.
8. ContractSettlementAgent, WeatherProbabilityAgent, and MarketCalibrationAuditor have non-overlapping authority.
9. KalshiWeatherTerminalGovernor is deterministic, lowest-ceiling, non-voting, and cannot execute.
10. No order-placement, cancellation, or order-modification interfaces exist in this package.

`can_execute=false` is invariant.
