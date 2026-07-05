---
name: LLP-Kalshi sports bridge hard caps
description: Non-negotiable label ceilings and pricing rules for the read-only LLP<->Kalshi sports/winner-market bridge (kalshi_engine/llp_bridge/).
---

The LLP<->Kalshi sports bridge (`kalshi_engine/llp_bridge/`: `inventory_adapter.py`,
`market_mapper.py`, `price_normalizer.py`, `ml_evaluate.py`) enforces several
hard caps that must never be loosened or bypassed by future edits:

- Only an EXACT team-name match (fixed alias table, not a similarity score)
  is approval-eligible. Fuzzy/ambiguous/multi-candidate matches always cap
  at `LLP_SCOUT`.
- Edge math must use the executable-side price (YES ask derived as
  `1 - best_no_bid`), never the midpoint. Midpoint is display-only.
- Orderbook staleness grading is exact: `<60s=A`, `60-300s=B`,
  `300-600s=C`, `>=600s` (or missing/invalid timestamp) = `KALSHI_DATA_UNOBTAINABLE`.
- Missing fee/friction data (no executable price or no liquidity grade)
  caps at `LLP_WATCH` — never higher, regardless of raw price edge.
- Settlement-rule auditor: ticker + event_ticker + market_title +
  settlement_condition must all be present and unambiguous, or the row
  caps at `LLP_SCOUT`.
- Edge sequencing order is fixed and must never be reordered: spread ->
  fee/friction -> staleness -> shrinkage (only if `model_probability>=0.80`)
  -> compare to 2.5% floor.
- Every response includes `dry_run_only: true` and `can_execute: false`,
  no exceptions, no order-placement code anywhere in this package.
- The ml-evaluate route re-checks the LIVE `KalshiInventoryAdapter` signal on
  every call (not just caller-supplied `candidate_markets`); unless the live
  signal is exactly `INVENTORY_READY`, the row is hard-capped at `LLP_SCOUT`
  regardless of what data the caller supplies. This closes a gap where a
  caller could otherwise self-report a clean EXACT match + settlement grade
  and get LLP_WATCH even while the exchange has zero real sports markets.

**Why:** approved by the user's external reviewer (ChatGPT) with amendments
specifically to prevent a connector from ever silently producing an
approval-grade signal from incomplete or ambiguous data.

**How to apply:** treat these as invariants when extending the bridge (e.g.
wiring real sports inventory). Do not mark the integration "connected"
anywhere until `INVENTORY_READY` + a real ticker + Grade A/B orderbook +
passing regression tests all hold simultaneously — sports inventory was
empty as of 2026-07-05, so the ml-evaluate endpoint is a stub incapable of
emitting `LLP_PLAYABLE`/`LLP_APPROVED` by design.
