"""
gate_engine/pg_odds_quota.py — PostgreSQL-backed cross-worker Odds API quota state.

Problem this fixes:
  app.py tracks Odds API quota (_ODDS_QUOTA_STORE) as a module-level dict
  guarded by a threading.Lock. Under gunicorn with 2 workers, each worker
  is a separate OS process with its own copy of that dict — state written
  by worker A is invisible to worker B. A caller hitting
  GET /wow/odds/quota-status may land on whichever worker did NOT just make
  the low-quota Odds API call, and see a stale quota_warning=False.

Fix: write-through every quota update to a small Postgres table
  (wow_odds_quota_state) and merge it into the response the read side
  returns, so the warning is visible regardless of which worker answers.

Design notes:
  - Uses the same psycopg2 + DATABASE_URL connection pattern as
    gate_engine/settlement_worker.py and gate_engine/pg_session_ledger.py.
  - Uses pg_try_advisory_lock with a NEW lock id (778597324) — distinct
    from the settlement worker's 778597299 and the LLP cron's 778597203.
    The lock is best-effort serialization only: the write itself is a
    single-statement UPSERT (INSERT ... ON CONFLICT DO UPDATE), which
    Postgres already executes atomically per row. We never skip or drop
    a write because the lock wasn't acquired — dropping a quota update
    is exactly the reliability bug this module exists to fix. The lock
    just matches the codebase's existing cross-worker-coordination idiom
    and reduces interleaved-write log noise.
  - Fail-open: any DB error is swallowed. Quota tracking is an
    observability signal, not a WOW gate — it must never block a request
    or raise into request handling. can_execute is untouched everywhere;
    this module has no relationship to execution gating.
  - A freshness window (default 120s, ODDS_QUOTA_DB_FRESHNESS_SEC env var)
    is applied on read so a stale/abandoned row doesn't report a phantom
    warning forever.
"""
from __future__ import annotations

import os
from datetime import timezone
from typing import Any, Optional

# pg_try_advisory_lock key — must be unique across all workers in the DB.
# 778597299 = settlement worker, 778597203 = LLP cron. This one: odds quota.
ADVISORY_LOCK_KEY = 778597324

# Ignore DB rows older than this many seconds when building a snapshot.
FRESHNESS_WINDOW_SEC = int(os.environ.get("ODDS_QUOTA_DB_FRESHNESS_SEC", "120"))

def _get_conn(conn_string: Optional[str] = None):
    import psycopg2  # type: ignore
    url = conn_string or os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(url, connect_timeout=5)


def ensure_table_exists(conn_string: Optional[str] = None) -> None:
    """Create wow_odds_quota_state if it doesn't exist. Safe to call repeatedly."""
    _DDL = """
    CREATE TABLE IF NOT EXISTS wow_odds_quota_state (
        tier                TEXT PRIMARY KEY,
        requests_remaining  INTEGER,
        requests_used       INTEGER,
        quota_warning       BOOLEAN NOT NULL DEFAULT FALSE,
        updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_by_pid      INTEGER
    )
    """
    conn = None
    try:
        conn = _get_conn(conn_string)
        cur = conn.cursor()
        cur.execute(_DDL)
        conn.commit()
        cur.close()
    except Exception:
        pass  # fail-open — schema creation must not block startup
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def persist_quota_update(
    tier: str,
    requests_remaining: Optional[int],
    requests_used: Optional[int],
    quota_warning: bool,
    conn_string: Optional[str] = None,
) -> bool:
    """
    Write-through a quota update to Postgres so every gunicorn worker can
    see it. Always performs the UPSERT — the advisory lock is best-effort
    serialization only, never a gate on whether the write happens (see
    module docstring). Returns True on success, False on any failure;
    never raises.
    """
    conn = None
    cur = None
    lock_held = False
    try:
        conn = _get_conn(conn_string)
        cur = conn.cursor()

        try:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
            lock_held = bool(cur.fetchone()[0])
        except Exception:
            lock_held = False

        cur.execute(
            """
            INSERT INTO wow_odds_quota_state
                (tier, requests_remaining, requests_used, quota_warning,
                 updated_at, updated_by_pid)
            VALUES (%s, %s, %s, %s, NOW(), %s)
            ON CONFLICT (tier) DO UPDATE SET
                requests_remaining = EXCLUDED.requests_remaining,
                requests_used      = EXCLUDED.requests_used,
                quota_warning      = EXCLUDED.quota_warning,
                updated_at         = EXCLUDED.updated_at,
                updated_by_pid     = EXCLUDED.updated_by_pid
            """,
            (tier, requests_remaining, requests_used, quota_warning, os.getpid()),
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        if conn is not None:
            try:
                if lock_held and cur is not None:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
                    conn.commit()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass


def fetch_quota_snapshot(conn_string: Optional[str] = None) -> dict[str, Any]:
    """
    Read the cross-worker quota state. Returns {} on any DB error — callers
    must treat this as best-effort and fall back to the local in-process
    store; this function never raises.

    Only rows updated within FRESHNESS_WINDOW_SEC are returned.
    """
    conn = None
    try:
        conn = _get_conn(conn_string)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT tier, requests_remaining, requests_used, quota_warning,
                   updated_at
            FROM wow_odds_quota_state
            WHERE updated_at > NOW() - (%s || ' seconds')::interval
            """,
            (FRESHNESS_WINDOW_SEC,),
        )
        rows = cur.fetchall()
        out: dict[str, Any] = {}
        for tier, remaining, used, warning, updated_at in rows:
            out[tier] = {
                "requests_remaining": remaining,
                "requests_used":      used,
                "quota_warning":      bool(warning),
                "updated_at":         updated_at.astimezone(timezone.utc)
                                        .isoformat().replace("+00:00", "Z"),
                "source":             "postgres_cross_worker",
            }
        return out
    except Exception:
        return {}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
