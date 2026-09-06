# Kalshi Weather V2 implementation status

Implemented in this slice:
- three strict specialist agents
- deterministic terminal governor
- single orchestrator entrypoint
- machine-readable exact contract bounds
- station/lane/lead-time calibration profile
- continuity-corrected exact-event probability math
- same-day daily-high conditioning on observed official maximum
- NWS, Open-Meteo, NOAA/NCEI and optional Xweather adapter interfaces
- regression tests for fail-closed settlement, probability independence, market holds and terminal precedence

Not implemented yet:
- live Kalshi contract parser / settlement-source resolver
- live HTTP client and retries/cache/rate-limit policy
- station-specific calibrator fitting from historical data
- Supabase persistence and immutable ledgers
- Render route mounting / capability registration
- Kalshi market/orderbook adapter
- portfolio governor integration beyond terminal weather decision
- Custom GPT Action schema and deployment

Safety: can_execute=false; no order-placement/cancel/modify interfaces.
