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
- GET-only HTTPS JSON client with bounded retry, cache and rate pacing
- read-only Kalshi market + orderbook adapter using current fixed-point dollar fields
- executable YES/NO buy-price derivation from the opposite-side best bid
- regression tests for fail-closed settlement, probability independence, market holds, live-source safety and terminal precedence

Not implemented yet:
- fully automatic live Kalshi weather contract parser / settlement-source resolver from market + series rule metadata
- production source retry/cache/rate policy tuning per provider
- station-specific calibrator certification from historical + forward data
- Supabase persistence and immutable ledgers
- Render route mounting / capability registration
- verified Kalshi fee/friction calculator
- portfolio governor integration beyond terminal weather decision
- Custom GPT Action schema and deployment

Safety: can_execute=false; no order-placement/cancel/modify interfaces.

Current implementation marker: `AGENTS_GOVERNOR_LIVE_SOURCE_CORE_V2`.
