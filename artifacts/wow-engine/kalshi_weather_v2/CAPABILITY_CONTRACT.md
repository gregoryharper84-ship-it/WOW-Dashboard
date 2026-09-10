# KALSHI_WEATHER_PROBABILITY capability contract

Host: `WOW_KALSHI_ENGINE`
Capability: `KALSHI_WEATHER_PROBABILITY`
Mode: analytical / non-executable

Required publication package:
- exact frozen contract/rule snapshot
- verified settlement source and station/coordinate identity
- timestamped weather evidence snapshots
- independent calibrated `P(YES)` / `P(NO)`
- lower/upper uncertainty bounds
- threshold-distance classification
- calibration method/version
- model/source provenance

Market/edge publication additionally requires:
- market open
- non-empty orderbook
- executable side verified
- price timestamp
- verified fee/friction break-even

A valid weather probability survives a market-data hold. Market price never substitutes for model probability.

`can_execute=false`
`capital_allocation=false`
`DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`
