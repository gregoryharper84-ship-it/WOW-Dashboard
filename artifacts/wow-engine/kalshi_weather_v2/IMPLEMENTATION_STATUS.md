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
- live public market -> event -> series contract-rule acquisition
- immutable hash-addressed market rule snapshots before interpretation
- structured settlement-source resolution from Kalshi series metadata
- multiple-source settlement ambiguity fails closed unless exact market rule text disambiguates one source
- regression tests for fail-closed settlement, probability independence, market holds, terminal precedence, public market plumbing and rule acquisition

Not implemented yet:
- full semantic conversion of live rule package into ContractSnapshot (station/coordinate, timezone, rounding and observation-window parser)
- cache/rate-limit coordination beyond bounded per-request retry policy
- station-specific calibrator fitting from production historical data
- Supabase persistence and immutable ledgers
- Render route mounting / capability registration
- verified Kalshi fee-policy calculator
- portfolio governor integration beyond terminal weather decision
- Custom GPT Action schema and deployment

Safety: can_execute=false; no order-placement/cancel/modify interfaces exist in this package.
