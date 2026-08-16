---
name: WOW-PATCH-2026-08-16 Production Repair
description: Six-condition RUN_PARTIAL_BACKEND_FAILURE fix — lessons on schema migration ordering, exposure ledger guards, and acquisition orchestration.
---

## Rules

**Schema migration must run BEFORE index DDL on existing tables.**
`llp_stage2_tables.ensure_all_tables()` previously ran `run_provenance_migration` AFTER the DDL loop. Any index referencing a column added by the migration (e.g. `freshness_status`) would crash on pre-existing tables that lacked the column. Fix: call `run_provenance_migration(conn)` at the TOP of `ensure_all_tables()` before the `for ddl in [...]` loop.

**Why:** `CREATE TABLE IF NOT EXISTS` is a no-op when the table exists. Columns added in later patches are absent. `CREATE INDEX IF NOT EXISTS ... ON tbl (new_col)` then fails. The migration must run first.

**How to apply:** Any time new columns are added to an existing Stage 2 table AND indexes are created on those columns in the same DDL string, ensure the ADD COLUMN migration runs before the index DDL. See `gate_engine/llp_stage2_tables.py` `ensure_all_tables()`.

---

**ExposureLedger guards belong INSIDE check_and_register, not in the pipeline skip loop.**
The pipeline's post-scoring loop (`for row in rows: if terminal_label in SKIP: continue; ledger.check_and_register(row)`) doubles as the gate that populates `row["gates"]["exposure_gate"]`. Skipping a row at the loop level prevents the gates dict entry from being set, which breaks downstream test assertions and gate-output completeness invariants.

**Why:** `ExposureLedger.check_and_register()` is both the gate runner AND the ledger registration in one call. Any per-row exception (e.g. DATA_CONTRACT_FAIL should not consume slots) must be handled INSIDE the method, which can set `registered=False + skipped_reason=...` while still populating the gates dict.

**How to apply:** To skip registration for a specific label, add the guard at the top of `ExposureLedger.check_and_register()` in `gate_engine/exposure_gate.py`, not in the pipeline iteration loop.

---

**BallDontLie client uses `fetch_all(url, params, max_pages, per_page)`, not `bdl_get`.**
The `gate_engine/balldontlie/client.py` module exports `fetch_all`, `BDLResponse`, `BDLStatus`, `BDLTier`, `credentials_available`, `detect_tier`, `endpoint_available`, `endpoint_available_for_tier`, `timezone`. There is no `bdl_get`. `fetch_all` returns a `BDLResponse` with `.ok` (bool), `.data` (list), `.meta` (dict|None), `.raw` (dict).

**How to apply:** Always use `fetch_all(url, params=..., max_pages=1)` for single-page lookups. Mock as `BDLResponse(status=BDLStatus.OK, endpoint=..., data=[...], meta=None, raw={})`.

---

**Odds API quota table sync: ensure before background warmup, not inside it.**
The background warmup thread races with the first incoming request. If the request arrives before the thread creates the quota table, `fetch_quota_snapshot()` fails and marks `degraded=True`. Fix: call `ensure_table_exists()` synchronously (in the main process, at module load time) before starting the warmup thread.

**Where:** `app.py` just before `_threading.Thread(target=_run_startup_warmup, ...)`.
