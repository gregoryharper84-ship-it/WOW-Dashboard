---
name: Kalshi live inventory scan + orderbook wire-format quirks
description: Two root-cause bugs found scanning real Kalshi sports inventory and parsing live orderbooks — both silently produced wrong/empty results without erroring.
---

**Bug 1 — generic market listing is combo-flooded:** scanning the generic
`/markets` endpoint (no `series_ticker`, or `category=sports`) returns pages
dominated by dynamically-generated MVE combo/parlay markets. A live check
found the first 100+ paginated results were 100% combos even while real
single-game MLB/WNBA winner markets existed on the exchange — this silently
produced `INVENTORY_EMPTY` even when real inventory existed. `category` is
not reliably scoped to single-game markets either (same flooding problem).

**Fix:** scan known `series_ticker` values directly (`KXMLBGAME`,
`KXWNBAGAME` for MLB/WNBA winner markets) instead of generic/category-scoped
pagination. Combos live under different series (`KXMVE*`, `KXMLBTOTAL`,
`KXMLBKS`, etc.) and are excluded by construction.

**Bug 2 — live orderbook responses use dollar-strings, not integer cents:**
`GET /markets/{ticker}/orderbook` on the live exchange returns
`orderbook_fp: {yes_dollars, no_dollars}` with levels as
`[price_dollar_string, size_dollar_string]` (e.g. `["0.4600", "21.00"]`) —
already decimal, NOT integer cents needing `/100`. A parser assuming the
older `{"price": int_cents}` shape silently produces wrong prices (off by
~100x) with no error, since both shapes parse without exceptions.

**Fix:** detect `orderbook_fp` (or `yes_dollars`/`no_dollars` keys) and
switch to a dollar-string parse path (`float(price_str)` directly, no
`/100`); fall back to the legacy cents-int parse path for
`orderbook`/`yes_bids`/`no_bids` shaped responses.

**Why this matters:** both bugs are "silent wrong data," not crashes — they
never raised, never showed up in error logs, and looked like legitimate
empty/valid results. Any new Kalshi-adjacent connector work must live-verify
against a real ticker/orderbook (not just unit-test against assumed shapes)
before trusting a "this feature works" conclusion.

**How to apply:** when adding new Kalshi series/categories, always verify
against a live curl first, and never trust generic/category-scoped market
listing as a source of single-game winner-market truth. When touching
orderbook parsing, check for `orderbook_fp` before assuming cents.
