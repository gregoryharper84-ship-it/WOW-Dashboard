# Kalshi Weather V2 implementation status

Current architecture on main includes:
- three strict specialist agents
- deterministic terminal governor
- single orchestrator entrypoint
- exact contract rule acquisition and frozen rule snapshots
- hourly temperature rule semantics, forecast fusion, settlement index and shadow runtime
- NWS, Open-Meteo, NOAA/NCEI and optional Xweather source adapters
- GET-only HTTPS acquisition with bounded retry/cache/rate pacing
- read-only Kalshi market/orderbook evidence
- governed fee policy
- Supabase persistence and immutable rule/source/prediction/market/outcome ledgers
- API route surface for the existing hourly shadow workflow
- `can_execute=false` throughout

Added in the daily-high fusion slice:
- deterministic `DAILY_HIGH_TEMPERATURE` weather evidence builder
- NWS hourly target-local-date maximum as primary forecast estimate
- official max-so-far reconstructed from the NWS observation series
- Open-Meteo model highs as disagreement/corroboration inputs only
- explicit contract-timezone aggregation for Open-Meteo daily highs
- temporal provenance rejection for future-retrieved/future-issued sources
- market-blind evidence metadata
- fail-closed regression coverage for missing official observations and target-day forecasts

Still required before daily-high production probability publication:
- daily-high shadow runtime that acquires exact rule semantics, station observations and model evidence end-to-end
- station/series policy registry for exact timezone, observation window, settlement location and rounding semantics
- certified daily-high station/lead-time calibration profiles
- daily-high forward shadow acceptance and settlement reconciliation
- capability registration/ratification for the daily-high family
- Custom GPT Action sync to the owned Render origin

Safety: no order-placement/cancel/modify interface is permitted. `can_execute=false` remains invariant.
