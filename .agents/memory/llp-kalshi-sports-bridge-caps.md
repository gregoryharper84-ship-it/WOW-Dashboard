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
passing regression tests all hold simultaneously.

**Update 2026-07-05 — real inventory found, STUB_CEILING removed:** real
MLB/WNBA single-game winner-market inventory now exists on Kalshi (was
previously believed permanently empty). Per explicit user sign-off, the
`ml_evaluate.py` blanket `LLP_WATCH` ceiling was removed — the endpoint now
routes through the canonical `cap_label`/`LLPLabel` from
`gate_engine/llp_governance.py`, so `LLP_PLAYABLE` is reachable whenever
every cap above passes. `LLP_APPROVED` remains permanently unreachable from
this endpoint by design (stateless single-shot call; WATCH→APPROVED needs a
full session-scoped governance rerun this endpoint can't perform) — this is
a structural ceiling, not a data-availability stub, and must not be "fixed"
later by trying to make APPROVED reachable here.

**Update 2026-07-05 — Kalshi Sports ML Edge Rule (WNBA/MLB only): mandatory
sportsbook no-vig consensus gate added.** `ml-evaluate` must never compute a
money edge from `model_probability` alone. A live no-vig consensus fair
probability for the exact Kalshi YES-side team (`kalshi_engine/llp_bridge/
consensus_odds.py`, Odds API primary / TheRundown fallback-corroboration
only) is now a mandatory upstream gate, and Step 5's post-friction edge must
independently clear the floor against BOTH the model probability and the
consensus. Ceilings: `NOT_CALLED`/`FAILED` (no consensus) -> `LLP_SCOUT`;
`STALE`/`CONTRADICTORY`/`single_book_fallback=True` -> `LLP_WATCH`.
**Gotcha:** a `STALE` consensus result legitimately has
`consensus_fair_probability=None` (same as NOT_CALLED/FAILED) — any status
gate here must branch on `status` first and only treat "probability is
None" as a fallback/defensive case afterward, or STALE gets silently
misrouted into the harsher SCOUT ceiling instead of WATCH. This exact bug
was caught by a dedicated unit test before reaching a live call.

**Update 2026-07-05 — `no_sub_title` does NOT name the opponent team.**
On the MLB/WNBA winner-market series, Kalshi's `no_sub_title` candidate
field duplicates `yes_sub_title` on every ticker observed live — it is
not a reliable source for "who is the other team". Any code needing the
opponent (e.g. for a consensus-odds lookup) must instead parse the
market's own `title` text (fixed `"TeamA vs TeamB Winner?"` wording) via
`kalshi_engine/llp_bridge/title_parser.py::parse_opponent_team`, which
returns `None` rather than guessing if the title doesn't match or
`yes_team` isn't one of the two parsed teams. Silently trusting
`no_sub_title` caused every consensus lookup to search for a team against
itself, producing a misleading `FAILED` status on games that actually had
a real, fresh sportsbook consensus available.
