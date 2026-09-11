# Free/Public Weather Data Integration

The goal is not to maximize source count. The goal is to use every zero-cost/open source that materially improves settlement fidelity, independent forecast skill, uncertainty estimation, or calibration history without weakening source hierarchy.

## P0 — active certification sources

- **Kalshi public Trade/Weather APIs** — exact contract/series rules, open market/orderbook data, and canonical Weather Index settlement evidence. This remains the controlling settlement authority when named by the contract.
- **NWS API (`api.weather.gov`)** — primary U.S. point/hourly forecast, raw grid forecast, station observations, and official NWS product access. U.S. government open data; reasonable rate limits apply.
- **Open-Meteo deterministic multi-model** — secondary GFS/ECMWF/GEM/ICON-style model disagreement evidence. The hosted free endpoint is non-commercial/rate-limited; production commercial use must move to an appropriate paid or self-hosted endpoint.
- **Open-Meteo Ensemble API** — ensemble spread/uncertainty evidence. Underlying model lineage is tracked so ensemble members are not falsely counted as independent providers.
- **Open-Meteo Previous Runs / Single Runs** — historical forecast replay at controlled lead times and exact run initialization for no-lookahead research.
- **NOAA/NCEI Access Data Service** — tokenless historical daily station/climate summaries for station-settled lanes and reconciliation.
- **Iowa Environmental Mesonet ASOS archive** — free ASOS/METAR and one-minute observation history for corroboration and historical station research. IEM is not settlement authority unless a Kalshi contract explicitly makes it so.

## P1 — enabled as corroboration / research

- **MET Norway Locationforecast** — global forecast corroboration with mandatory identifying User-Agent. It does not automatically count as an independent provider family because underlying model lineage may overlap other sources.

## Optional direct-model source

- **NOAA/NCEP NOMADS** — direct HRRR/GFS/NAM/NBM operational model data. It is free and authoritative, but raw GRIB processing adds operational weight. For the current certification slice, the same NCEP model families are available through governed JSON archives, so NOMADS is kept optional rather than becoming a reliability dependency. It can be promoted if direct-source verification adds measurable out-of-sample value.

## Source authority rules

Settlement authority is always the exact source named by the frozen Kalshi contract/series rules. None of these providers may silently replace a different contract-named settlement source.

For `DAILY_HIGH_TEMPERATURE`:
- NWS hourly periods are filtered using the exact contract timezone and local calendar date.
- official max-so-far is reconstructed from the NWS station observation series for that same local date.
- Open-Meteo daily-high aggregation is requested using the exact contract timezone, not blindly UTC.
- NCEI/IEM are historical calibration/corroboration sources unless the contract expressly permits them for settlement.
- provider disagreement widens uncertainty/flags risk; it is not resolved by majority vote or narrative judgment.

For `HOURLY_TEMPERATURE`:
- the exact Kalshi Weather Index target minute remains settlement truth;
- NWS + Open-Meteo deterministic evidence remains the controlling forecast mean until empirical testing approves another fusion;
- NWS raw grid, Open-Meteo ensembles, and MET Norway are captured as non-controlling diagnostic evidence;
- threshold-sibling contracts do not create duplicate calibration samples for the same weather target and lead-time bucket.

All evidence must preserve retrieval/issuance/valid timestamps sufficient for decision-time replay. Market price is never a weather-model input. `probability_publishable=false` remains mandatory until certified calibration and V17 promotion gates pass. `can_execute=false`.
