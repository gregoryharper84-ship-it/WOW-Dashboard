# Kalshi Weather V2 fee-policy evidence

This analytical fee layer is grounded in current Kalshi public documentation and public fee metadata.

Authoritative implementation assumptions:

- General taker model fee formula uses `M * 0.07 * C * P * (1-P)`.
- Maker-fee markets use the documented maker coefficient `0.0175` with the applicable multiplier.
- Current fixed-point fee mechanics round the model trade fee upward to six decimal dollar places (`$0.000001`).
- Account balance alignment is separate from the model trade fee: direct members use `$0.0001`; non-direct members use `$0.01`.
- Rounding-fee accumulators and rebates are per order across fills, so generic pre-trade analysis must not claim an exact multi-fill cash fee.
- Series fee policy is read from the exact Series object.
- Event fee overrides layer on top of the parent Series policy.
- `GET /series/fee_changes` is queried with the exact series ticker and historical changes enabled for stale-policy detection.
- `GET /events/fee_changes` is queried with the exact event ticker; incomplete pagination fails closed.
- An active `fee_waiver_expiration_time` does not become a zero-fee assumption without exact waiver semantics.

Current public references checked during implementation:

- Kalshi Fee Schedule, effective July 7, 2026: `https://kalshi.com/docs/kalshi-fee-schedule.pdf`
- Fixed-point Fee Rounding: `https://docs.kalshi.com/getting_started/fee_rounding`
- Series fee changes: `https://docs.kalshi.com/api-reference/exchange/get-series-fee-changes`
- Event fee changes: `https://docs.kalshi.com/api-reference/events/get-event-fee-changes`

Safety invariant: this package remains analytical only with `can_execute=false`; no order placement, cancel, amend, or decrease interfaces are introduced.
