---
name: Stage 2 data pipeline architecture
description: Seven-item Stage 2 build — event identity, mutex, prob schema, staleness, calibration, settlement worker, DB tables.
---

## What was built

**Item 1 — `gate_engine/event_identity.py`**
- `build_event_key(league, official_event_id, scheduled_start_utc, participants, settlement_market)` → SHA-256 hex[:16]
- Participants sorted before hashing so home/away order is irrelevant
- `build_event_key_from_row(row)` resolves from multiple field aliases
- `detect_event_status(event_meta)` → STATUS_SCHEDULED/POSTPONED/IN_PROGRESS/COMPLETED/CANCELLED/UNKNOWN + can_score
- `validate_slate_date_utc(scheduled_start_utc, target_date)` — replaces string-level slate_date matching
- `detect_duplicates(rows)` — finds rows with same event_key submitted twice

**Item 2 — `gate_engine/event_mutex.py`**
- `validate_event_mutex(rows)` — scans final-selection rows; any two rows sharing event_key with different non-empty sides → RUN_INVALID_OPPOSING_SIDES
- Mutates conflicting rows in-place: adds blockers entry + `_run_invalid=True`
- FINAL_SELECTION_LABELS: FINAL_APPROVED, MONEY_QUALIFIED, LLP_APPROVED, LLP_PLAYABLE

**Item 3 — `gate_engine/prob_ledger.py` extended**
- 7 Stage 2 required fields: raw_probability, calibrated_probability, lower_bound, upper_bound, model_timestamp, source_snapshot_id, calibration_method
- `_validate_stage2_schema(ledger_payload, row)` → complete + rank_eligible booleans
- `rank_eligible=True` only when all 7 fields present, numeric fields parse as float in (0,1), lower_bound ≤ upper_bound
- `run()` now sets `row["rank_eligible"]` directly and blocks with PROB_SCHEMA_INCOMPLETE

**Item 4 — `gate_engine/llp_governance.py` extended**
- New validator `validate_material_staleness(candidate)` added to ALL_GOVERNANCE_VALIDATORS between "reapproval" and "calibration_ledger"
- Automatic comparison: model_timestamp vs latest_material_update_at (both parsed as tz-aware datetimes)
- If model_timestamp < latest_material_update_at → STALE_MODEL_OUTPUT (ceiling: LLP_REJECT)
- If model_timestamp missing → NO_MODEL_TIMESTAMP (ceiling: LLP_WATCH)
- Legacy `material_change_flagged` boolean still checked as fallback when no timestamps present
- `_parse_ts` and `_fmt_delta` already existed in the file — reused

**Item 5 — `gate_engine/llp_governance.py` + `kalshi_engine/calibration_ledger.py`**
- `log_calibration_entry` now writes to Postgres `llp_calibration_ledger` (primary) with JSONL at `/tmp/llp_calibration_ledger.jsonl` as fallback
- `get_calibration_ledger` reads Postgres first, JSONL fallback
- `kalshi_engine/calibration_ledger._get_conn()` now has `connect_timeout=10`
- `_compute_log_loss(model_prob, outcome)` added — binary log loss, p clipped to [1e-7, 1-1e-7]
- `_probability_to_calibration_bucket(prob)` — canonical 5-bucket mapping: 52-55%, 55-60%, 60-65%, 65-70%, 70%+
- `calibration_bucket` column added to `kalshi_forecast_ledger` (migration via ALTER TABLE ADD COLUMN IF NOT EXISTS)
- `is_primary_observation` column added — set False when opposing side of same event_ticker already exists
- `_is_opposing_side_duplicate(conn, event_ticker, side_yes_no)` prevents both sides of a game from being double-counted
- `settle_result()` now computes and stores both brier_score AND log_loss
- `get_brier_score()` returns mean_log_loss, mean_log_loss_primary, primary_observations count
- `get_brier_score_by_bucket()` uses calibration_bucket (canonical), also returns mean_log_loss_primary

**Item 6 — `gate_engine/settlement_worker.py`**
- Background daemon thread; started via `start_settlement_worker()` called from app.py near _wnba_start_cron
- pg_try_advisory_lock key: 778597299 (differs from LLP cron key 778597203)
- Env: SETTLEMENT_WORKER_DISABLED=1 to turn off; SETTLEMENT_WORKER_INTERVAL_SEC (default 300)
- Two grading paths per tick: prop settlements (`llp_event_settlements`) and Kalshi (`kalshi_forecast_ledger`)
- Grades ONLY the selected_side — never the opposing side
- `_grade_open_prop_settlements` calls `ml_settlement_truth.reconcile_settlement()` for props
- `_grade_open_kalshi_settlements` calls `kalshi_engine.settlement_reconciliation.reconcile()` for Kalshi
- `_fetch_kalshi_resolution(market_ticker)` is READ-ONLY — never submits orders
- `get_worker_status()` endpoint-ready stats dict

**Item 7 — `gate_engine/llp_stage2_tables.py`**
- Seven tables: llp_source_snapshots, llp_research_runs, llp_events, llp_event_candidates, llp_event_decisions, llp_event_settlements, llp_calibration_ledger
- `ensure_all_tables()` creates all 7 in correct FK dependency order; guarded by module-level lock + CREATE IF NOT EXISTS
- Called from `_run_startup_warmup` in app.py (background daemon thread, non-fatal)
- `log_calibration_entry_pg(entry)` and `get_calibration_ledger_pg(limit)` are the new canonical Postgres read/write API for LLP prop calibration
- `probability_to_bucket()` and `compute_log_loss()` are public helpers shared with settlement_worker

## Why
- Ephemeral JSONL at /tmp is lost on every restart — calibration history was being discarded
- Both sides of a game being independently calibrated double-counts model observations
- Manual material_change_flagged boolean required human intervention; automated timestamp comparison is fail-safe
- No event_key meant opposing sides could both reach FINAL_APPROVED in one run without detection

## Hardening pass (8 items from external reviewer)

1. **`lower_bound <= calibrated_probability <= upper_bound`** — added cross-field check in `_validate_stage2_schema`; a calibrated estimate outside its own interval fails validation.
2. **Bool rejection** — `isinstance(val, bool)` checked before `float()` for all 4 numeric probability fields; `True`/`False` are now type violations, not silently accepted as 1.0/0.0.
3. **Participant normalization** — `_norm()` in `event_identity.py` now removes periods, strips all non-alphanumeric chars (except spaces), and collapses whitespace; `"St. Louis Blues"` and `"st louis blues"` produce the same key.
4. **Canonical `event_key` preferred for dedup** — `_check_and_lock_for_primary` prefers `event_key` (SHA-256) over `event_ticker` (external Kalshi ID that can be reused/derivative).
5. **Transaction-safe primary assignment** — `_check_and_lock_for_primary` uses `SELECT … FOR UPDATE` within the caller's transaction; concurrent inserts from different gunicorn workers for the same event are serialised.
6. **Idempotent settlement inserts** — `llp_event_settlements` has `UNIQUE (run_id, event_key, selected_side)` constraint; both UPDATE paths in `settlement_worker.py` add `AND settlement_status = 'OPEN'` + check `cur.rowcount > 0` to prevent re-grading.
7. **Health endpoint exposed** — `GET /wow/stage2/health` returns `schema_ready` (from `_TABLES_READY`), full worker stats including `last_success_tick` and `last_error`; `can_execute=false` always.
8. **`_run_invalid` downstream block** — `is_run_blocked(rows)` and `filter_valid_rows(rows)` added to `event_mutex.py`; docstring explicitly warns callers must abort the full run, not salvage unaffected rows.

## Hard requirement preserved
`can_execute = False` and `EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"` are present as module-level constants in all 4 new modules and enforced unconditionally in `settle_result`, `reconcile()`, and all settlement worker paths.
