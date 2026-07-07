---
name: Kalshi final-lock edge gate
description: Four required conditions for LLP_PLAYABLE on /wow/llp/kalshi/ml-evaluate; evaluate_stub() new gate params; test patterns.
---

## Rule

No Kalshi sports contract may advance beyond LLP_WATCH from
`/wow/llp/kalshi/ml-evaluate` unless ALL four pass simultaneously:
1. `kalshi_orderbook_source == "direct_api"` (Gate A) — only `orderbook_fetcher.fetch()` may produce this
2. `trading_active is True` (Gate B) — market must be open
3. `final_lock_rechecked_at` supplied and age ≤ 1800s (Gate C) — 30-min recheck window
4. Adjusted edge ≥ edge floor for market type (Step 5) — EDGE_FLOOR_MAIN=1.5% for KXMLBGAME/KXWNBAGAME, EDGE_FLOOR_DERIVATIVE=2.5% otherwise

`can_execute` is always False — DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS.

## evaluate_stub() new params (keyword, all have defaults)

```python
evaluate_stub(
    ...,
    market_type="derivative",          # or "main_winner" — from detect_market_type()
    trading_active=None,               # Gate B
    final_lock_rechecked_at=None,      # Gate C — ISO-8601 string or None
    kalshi_orderbook_source="no_ticker",  # Gate A
)
```

Return dict gains: `market_type`, `edge_floor`, `kalshi_orderbook_source`,
`trading_active`, `final_lock_fresh`, `final_lock_age_seconds`.

## Test pattern

Pre-existing tests that assert `label == "LLP_PLAYABLE"` MUST supply all three:
```python
kalshi_orderbook_source="direct_api",
trading_active=True,
final_lock_rechecked_at=datetime.now(tz=timezone.utc).isoformat(),
```
Without them, Gate A fires (`no_ticker` default → LLP_WATCH) and Gate C fires
(None → LLP_WATCH), so the test lands on LLP_SCOUT/LLP_WATCH instead.

## Modules

- `kalshi_engine/llp_bridge/orderbook_fetcher.py` — `fetch(ticker)`, `detect_market_type(ticker, series_ticker)`
- `kalshi_engine/llp_bridge/kalshi_watch_ledger.py` — `ensure_schema(conn)`, `log_candidate(conn, ...)`
- DB: `kalshi_candidate_ledger` table (lazy-created on first route call)

**Why:** Callers could previously supply web UI display prices as `raw_orderbook` and satisfy price gates off non-API data. Gate A closes that path entirely.
