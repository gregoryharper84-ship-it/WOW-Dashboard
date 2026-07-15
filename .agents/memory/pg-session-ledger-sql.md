---
name: PgSessionLedger SQL pattern
description: PostgreSQL-backed session exposure ledger for wow_session_exposure table — atomic locking, TTL, fail-closed, and the f-string INTERVAL pitfall.
---

## Schema

```sql
CREATE TABLE IF NOT EXISTS wow_session_exposure (
    session_id   TEXT        NOT NULL,
    exposure_key TEXT        NOT NULL,  -- "player:{name}", "game:{game}", "arch:{arch}"
    count        INTEGER     NOT NULL DEFAULT 0,
    expires_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (session_id, exposure_key)
);
CREATE INDEX IF NOT EXISTS idx_wse_expires ON wow_session_exposure (expires_at);
```

File: `gate_engine/pg_session_ledger.py`, called once at app startup via `ensure_table_exists()`.

## Atomic check-and-register pattern

```
BEGIN (implicit with psycopg2 context manager)
  INSERT (session_id, key, count=0, expires_at) ON CONFLICT DO NOTHING   -- initialise
  DELETE expired rows for this session                                     -- housekeeping
  SELECT count FROM ... WHERE session_id=? AND key=ANY([...]) FOR UPDATE  -- lock + read
  check limits
  if ok: UPDATE count+1 and refresh expires_at for each key
COMMIT
```

The `FOR UPDATE` lock prevents two concurrent workers from both passing the check and both registering, which would allow a duplicate through.

## INTERVAL literal — use f-string, NOT % dict formatting

**WRONG:**
```python
cur.execute(
    "INSERT ... VALUES (%s, %s, 0, NOW() + INTERVAL '%(h)s hours')" % {"h": 4},
    (session_id, key),
)
```
After Python's `%` dict formatting, the `%s` placeholders in `VALUES (%s, %s, ...)` raise `TypeError: not enough arguments for format string` because dict-based `%` formatting requires ALL `%` sequences to be named.

**CORRECT:**
```python
ttl = self.ttl_hours  # integer constant, not user input
cur.execute(
    f"INSERT ... VALUES (%s, %s, 0, NOW() + INTERVAL '{ttl} hours')",
    (session_id, key),
)
```

The f-string inlines the integer TTL before psycopg2 sees the query. `ttl_hours` is always an `int` constant (never user-supplied), so there is no injection risk.

The same applies to UPDATE statements with INTERVAL.

## Fail-closed behavior

Any exception in `_atomic_check_and_register` is caught and the row is stamped with:
```python
row["gates"]["exposure_gate"] = {
    "passed":   False,
    "blocks":   ["SESSION_LEDGER_UNAVAILABLE:..."],
    "db_error": str(exc)[:200],
    "backend":  "postgres",
}
```

This blocks the row rather than allowing unchecked exposure through.

## App.py wiring

- `_get_session_ledger(session_id)` returns a `PgSessionLedger` handle (no process state).
- Every request that includes `session_id` in the body gets a PgSessionLedger passed to `run_pipeline(existing_ledger=...)`.
- `ensure_table_exists()` is called once at module import (wrapped in try/except to avoid blocking app startup on transient DB issues).

## Smoke test proof (2026-07-15)

- E: two HTTP requests with same session_id → second request blocked via `PLAYER_EXPOSURE:2x`, backend=postgres ✓
- F: worker restart → session data survives in PostgreSQL → duplicate still blocked ✓
