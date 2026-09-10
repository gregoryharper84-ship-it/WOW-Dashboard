# Fee-policy implementation boundary

The fee-policy calculator is analytical and fail-closed.

It resolves exact Series fee metadata, current Event overrides, and scheduled Series/Event fee changes. It implements current fixed-point trade-fee precision separately from member balance-alignment precision, and it requires explicit balance precision before calculating a one-fill cash break-even.

It does not claim exact multi-fill cash friction because Kalshi's rounding accumulator and rebates are path-dependent across fills. It also does not interpret an active fee-waiver timestamp as a zero-fee instruction without exact waiver semantics.

This slice does not alter probability generation, settlement semantics, capability certification, or execution permissions. `can_execute=false` remains invariant.
