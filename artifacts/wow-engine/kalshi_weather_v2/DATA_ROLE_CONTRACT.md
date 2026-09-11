# Kalshi Weather V2 data-role contract

Source roles are authority boundaries, not labels for averaging.

- `SETTLEMENT_AUTHORITY`: exact frozen source named by the Kalshi contract. Controls grading only.
- `OFFICIAL_OBSERVATION`: official station/index observations used for trajectory/max-so-far when identity matches the contract.
- `PRIMARY_FORECAST`: highest-priority forecast evidence used by the fitted weather model.
- `SECONDARY_FORECAST`: independent model guidance used for disagreement, uncertainty and calibrated features.
- `HISTORICAL_CALIBRATION`: historical observations/forecast archives used to fit and validate error distributions without look-ahead.
- `CORROBORATION_ONLY`: optional evidence that may flag disagreement but cannot make the core model available or override settlement evidence.
- `MARKET_PRICE`: downstream executable pricing evidence only; never a weather-model feature.

For daily-high V2, NWS hourly forecast is currently `PRIMARY_FORECAST`, NWS station observations are `OFFICIAL_OBSERVATION` when the exact station is contract-compatible, Open-Meteo is `SECONDARY_FORECAST`, NOAA/NCEI is `HISTORICAL_CALIBRATION`, and Xweather is `CORROBORATION_ONLY`.

If the Kalshi contract names a different settlement authority, that source still controls grading. Provider consensus cannot replace it.

`can_execute=false`.
