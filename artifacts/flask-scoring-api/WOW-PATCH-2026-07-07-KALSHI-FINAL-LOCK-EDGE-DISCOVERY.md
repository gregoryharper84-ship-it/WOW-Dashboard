# WOW-PATCH-2026-07-07-KALSHI-FINAL-LOCK-EDGE-DISCOVERY

## Patch ID
`WOW-PATCH-2026-07-07-KALSHI-FINAL-LOCK-EDGE-DISCOVERY`

## Author / date
User (spec + governance rule) + legacy platform agent — 2026-07-07

## Status
`SHIPPED`

---

## 1. Problem statement

Before this patch the `/wow/llp/kalshi/ml-evaluate` route accepted
`raw_orderbook` + `orderbook_timestamp_utc` directly from the caller as its
price source. A caller could supply web UI / display prices (not executable
orderbook data), attach a plausible-looking timestamp, and potentially advance
through price-related gates off non-API data. There was also no check that the
Kalshi market was actually open and trading (a closed or settled market has no
executable price), no final-lock recheck freshness requirement before advancing
beyond LLP_WATCH, and a single 2.5% edge floor regardless of market type.

The governance rule requested in the spec:
> **No Kalshi sports contract may advance beyond LLP_WATCH unless fresh direct
> Kalshi orderbook data, sportsbook no-vig consensus, final-lock recheck,
> market trading_active status, and edge threshold all pass.**

## 2. Affected spec sections

| Section | Change type | Description |
|---------|-------------|-------------|
| §34 (new) — Kalshi Sports Final-Lock Edge Gate | ADD | Governance rule, 3 new gates (orderbook source, market status, final-lock recheck), market-type edge floors, candidate ledger |
| §7 — Field contract | ADD | 6 new response fields on `/wow/llp/kalshi/ml-evaluate`; `kalshi_candidate_ledger` table |

## 3. Exact delta

### New file — `kalshi_engine/llp_bridge/orderbook_fetcher.py`
- `fetch(ticker, depth=10)` — pulls live orderbook from Kalshi API and
  simultaneously calls `get_market_status()` to extract `status`/
  `trading_active`. Returns a result dict including `kalshi_orderbook_source`
  (`"direct_api"` on success, `"fetch_failed"` on error),
  `orderbook_timestamp_utc` (set to call time), and `trading_active`.
  This is the ONLY code path that may tag source as `"direct_api"`.
- `detect_market_type(ticker, series_ticker)` — returns `"main_winner"` for
  KXMLBGAME-*/KXWNBAGAME-* series, `"derivative"` for everything else.

### New file — `kalshi_engine/llp_bridge/kalshi_watch_ledger.py`
- `ensure_schema(conn)` — idempotent CREATE TABLE IF NOT EXISTS for
  `kalshi_candidate_ledger` + indexes.
- `log_candidate(conn, ...)` — INSERT per evaluate call; never raises (logging
  failure must not break the primary evaluation flow).
- All labels logged (WATCH, PLAYABLE, SCOUT, REJECT) — WATCH candidates are
  first-class log entries for CLV tracking, not filtered out.

### `kalshi_engine/llp_bridge/ml_evaluate.py`
- New constants: `EDGE_FLOOR_MAIN = 0.015`, `EDGE_FLOOR_DERIVATIVE = 0.025`,
  `FINAL_LOCK_WINDOW_SECONDS = 1800` (30 min).
  `EDGE_FLOOR = EDGE_FLOOR_DERIVATIVE` alias retained for backward compat.
- New parameters to `evaluate_stub()`:
  `market_type`, `trading_active`, `final_lock_rechecked_at`,
  `kalshi_orderbook_source` (all keyword-only with safe defaults).
- **Gate A** — `kalshi_orderbook_source != "direct_api"` → cap LLP_WATCH +
  blocker `KALSHI_ORDERBOOK_SOURCE_NOT_DIRECT_API`.
- **Gate B** — `trading_active is False` → cap LLP_SCOUT + blocker
  `MARKET_NOT_TRADING`.
- **Gate C** — `final_lock_rechecked_at` absent or age > 1800s → cap LLP_WATCH
  + blocker `FINAL_LOCK_RECHECK_REQUIRED`.
- Step 5 now uses `edge_floor` (market-type-selected) instead of the module-
  level `EDGE_FLOOR` constant.
- Return dict gains: `market_type`, `edge_floor`, `kalshi_orderbook_source`,
  `trading_active`, `final_lock_fresh`, `final_lock_age_seconds`.

### `app.py` — `POST /wow/llp/kalshi/ml-evaluate`
- Imports `orderbook_fetcher` and `kalshi_watch_ledger`.
- Accepts `final_lock_timestamp_utc` from request body.
- After ticker mapping, calls `orderbook_fetcher.fetch(matched_ticker)` for the
  live orderbook — source is always "direct_api". If `raw_orderbook` is supplied
  by the caller without a matched ticker, it is tagged "caller_supplied" (Gate A
  caps the row). The `raw_orderbook` body field is NOT the authority any more.
- `trading_active` comes from the fetcher's `get_market_status()` call.
- `market_type` detected from `detect_market_type(ticker, series_ticker)`.
- All new params passed to `evaluate_stub()`.
- `kalshi_watch_ledger.log_candidate()` called after every evaluation.
- New top-level response fields: `kalshi_orderbook_source`, `market_type`,
  `market_status`, `trading_active`, `final_lock_fresh`, `ledger_row_id`.
- `execution_rule` string updated:
  `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS`.

### DB — `kalshi_candidate_ledger` table (new)
```sql
CREATE TABLE IF NOT EXISTS kalshi_candidate_ledger (
    id                       BIGSERIAL PRIMARY KEY,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    scan_date                DATE        NOT NULL DEFAULT CURRENT_DATE,
    sport                    TEXT,
    home_team                TEXT,
    away_team                TEXT,
    ticker                   TEXT,
    event_ticker             TEXT,
    market_title             TEXT,
    market_type              TEXT,
    market_status            TEXT,
    trading_active           BOOLEAN,
    kalshi_orderbook_source  TEXT,
    orderbook_age_seconds    NUMERIC,
    staleness_grade          TEXT,
    scan_price               NUMERIC,
    no_vig_probability       NUMERIC,
    model_probability        NUMERIC,
    adjusted_edge            NUMERIC,
    edge_floor               NUMERIC,
    label                    TEXT,
    blocker_tags             TEXT[],
    final_lock_checked_at    TIMESTAMPTZ,
    final_lock_fresh         BOOLEAN,
    closing_price            NUMERIC,       -- null at scan time
    settlement_result        TEXT,          -- null at scan time
    clv_beat                 BOOLEAN,       -- null at scan time
    notes                    TEXT
);
```
Created lazily on first `/wow/llp/kalshi/ml-evaluate` call via
`ensure_schema()`. Existing tables untouched.

## 4. Test case

```bash
# Baseline — no candidate_markets, no final_lock_timestamp_utc
# → Gate A (no_ticker), Gate C (no timestamp) both fire → LLP_WATCH
curl -s -X POST "http://localhost:80/wow/llp/kalshi/ml-evaluate" \
  -H "X-API-Key: $SCORING_API_KEY" -H "Content-Type: application/json" \
  -d '{
    "llp_home_team": "Atlanta Braves",
    "llp_away_team": "Colorado Rockies",
    "llp_sport": "MLB",
    "model_probability": 0.62
  }' | python3 -m json.tool | grep -E '"label"|"kalshi_orderbook_source"|"final_lock_fresh"|"can_execute"'
# Expected:
# "label": "LLP_WATCH",          ← Gate A or C fires
# "kalshi_orderbook_source": "no_ticker",
# "final_lock_fresh": false,
# "can_execute": false

# Caller-supplied orderbook — still Gate A, still LLP_WATCH
curl -s -X POST "http://localhost:80/wow/llp/kalshi/ml-evaluate" \
  -H "X-API-Key: $SCORING_API_KEY" -H "Content-Type: application/json" \
  -d '{
    "llp_home_team": "Atlanta Braves", "llp_away_team": "Colorado Rockies",
    "llp_sport": "MLB", "model_probability": 0.62,
    "raw_orderbook": {"orderbook": {"yes": [{"price": 60, "quantity": 300}],
                                    "no":  [{"price": 38, "quantity": 300}]}},
    "orderbook_timestamp_utc": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"
  }' | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['evaluation']['label'], d['kalshi_orderbook_source'])"
# Expected: LLP_WATCH caller_supplied
```

## 5. Conflict check

| Question | Answer |
|----------|--------|
| Does this change any existing badge/ceiling rule? | Adds three new caps (Gates A/B/C) that run BEFORE all existing gates. Pre-existing gate behavior (inventory, settlement, consensus, fee, staleness, edge) unchanged — but the edge floor is now market-type-aware instead of always 2.5%. All pre-existing tests still pass. |
| Does this add, rename, or remove a top-level field from the field contract? | ADD 6 new response fields on `/wow/llp/kalshi/ml-evaluate` and the `evaluate_stub` return dict; ADD `kalshi_candidate_ledger` table. `execution_rule` string updated. No existing field removed or renamed. |
| Does this change the set of hard vs. advisory failure-path tags? | Adds 3 new blocker tags: `KALSHI_ORDERBOOK_SOURCE_NOT_DIRECT_API`, `MARKET_NOT_TRADING`, `FINAL_LOCK_RECHECK_REQUIRED`. All are hard blockers (ceiling, not advisory). |
| Does this alter `_llp_decision` logic or its input thresholds (§3)? | No — this patch is scoped to `kalshi_engine/llp_bridge/ml_evaluate.py` and the `/wow/llp/kalshi/ml-evaluate` route. `_llp_decision` and `gate_engine/llp_governance.py` are untouched. |
| Does this change any Odds API market alias or sport-key mapping (§5, §8)? | No. |
| Does this affect the odds-snapshot cron, snapshot kinds, or CLV grading (§11)? | `kalshi_candidate_ledger` adds settlement/CLV tracking at scan time (null at first insert); closing_price/settlement_result/clv_beat are filled in at settle time via a future endpoint. The existing `clv_log` and `llp_postmortem` tables are untouched. |
| Does this require a DB migration? | YES — new `kalshi_candidate_ledger` table, created lazily via `ensure_schema()` on first call (non-destructive, no ALTER on existing tables). |
| Does this add a new route? | No — reuses the existing `/wow/llp/kalshi/ml-evaluate` route with updated behavior. No new Express proxy forwarding needed. |
| Could gunicorn's 2-worker setup cause a race condition? | `ensure_schema()` uses a module-level lock + flag (same pattern as `_ensure_llp_postmortem_schema`); concurrent workers that both hit the lazy init call the same idempotent DDL — safe. |

## 6. Ground-truth doc update

Added `## §34 — Kalshi Sports Final-Lock Edge Gate` to `LLP_GROUND_TRUTH.md`,
documenting the governance rule, the three new gates (A/B/C), market-type edge
floors, `orderbook_fetcher`, `kalshi_watch_ledger`, and all new field contracts.

---

## Notes

- `LLP_APPROVED` remains never reachable from this endpoint (unchanged
  architectural decision — requires full session-scoped governance rerun).
- The "final-lock recheck" concept here is stateless: the caller supplies
  `final_lock_timestamp_utc` (ISO-8601) representing when they last ran a
  final-lock evaluation on this candidate. This is verifiable and audit-logged
  in `kalshi_candidate_ledger.final_lock_checked_at`. A future improvement could
  look up the ledger directly instead of relying on the caller's supplied
  timestamp.
- Edge floor for WNBA is treated the same as MLB main winner (1.5%) since both
  use KXWNBAGAME/KXMLBGAME series. Any WNBA derivative market (if Kalshi creates
  one) would use the 2.5% derivative floor automatically via `detect_market_type`.
