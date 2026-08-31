---
name: Flask scoring API — gunicorn workers & in-process crons
description: Prod runs 2 gunicorn workers, so any in-process background thread runs twice; how to make shared-DB writes safe.
---

# In-process background work runs once PER gunicorn worker

`artifacts/flask-scoring-api` runs in **dev** as a single `python app.py`
process, but in **prod** via gunicorn with `--workers 2` (see
`.legacy platform-artifact/artifact.toml`). Anything started at import time — daemon
threads, pollers, cron loops — therefore runs **once per worker** in prod (2×),
each on its own DB connection.

**Why this matters:** a naive check-then-insert background job (read "does row X
exist?", then insert if not) is safe in dev's single process but races in prod:
both workers read "missing" and both insert → duplicate rows, double-counted
one-time records, duplicate derived rows (e.g. CLV).

**How to apply — serialize ticks with a Postgres advisory lock:**
- At the top of each tick, `SELECT pg_try_advisory_lock(<stable_bigint_key>)`.
  If it returns false, another worker/instance is already ticking → return early
  and skip this tick.
- Do the work, `commit`, then in `finally` `SELECT pg_advisory_unlock(<key>)`
  and commit. Advisory locks are **session-scoped**, so they survive a data
  `commit()` mid-tick — only an explicit unlock or session end releases them.
- This makes cross-worker check-then-insert safe without needing partial unique
  indexes. The same pattern protects any future in-process scheduler here.

**More cron gotchas hit on the odds-snapshot cron (Step 3):**
- A CLV/derived-row computed at the end of a tick must read its anchor on the
  **same connection** as the inserts it depends on — a second connection can't
  see the current tick's uncommitted rows, so it silently drops the derived row.
- Parse int env vars (intervals, windows) through a try/except fallback; a bad
  env value in a module-level `int(os.environ[...])` crashes app startup, which
  violates the "background work must never block startup" rule.
- **Opening-anchor fabrication trap:** when you derive an "opening" line from a
  same-day snapshot table AND insert the "close" row in the same pass, an
  earliest-row-today query will return the close row you just wrote when no real
  prior history exists → a fabricated FLAT/zero CLV. Gate on whether genuine
  prior-tick history existed (e.g. `bool(preloaded_kinds_for_this_market)`
  captured *before* mutating it). If none, write an explicit `INCOMPLETE` CLV
  row (opening/delta/beat NULL) rather than grading. **Why:** "feed started after
  the market opened" must be recorded as incomplete, never back-filled into a
  real beat. Note the analyze path also writes `odds_snapshots` rows with
  `snapshot_kind='current'` and no `first_seen`, so gate on *any* prior kind, not
  only `first_seen`.
