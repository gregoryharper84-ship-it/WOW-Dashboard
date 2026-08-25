# WOW Architecture Decision Records

This directory contains Architecture Decision Records (ADRs) for non-obvious choices in the WOW scoring engine.

Each ADR follows the format:
- **Status:** Accepted / Superseded / Deprecated
- **Context:** What problem prompted this decision
- **Decision:** What was decided
- **Consequences:** What breaks if this is violated

---

## Index

| ADR | Title | Status |
|-----|-------|--------|
| ADR-001 | gunicorn `--preload` disabled | Accepted |
| ADR-002 | Enrichment keyed by player:prop then promoted to row_id | Accepted |
| ADR-003 | `python -m gunicorn` in production run command | Accepted |
| ADR-004 | `pg_try_advisory_lock` for shared-DB cron writes | Accepted |
| ADR-005 | nba_api lazy-init pattern | Accepted |

---

## ADR-001 — gunicorn `--preload` disabled

**Status:** Accepted

**Context:** gunicorn with `--preload` forks workers from a pre-loaded master process. Any `threading.Lock` instance created at module import time is inherited in a locked state by forked workers.

**Decision:** `--preload` is disabled. Workers start independently. All `threading.Lock` instances are created inside `post_fork` hooks or on first use.

**Consequences:** If `--preload` is re-enabled, every worker that inherits a locked mutex will hang permanently with WORKER TIMEOUT. This is not recoverable without a restart.

---

## ADR-002 — Enrichment keyed by player:prop then promoted to row_id

**Status:** Accepted

**Context:** `build_auto_enrichment` writes the full enrichment entry (game_log + all required sentinel fields) under `"jeremy peña:hits"` (player:prop format) because `row_id` is not known until after `normalize_board` runs. A later `_stamp_enrichment(row_id, ...)` call created a sparse `enrichment[row_id]` that shadowed the full player:prop entry.

**Decision:** `_check_prop_game_log` explicitly checks both `enrichment[row_id]` and `enrichment["player:prop"]`. When game_log is found via the player:prop key and not the rid key, the full entry is promoted: `enrichment[row_id] = {**_enr_by_pp, **_enr_by_rid}`. The rid entry wins on field conflicts.

**Consequences:** Any code that writes to `enrichment[row_id]` (e.g. `_stamp_enrichment`) BEFORE the promotion runs will create a sparse entry that shadows the full player:prop entry. The promotion block checks `not _enr_by_rid.get("game_log")` to detect this and merge.

---

## ADR-003 — `python -m gunicorn` in production run command

**Status:** Accepted

**Context:** Bare `gunicorn` command silently fails in the Replit deployment container — no output, no port bind, no error. The PATH in the deployment container does not include the gunicorn entry point.

**Decision:** All production run commands use `python -m gunicorn`. Dev server uses `flask run` or `python app.py`.

**Consequences:** If someone changes the run command to `gunicorn` without `-m`, the production deployment will silently fail to bind a port.

---

## ADR-004 — `pg_try_advisory_lock` for shared-DB cron writes

**Status:** Accepted

**Context:** gunicorn runs 2 workers. In-process cron jobs (e.g. quota table sync, settlement worker) run in each worker simultaneously. Without a lock, both workers attempt to write to shared tables, causing constraint violations or double-counting.

**Decision:** All shared-DB cron writes acquire `pg_try_advisory_lock(778597299)` before executing. If the lock is not obtained, the worker skips that cycle.

**Consequences:** Only one worker executes each cron cycle. If that worker crashes mid-cycle, the lock is released automatically on connection close. Advisory lock ID `778597299` is hardcoded — do not change without updating all callers.

---

## ADR-005 — nba_api lazy-init pattern

**Status:** Accepted

**Context:** Module-level `import nba_api` took 10.7 seconds due to player lookup table loading. This caused the health endpoint to return 500 during every server restart while the import was in progress.

**Decision:** All nba_api imports use a `_nba_ensure()` lazy-init function. No nba_api code runs at module import time.

**Consequences:** The first request that needs nba_api data will be slow (~10 s). All subsequent requests use the cached state. If any module imports nba_api at module level, restarts will time out on health checks.
