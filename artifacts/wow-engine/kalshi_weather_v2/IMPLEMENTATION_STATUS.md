# Kalshi Weather V2 implementation status

Implemented:
- three strict specialist agents
- deterministic terminal governor
- single orchestrator entrypoint
- machine-readable exact contract bounds
- station/lane/lead-time calibration profile structures
- continuity-corrected exact-event probability math
- same-day daily-high conditioning on observed official maximum
- NWS, Open-Meteo, NOAA/NCEI and optional Xweather adapter interfaces
- hardened HTTPS GET-only JSON client with bounded retries
- public Kalshi market and orderbook adapter
- executable ask reconstruction from opposite-side bids only
- explicit prohibition on last-price/displayed-chance substitution
- live public market -> event -> series contract-rule acquisition
- immutable hash-addressed market rule snapshots before interpretation
- structured settlement-source resolution from Kalshi series metadata
- multiple-source settlement ambiguity fails closed unless exact market rule text disambiguates one source
- strict current-shape daily max/min temperature rule parser
- source-native settlement location codes (for example CLINYC) represented directly; no forced NWS-station substitution
- parsed rule source cross-checked against series settlement_sources
- parsed threshold semantics cross-checked against Kalshi strike_type/floor_strike/cap_strike
- six immutable Supabase Kalshi Weather ledgers for rules, weather evidence, calibration profiles, predictions, market snapshots and outcomes
- KALSHI_WEATHER_PROBABILITY runtime capability registered fail-closed as UNAVAILABLE / IMPLEMENTATION_NOT_CERTIFIED / probability_publishable=false / can_execute=false
- governed analytical fee-policy calculator: current series policy + event overrides + series/event scheduled fee-change checks
- current fixed-point fee treatment: six-decimal model-fee rounding separated from member-specific balance alignment
- explicit direct-member ($0.0001) vs non-direct-member ($0.01) balance precision; account type is never guessed
- active fee-waiver markers fail closed until exact waiver semantics are available
- canonical Kalshi hourly weather-index read-only acquisition on this branch
- exact city and Fahrenheit identity checks for hourly index payloads
- preservation of real minute gaps and `incomplete` index points; no interpolation or zero filling
- detailed member-station readings preserved only as QC/evidence, never substituted for canonical index value
- published hourly-index calibration timeline frozen raw pending a separate calibration-semantics parser
- regression tests for fail-closed settlement, probability independence, market holds, terminal precedence, market plumbing, rule acquisition/semantics, fee calculations and hourly-index acquisition

Not implemented/certified yet:
- final daily semantic conversion into ContractSnapshot still requires explicit timezone, observation-window and rounding/settlement-period semantics from controlling terms
- hourly-temperature exact market-rule semantic parser and terminal lane integration
- hourly-index calibration-contract semantic parser / reproduction audit
- cache/rate-limit coordination beyond bounded per-request retry policy
- station/source-specific calibrator fitting from production historical data
- persistence wiring from live agent outputs into the existing Supabase ledgers
- Render analytical route mounting
- KALSHI_WEATHER_PROBABILITY certification/promotion
- portfolio governor integration beyond terminal weather decision
- live shadow acceptance
- Custom GPT Action schema synchronization and deployment

Safety: can_execute=false; no order-placement/cancel/modify interfaces exist in this package.
