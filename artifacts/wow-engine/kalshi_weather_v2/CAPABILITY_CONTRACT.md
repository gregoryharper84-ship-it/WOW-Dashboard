# Kalshi Weather V2 capability contract

Capability family: `KALSHI_WEATHER_PROBABILITY`
Host: `WOW_KALSHI_ENGINE`
`can_execute=false`

A model capability is not the same thing as source availability, a successful forecast fetch, or a market being open.

Publication requires all applicable gates to succeed:
- exact contract and settlement identity resolved;
- decision-time-safe authoritative evidence;
- supported market family and exact event semantics;
- certified calibration profile for the relevant settlement location / lane / lead-time bucket;
- coherent numeric probability package with uncertainty bounds;
- immutable pre-settlement persistence;
- capability status explicitly available/ratified for that market family.

Current family state:
- `HOURLY_TEMPERATURE`: shadow runtime exists; publication remains governed by its calibration/capability state.
- `DAILY_HIGH_TEMPERATURE`: evidence fusion exists, but this alone MUST NOT mark the family probability-publishable. Daily-high runtime, certified calibration, forward shadow acceptance, settlement reconciliation, and capability ratification remain required.

Market/orderbook availability is a separate downstream contract. Missing or stale market data may block edge publication but must not erase a completed sporting/weather probability.

No capability state may expose live trading, order placement, cancellation, modification, or capital allocation.
