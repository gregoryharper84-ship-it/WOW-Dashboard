# WOW-PATCH-003: Price-Source and Staleness Gate

**Status:** SHIPPED  
**Patch ID:** WOW-PATCH-003-WEATHER-PRICE-STALENESS-GATE  
**Depends on:** WOW-PATCH-001, WOW-PATCH-002  
**Implemented:** 2026-07-01  
**Acceptance tests:** TF-WX-11 through TF-WX-13, TF-WX-19, TF-WX-20

---

## Problem

PATCH-001 accepted any `yes_price` from the caller without checking whether it
came from a live Kalshi order book or was fabricated for testing. A synthetic
price (e.g. `yes_price: 0.35`) on a FINAL CLI bracket could produce a large
apparent edge (0.65) with no real trading opportunity — the market would already
be settled. There was also no mechanism to distinguish "this is a real signal"
from "this is a test payload."

---

## Solution

Add a price-source classification layer and a multi-step staleness gate that
must pass before `KALSHI_PLAYABLE_LIMIT_ONLY` can be emitted.

### New functions

| Function | Description |
|---|---|
| `_fetch_kalshi_nhigh_prices(series, date_str, brackets)` | Searches Kalshi for live NHIGH bracket markets; maps bracket labels to market prices via subtitle/ticker matching |
| `_apply_weather_price_gate(price_source, price_timestamp, report_status, kalshi_prices)` | Multi-step gate — returns `can_trade`, `can_execute`, `adjusted_terminal_label`, `trade_block_reason` |
| `_weather_terminal_label_v2(weather_label, brackets_scored, price_gate)` | Updated label mapper with full terminal label set and gate override |

---

## Price-source classification

| `price_source` value | Meaning |
|---|---|
| `kalshi_live_orderbook` | Fetched live from Kalshi search_markets; timestamp recorded |
| `operator_supplied` | Caller provided yes_prices but live fetch failed or wasn't attempted |
| `synthetic_test` | Caller explicitly declared `"price_source": "synthetic_test"` |
| `not_found` | No prices at all — live fetch failed and no caller prices |

The request field `price_source` may be set to `"synthetic_test"` or
`"operator_supplied"` to skip the live fetch. Omitting it causes the endpoint
to attempt live Kalshi price discovery automatically.

---

## Gate rules

### Gate 1 — Non-live price sources

If `price_source ∈ {synthetic_test, operator_supplied}`:
- `can_trade = false`
- `can_execute = false`
- `adjusted_terminal_label = KALSHI_WATCH` (caps PLAYABLE)

### Gate 2 — FINAL CLI + no live book

If `report_status == FINAL` AND `price_source != kalshi_live_orderbook`:
- `can_trade = false`
- `can_execute = false`
- `adjusted_terminal_label = KALSHI_DATA_UNOBTAINABLE`
- `trade_block_reason = "CLI FINAL — tradeable window closed or price not live."`

### Gate 3 — Live fetch failed

If `price_source == not_found` (live fetch returned no markets):
- `can_trade = false`
- `can_execute = false`
- `adjusted_terminal_label = KALSHI_DATA_UNOBTAINABLE`

### Gate 4 — Live orderbook path

If `price_source == kalshi_live_orderbook`:
- Staleness check: `price_age_minutes > 10` → `KALSHI_DATA_UNOBTAINABLE`
- Market open check: `market_status != open` → `KALSHI_REJECT_BAD_RULES`
- If fresh + open: `can_trade = false`, `can_execute = false` (DRY_RUN_ONLY always)
- No `adjusted_terminal_label` — normal scoring path proceeds

### DRY_RUN_ONLY invariant

`execution_rule = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"` is returned
in every response and `can_execute` is never `true`, regardless of price source
or gate path.

---

## Terminal label set (complete)

| Label | Meaning |
|---|---|
| `KALSHI_PLAYABLE_LIMIT_ONLY` | Both PATCH-002 + PATCH-003 gates pass; edge ≥ 10pp |
| `KALSHI_WATCH` | Watchlist only — WATCH/SCOUT horizon, synthetic price, or weak edge |
| `KALSHI_REJECT_NO_EDGE` | Model sees no edge |
| `KALSHI_REJECT_BAD_RULES` | Market rules block execution (closed, bad status) |
| `KALSHI_REJECT_THIN_BOOK` | Insufficient order book liquidity |
| `KALSHI_REJECT_FEE_DRAG` | Spread/fee destroys edge |
| `KALSHI_REJECT_UNCALIBRATED` | Model not calibrated for this scenario |
| `KALSHI_DATA_UNOBTAINABLE` | Data cannot be fetched or is post-settlement without live book |

`WEATHER_*` labels are internal only and never appear as `terminal_label` (TF-WX-24).

---

## New response fields

| Field | Description |
|---|---|
| `price_source` | Classified source (TF-WX-13) |
| `price_timestamp` | ISO UTC timestamp of live price fetch, or null |
| `market_status` | `"open"` / `"closed"` / null from Kalshi search |
| `orderbook_source` | Raw `price_source` from the fetch result |
| `live_orderbook_checked` | Whether live Kalshi fetch was attempted |
| `price_age_minutes` | Minutes since price was fetched (staleness gate) |
| `can_trade` | Always false — DRY_RUN_ONLY |
| `can_execute` | Always false — DRY_RUN_ONLY |
| `execution_rule` | `"DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"` |
| `trade_block_reason` | Human-readable reason why can_execute is false |

---

## Live Kalshi price integration

`_fetch_kalshi_nhigh_prices` uses `kalshi_client.search_markets` to find open
NHIGH bracket markets matching the city series and event date:

1. Search with `series_ticker=<series>, status="open", limit=50`
2. If empty, fall back to global search filtered by ticker prefix
3. Filter to event date by matching `date_short` (e.g. `26JUL01`) or `date_digits` in ticker
4. Match each bracket label to a market by threshold value in subtitle or `T<N>` in ticker
5. If any bracket prices found → `price_source = kalshi_live_orderbook`
6. Merge live `yes_ask` into scored brackets as `live_yes_ask` + `live_raw_edge`

If the Kalshi API returns no results (no open markets for that series/date),
`price_source` falls back to `operator_supplied` or `not_found`.

---

## Acceptance test results

| Test | Assertion | Result |
|---|---|---|
| TF-WX-11 | FINAL CLI → `can_trade=false` without live book | PASS |
| TF-WX-12 | FINAL CLI + synthetic/operator → never KALSHI_PLAYABLE_LIMIT_ONLY | PASS |
| TF-WX-13 | `price_source` tagged; synthetic/operator non-executable | PASS |
| TF-WX-19 | Live orderbook unavailable → `can_execute=false`, terminal ≤ KALSHI_WATCH | PASS |
| TF-WX-20 | DRY_RUN_ONLY enforced in every response | PASS |
| TF-WX-24 | No WEATHER_* labels in terminal_label | PASS |

---

## Invariants preserved

- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS` enforced unconditionally
- Station map unchanged: KMDW / KMIA / KLAX / KNYC / KAUS
- No new live execution paths opened
- `KALSHI_PLAYABLE_LIMIT_ONLY` requires all of: WEATHER_MODEL_READY + live orderbook
  + price_age ≤ 10m + market open + edge ≥ 10pp + not DRY_RUN (which is always active)
