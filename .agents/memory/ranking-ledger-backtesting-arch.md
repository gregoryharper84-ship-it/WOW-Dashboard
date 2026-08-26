---
name: Ranking, ledger, backtesting, and source-health architecture
description: Five new gate_engine modules + 11 Flask routes + 4 dashboard pages for the WOW v16 multi-sport prop ranking engine.
---

## Modules (all in gate_engine/)

| File | Purpose | Key invariant |
|---|---|---|
| `prediction_ledger.py` | Immutable write-once `wow_prop_predictions` + `wow_prop_outcomes` tables | `can_execute=False`; write_prediction returns UUID |
| `settlement_audit.py` | Brier=(p−o)², log-loss, CLV=cal_prob−close_mkt; lower-bound reliability | `_brier`, `_log_loss`, `_outcome_value` are standalone pure functions |
| `cross_sport_ranker.py` | Four lanes: highest CLB, highest cal_prob, best edge, best multi-leg | `can_execute=False`; `auto_execute=False`; `requires_human_confirm=True`; weakest-leg eliminated at CLB<0.50 |
| `source_health_monitor.py` | `wow_source_health_log` table; 10 named sources; `aggregate_health_summary()` | `can_execute=False` |
| `backtesting.py` | Four modes: CALIBRATION, CLB_RELIABILITY, SPORT_SLICE, LABEL_AUDIT | `can_execute=False`; offline only |

## Flask routes added to app.py (before `if __name__ == "__main__":`)

- `GET  /wow/rankings` — from_db() with sport/since_date/top_n/multi_leg params
- `POST /wow/rankings/score` — in-memory rank() on submitted rows (no DB write)
- `GET  /wow/predictions` — read_predictions with sport/since_date/label/min_lb/limit
- `POST /wow/predictions` — write_prediction (single dict or `{rows:[...]}`)
- `GET  /wow/predictions/<id>` — read_prediction by UUID
- `POST /wow/predictions/<id>/settle` — write_outcome
- `GET  /wow/calibration/summary` — calibration_summary + batch_compute_metrics
- `GET  /wow/source-health` — aggregate_health_summary + best-effort internal probes
- `POST /wow/source-health/probe` — record_probe manually
- `POST /wow/backtest/run` — run_backtest(mode, days, sport)
- `GET  /wow/backtest/modes` — describe available modes

## Dashboard pages added to artifacts/final-lock/src/pages/

- `rankings.tsx` — Four-lane view (hit prob / cal prob / edge / multi-leg); PropCard + MultiLegCard
- `history.tsx` — Expandable prediction rows with three-state breakdown + probability fields
- `source-health.tsx` — Status grid grouped by DOWN/DEGRADED/OK/UNKNOWN; auto-refreshes 30s
- `backtesting.tsx` — Mode selector + results display (calibration chart, CLB table, sport table, label table)

## App.tsx nav rail

10 nav items: Final Lock · Rankings · Predictions · Backtesting · Source Health · Props · Prompt · Kalshi · Logs · Leaderboard.
Routes: `/rankings`, `/history`, `/backtest`, `/health` added alongside existing 6.

## Acceptance tests

`tests/test_acceptance.py` — 59 tests (6 skipped) covering all 25 spec ATs + additional invariant tests.
Full suite: 217 passed, 6 skipped.

## Key patterns

- `_ensure_ledger_tables(conn)` calls `prediction_ledger.ensure_tables()` + `source_health_monitor.ensure_table()` — idempotent; called at route entry.
- `cross_sport_ranker.rank([])` returns a `RankResult` with `.to_dict()` always including `can_execute: false`.
- Multi-leg same-player detection: `SAME_PLAYER_DUPLICATE` in `dependence_flags`.

**Why:** Needed immutable prediction history, settlement audit, CLB reliability tracking, and cross-sport prop ranking for WOW v16 master spec acceptance.
