# Kalshi Weather V2 implementation status

Implemented:
- three strict specialist agents
- deterministic terminal governor
- single orchestrator entrypoint
- machine-readable exact contract bounds
- station/lane/lead-time calibration profile
- continuity-corrected exact-event probability math
- same-day daily-high conditioning on observed official maximum
- NWS, Open-Meteo, NOAA/NCEI and optional Xweather adapter interfaces
- hardened HTTPS GET-only JSON client with bounded retries
- public Kalshi market and orderbook adapter
- executable ask reconstruction from opposite-side bids only
- explicit prohibition on last-price/displayed-chance substitution
- fee/friction fail-closed market conversion; zero fees are never assumed
- regression tests for fail-closed settlement, probability independence, market holds, terminal precedence and public market plumbing

Not implemented yet:
- live Kalshi contract-rule acquisition/parser and settlement-source resolver
- cache/rate-limit coordination beyond bounded per-request retry policy
- station-specific calibrator fitting from production historical data
- Supabase persistence and immutable ledgers
- Render route mounting / capability registration
- verified Kalshi fee-policy calculator
- portfolio governor integration beyond terminal weather decision
- Custom GPT Action schema and deployment

Safety: can_execute=false; no order-placement/cancel/modify interfaces exist in this package.
