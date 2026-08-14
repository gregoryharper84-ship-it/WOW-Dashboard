---
name: UAC provenance hook transaction isolation
description: Savepoint pattern for best-effort hooks that share the caller's psycopg2 connection — prevents InFailedSqlTransaction cascade on hook failure.
---

# UAC Provenance Hook — Transaction Isolation (SAVEPOINT)

## The Rule
Any best-effort DB hook that shares the caller's psycopg2 connection **must** wrap its SQL in a named SAVEPOINT. `except Exception: pass` alone is insufficient — PostgreSQL puts the connection in `PGTRANSACTION_ABORTED` state on any SQL error, and psycopg2 raises `InFailedSqlTransaction` on every subsequent statement until a ROLLBACK or ROLLBACK TO SAVEPOINT is issued.

## Why
`_audit_uac_evidence_provenance()` ran an UPDATE on the caller's shared `conn`. When the provenance columns didn't exist (migration not run via `ensure_tables()`), psycopg2 aborted the transaction. The `except: pass` guard swallowed the Python exception but never called `ROLLBACK TO SAVEPOINT`. All 19 tests in `test_universal_agent_b0_db.py` and 7 in `test_universal_agent_b2_db.py` cascaded with `InFailedSqlTransaction` because the class shares one connection via `setUpClass`.

## How to Apply
Any function with this pattern:
```python
try:
    _some_optional_db_hook(conn, ...)
except Exception:
    pass
```

Must become:
```python
_SP = "_hook_savepoint_name"
try:
    with conn.cursor() as _c: _c.execute(f"SAVEPOINT {_SP}")
    _some_optional_db_hook(conn, ...)
    # On success: hook commits internally, savepoint auto-released by commit.
except Exception:
    try:
        with conn.cursor() as _c:
            _c.execute(f"ROLLBACK TO SAVEPOINT {_SP}")
            _c.execute(f"RELEASE SAVEPOINT {_SP}")
    except Exception:
        pass  # Savepoint may not exist (creation failed or hook already committed)
```

**Key properties:**
- `ROLLBACK TO SAVEPOINT` is permitted by PostgreSQL even in `PGTRANSACTION_ABORTED` state — it is the correct recovery mechanism.
- If the hook commits internally (as `_audit_uac_evidence_provenance` does), the savepoint is auto-released by that commit — no explicit RELEASE needed on the success path.
- A blind `conn.rollback()` was rejected: it discards uncommitted caller work. Savepoint ONLY undoes the failed hook SQL.
- Separate-connection alternative (pg_odds_quota.py pattern) is for PRIMARY operators, not hooks sharing a caller's conn.

## Migration vs. ensure_tables gap
`ensure_tables()` (audit_store.py) creates only 7 base columns in `uac_evidence_packets`. The 16 provenance columns are added only by `run_provenance_migration()` → `ensure_all_tables()` (app startup path). Tests calling `ensure_tables()` in `setUpClass` will never have provenance columns, so the hook will always fail in those test environments. The savepoint ensures this is harmless.

## LLP calibration hook same vulnerability
`_audit_calibration_entry_provenance()` in `llp_stage2_tables.py` has the same pattern (shares caller's `conn`, no ROLLBACK TO SAVEPOINT in except). Not fixed by this patch — separate task needed.

## Tests
`gate_engine/tests/test_audit_store_hook_resilience.py` — T-AH-01..T-AH-04:
- T-AH-01: real UndefinedColumn failure (no mock)
- T-AH-02: loop idempotency (ON CONFLICT + repeated failures)
- T-AH-03: forced failure via unittest.mock injection
- T-AH-04: success path (no dangling savepoint)
