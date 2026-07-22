---
name: gunicorn --preload threading.Lock deadlock
description: All module-level threading.Lock() instances must be re-initialized in a post_fork hook when gunicorn uses --preload, or workers inherit permanently-locked mutexes from the master process.
---

# gunicorn --preload + threading.Lock deadlock

## The rule
Any `threading.Lock()` created at module import time is vulnerable to a permanent deadlock when gunicorn runs with `--preload`. Re-create every lock in a `post_fork` hook.

**Why:** With `--preload`, gunicorn imports the entire app in the master process, starts daemon threads (warmup, cron, etc.), then forks workers. If a daemon thread *holds* a `threading.Lock` at the exact moment of `os.fork()`, the worker inherits the lock in a permanently-locked state. No thread in the worker can ever release it. Every `with lock:` call in the worker blocks forever until gunicorn fires WORKER TIMEOUT.

This was the confirmed root cause of all WORKER TIMEOUT 500s on `POST /run-connected-model`. The traceback showed `line N, in _cm_db` / `_cm_ensure_schema()` → stuck on `with _CM_SCHEMA_LOCK:`.

**How to apply:**
- Keep a `gunicorn_conf.py` at the artifact root with a `post_fork(server, worker)` function.
- Wire it into the gunicorn run command: `--config gunicorn_conf.py`.
- In `post_fork`: re-assign every module-level `threading.Lock()` to `threading.Lock()` (fresh instance).
- Also reset every `_SCHEMA_READY = False` so each worker performs its own idempotent DDL bootstrap.
- `CREATE TABLE IF NOT EXISTS` DDL is safe to run concurrently from multiple workers — PostgreSQL handles it.

## Locks re-initialized in this project (flask-scoring-api)
`_log_lock`, `_ESPN_PLAYER_SEARCH_LOCK`, `_FIXTURES_SCHEMA_LOCK`, `_FIXTURES_REFRESH_LOCK`, `_TENNIS_CSV_LOCK`, `_UMPIRE_SCHEMA_LOCK`, `_UMPIRE_POPULATE_LOCK`, `_LINES_SCHEMA_LOCK`, `_CLV_SCHEMA_LOCK`, `_CM_SCHEMA_LOCK`, `_LLP_POSTMORTEM_SCHEMA_LOCK`, `_LLP_PRO_SCHEMA_LOCK`, `_LLP_CRON_LOCK`, `_WNBA_CRON_LOCK`

## Ready flags reset in post_fork
`_FIXTURES_SCHEMA_READY`, `_UMPIRE_SCHEMA_READY`, `_LINES_SCHEMA_READY`, `_CLV_SCHEMA_READY`, `_CM_SCHEMA_READY`, `_LLP_POSTMORTEM_SCHEMA_READY`, `_LLP_PRO_SCHEMA_READY`
