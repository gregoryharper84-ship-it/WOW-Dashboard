# Overnight Blockers

Current evidence-derived blockers:

1. MLB V2D Calibration Health remains BLOCKED while legitimate forward-shadow games are pending. This is time-gated and must not be force-cleared.
2. Durable Agent Runtime code exists on this branch but is not yet merged/deployed.
3. Render persistent queue/worker infrastructure is not yet provisioned/certified.
4. Any lane-specific model capability still requires its own fitted artifact, calibration, freshness, and publication gates; no generic fallback is allowed.

Invariant: `can_execute=false`; `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`.
