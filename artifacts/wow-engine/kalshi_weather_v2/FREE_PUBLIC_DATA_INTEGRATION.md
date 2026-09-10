# Free/Public Weather Data Integration

Approved zero-cost core:
- NWS API: primary U.S. forecast and station-observation evidence.
- Open-Meteo: secondary multi-model forecast/disagreement evidence and archive replay.
- NOAA/NCEI: historical station/climate calibration and reconciliation evidence.
- Xweather/Vaisala: optional corroboration only; never a hard dependency.

Settlement authority is always the exact source named by the frozen Kalshi contract/series rules. None of these providers may silently replace a different contract-named settlement source.

For `DAILY_HIGH_TEMPERATURE`:
- NWS hourly periods are filtered using the exact contract timezone and local calendar date.
- official max-so-far is reconstructed from the NWS station observation series for that same local date.
- Open-Meteo daily-high aggregation is requested using the exact contract timezone, not blindly UTC.
- provider disagreement widens uncertainty/flags risk; it is not resolved by majority vote or narrative judgment.

For `HOURLY_TEMPERATURE`, the existing exact-target-time UTC forecast fusion remains controlling.

All evidence must preserve retrieval/issuance/valid timestamps sufficient for decision-time replay. Market price is never a weather-model input. `can_execute=false`.
