---
name: Kalshi category-scan architecture
description: GET /wow/kalshi/category-scan — category-router / singles-governor pipeline structure and key constraints
---

## Route
`GET /wow/kalshi/category-scan` — orchestrator in app.py (inserted before /wow/wnba/ingestion/health)

## Five new modules
- `kalshi_engine/category_router.py` — pure classify; priority: combo → economics → weather → sports_winner → disabled
- `kalshi_engine/weather_gate.py` — 12 gates; gate 12 portfolio_check_passed pre-set to True by orchestrator, governor is binding
- `kalshi_engine/sports_gate.py` — 9 gates; uses consensus_odds + fee_calc, not evaluate_stub (stateless)
- `kalshi_engine/portfolio_governor.py` — Recovery Mode caps + 8-key ranking; final pool max 2, max 1/event
- `kalshi_engine/category_scan_ledger.py` — extends kalshi_candidate_ledger via idempotent ALTER TABLE ADD COLUMN IF NOT EXISTS; also creates kalshi_category_scan_log

## Key constraints
- Gate 12 of weather_gate (portfolio_check_passed) is pre-set True by orchestrator; portfolio_governor.run() is the binding final enforcement
- Economics → RESEARCH_LANE_NOT_BUILT, never receives a probability
- Disabled categories → CATEGORY_DISABLED_OR_UNSUPPORTED, never fall through to generic modeling
- Combo (mve_collection_ticker OR underlying_count≥2) → KALSHI_REJECT_COMBO_DISABLED
- Sports upset (calibrated_prob_lower_bound < 0.65) → UPSET_REJECTED at gate 5 regardless of edge
- High-prob favorite with net_edge_lower_bound ≤ 0 → EDGE_BELOW_FLOOR at gate 9 unconditionally
- Non-direct_api orderbook source → KALSHI_ORDERBOOK_SOURCE_NOT_DIRECT_API at sports gate 8
- WEATHER_WATCH / WEATHER_SCOUT → blocked at weather gate 1 (WEATHER_WATCH_NOT_ELIGIBLE)
- can_execute=False and dry_run_only=True on all response paths

## Test file
`kalshi_engine/tests/test_category_scan.py` — 30 tests, no network, runs in 0.33s
