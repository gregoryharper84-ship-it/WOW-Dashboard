# V17 TheRundown winner acquisition: paid-scope admission

This is a **Class B, market-evidence-only** hardening change. It does not change
sporting probabilities, model weights, terminal reduction, or `can_execute=false`.

## Required operator configuration

Before enabling paid TheRundown winner acquisition, verify the actual V2
market IDs and entitled affiliate IDs from provider evidence, then configure:

- `WOW_RUNDOWN_WINNER_MARKET_IDS`: comma-separated **numeric provider IDs**
  for the outright-winner market (or use the existing pinned market-ID mapping).
- `WOW_RUNDOWN_WINNER_AFFILIATE_IDS`: comma-separated **numeric provider
  affiliate IDs** for explicitly selected entitled sportsbooks.

No IDs are supplied by this patch. **Do not guess IDs or infer entitlement.**
Missing, empty, malformed or nonnumeric dimensions result in
`RUNDOWN_PAID_SCOPE_UNRESOLVED` before the paid odds request. The LLP
post-score bridge preserves the fitted sporting probability and reports
`MARKET_DATA_UNOBTAINABLE`; the additive ML board preserves primary discovery.

Both consumers pass `market_ids`, `affiliate_ids`, `main_line=true`, and
`hide_closed=true` into the existing snapshot cache/acquisition transport.

## Local verification

From `artifacts/wow-engine`, run:

```bash
pytest -q tests/test_v17_rundown_paid_scope.py tests/test_v17_rundown_ml_board.py tests/test_v17_rundown_ml_auth_boundary.py tests/test_v17_llp_rundown_market_bridge.py tests/test_v17_free_core_budget_scope_integration.py
```

These are **local unit tests**, not a production quota or entitlement probe.
Do not merge or deploy until CI, independent QA and Release gates pass.
Live provider receipt accounting, entitlement verification and reserve admission
remain separate follow-up work. No production provider call is authorized by
this patch.

## Rollback

Disable optional consumers independently with
`WOW_RUNDOWN_ML_BOARD_ENABLED=false` and
`WOW_LLP_RUNDOWN_MARKET_ENABLED=false`. Rollback of the code must go through
the normal governed PR/release process.
