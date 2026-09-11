# Kalshi Weather V2 live-source contract

All production analytical acquisition is read-only and timestamped. `can_execute=false`.

## Contract and market evidence
- Kalshi market -> event -> series rule evidence is acquired and frozen before scoring.
- Exact settlement source/location comes from the governing contract/series evidence; market title alone is never enough.
- Kalshi orderbook data is downstream market evidence only and never a weather-model feature.

## Hourly temperature
- Existing hourly forecast fusion uses exact target-time NWS and Open-Meteo evidence.
- Settlement uses the exact frozen settlement-index contract.

## Daily high temperature
- Primary forecast evidence: NWS hourly forecast filtered to the exact contract-local date; central estimate is that day's forecast maximum.
- Intraday official observation evidence: NWS station observation series filtered to the exact contract-local date and reconstructed into max-so-far.
- Secondary forecast evidence: Open-Meteo multi-model daily highs requested using the exact contract timezone.
- Open-Meteo contributes model-disagreement information; it does not overrule authoritative settlement evidence by majority vote.
- NOAA/NCEI remains a historical calibration/reconciliation source, not a live substitute for missing contract settlement evidence.
- Xweather remains optional corroboration only and cannot be a hard dependency.

## Temporal provenance
A source retrieved or issued after `analysis_time` is invalid for that decision snapshot. Historical replay must use only source vintages available at the recorded decision time.

## Safety
No source adapter may expose POST/PUT/PATCH/DELETE trading actions. No market price, midpoint, screenshot percentage, or implied probability may be injected into the weather probability model.
