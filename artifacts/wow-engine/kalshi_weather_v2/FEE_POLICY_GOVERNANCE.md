# Fee-policy governance invariants

- Market price is never substituted for weather probability.
- Fee metadata changes edge/break-even analysis only; it never changes model probability.
- Unknown fee policy, waiver semantics, account balance precision, or incomplete fee-change history holds edge publication fail-closed.
- `can_execute=false` remains invariant.
- No order placement, amendment, cancellation, or decrease interfaces are introduced by this slice.
