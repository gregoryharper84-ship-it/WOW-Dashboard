---
name: WOW-PATCH-2026-08-16 Production Repair
description: Six-condition RUN_PARTIAL_BACKEND_FAILURE fix + R2 active-fetch patch — key lessons on schema migration, acquisition orchestration, and accent-strip fallbacks.
---

## Rules

### Schema migration must run BEFORE index DDL on existing tables
`llp_stage2_tables.ensure_all_tables()` must call `run_provenance_migration(conn)` at the TOP of the function, before the `for ddl in [...]` loop. Any index referencing a column added by the migration (e.g. `freshness_status`) crashes on pre-existing tables that lack the column.

**Why:** `CREATE TABLE IF NOT EXISTS` is a no-op when the table exists. Columns added in later patches are absent. `CREATE INDEX ... ON tbl (new_col)` then fails.

**Where:** `gate_engine/llp_stage2_tables.py` `ensure_all_tables()`.

---

### ExposureLedger guards belong INSIDE check_and_register, not in the pipeline skip loop
The pipeline's post-scoring loop populates `row["gates"]["exposure_gate"]`. Skipping a row at the loop level prevents that dict entry, breaking downstream test assertions. Any per-row exception (e.g. DATA_CONTRACT_FAIL should not consume slots) must be handled INSIDE `ExposureLedger.check_and_register()`, setting `registered=False + skipped_reason=...` while still populating the gates dict.

**Where:** `gate_engine/exposure_gate.py`.

---

### Acquisition orchestrator must actively FETCH, not just check
The acquisition orchestrator's `_check_prop_game_log` was advisory-only — it checked whether game_log was present but never fetched it. This caused `l5_l10_ledger.run()` to receive `game_log=None` and record `direct_game_log_feed=NOT_CALLED`.

**Fix:** After player_id is resolved, call `_attempt_game_log_fetch()` which writes `game_log`/`l5_values`/`l10_values` into `enrichment[row_id]`.

**Write to `enrichment[row_id]` not `enrichment["player:prop_type"]`** — the pipeline's `_get_enrichment()` tries `enrichment[rid]` first; writing to `row_id` avoids write-key mismatches when `normalize_board()` changes prop_type.

---

### MLB Stats API /people/search silently returns empty for accented names
`urllib.parse.quote("Jeremy Peña")` → `"Jeremy%20Pe%C3%B1a"`. The MLB Stats API endpoint `/api/v1/people/search?names=Jeremy%20Pe%C3%B1a` returns `{"people": []}` silently. Fix: strip Unicode combining marks via NFD decomposition and retry with ASCII name.

```python
import unicodedata
ascii_name = "".join(
    c for c in unicodedata.normalize("NFD", player_name)
    if unicodedata.category(c) != "Mn"
)
```

**Where:** `gate_engine/acquisition_orchestrator._resolve_mlb_player_id` and `gate_engine/auto_enrichment._lookup_mlb_player_id`.

---

### BallDontLie client uses `fetch_all(url, params, max_pages, per_page)`, not `bdl_get`
`gate_engine/balldontlie/client.py` exports `fetch_all`, `BDLResponse`, `BDLStatus`, etc. There is no `bdl_get`. `fetch_all` returns a `BDLResponse` with `.ok`, `.data`, `.meta`, `.raw`.

---

### Odds API quota table sync: ensure before background warmup, not inside it
Call `ensure_table_exists()` synchronously in the main process before starting the warmup thread. Otherwise the first request races with the thread and `fetch_quota_snapshot()` marks `degraded=True`.

**Where:** `app.py` just before `_threading.Thread(target=_run_startup_warmup, ...)`.

---

### pytest `patch()` vs direct attribute replacement for local imports
When a function imports a module locally (e.g. `import urllib.request` inside a closure), `patch("urllib.request.urlopen")` works in isolation but can fail in a full pytest run due to test isolation. Direct attribute replacement is reliable:

```python
orig = urllib.request.urlopen
urllib.request.urlopen = mock_fn
try:
    result = func_under_test()
finally:
    urllib.request.urlopen = orig
```
