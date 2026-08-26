"""
gate_engine/mlb/player_identity_cache.py
=========================================
Persistent DB-backed MLB player identity cache.

Stores pybaseball playerid_lookup() results (MLBAM integer IDs) in Postgres
so they survive gunicorn worker restarts and process crashes.  MLBAM IDs are
assigned by MLB and never change for a player, so a 30-day TTL is generous.

Schema (version 1)
------------------
  wow_mlb_player_identity
    player_key    TEXT PRIMARY KEY   deterministic: "last_first" lowercased
    mlbam_id      INTEGER NOT NULL
    name_first    TEXT NOT NULL
    name_last     TEXT NOT NULL
    cached_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
    expires_at    TIMESTAMPTZ NOT NULL
    schema_version INTEGER NOT NULL DEFAULT 1
    lookup_source TEXT NOT NULL DEFAULT 'pybaseball_playerid_lookup'
    is_valid      BOOLEAN NOT NULL DEFAULT TRUE

Invariants
----------
- No secrets or PII stored — MLBAM ID is a public integer, names are public.
- Corrupt cache recovery: if the fetched row fails type validation the row is
  evicted and None is returned so the caller falls back to a live fetch.
- Thread-safe: a module-level Lock guards all DB writes.
- Fail-closed: any exception during lookup returns None (not a stale value).
- Schema bootstrap is idempotent: ADD COLUMN IF NOT EXISTS for future columns.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

# Schema version bump here when table DDL changes.
_SCHEMA_VERSION = 1
_TTL_DAYS = 30
_TABLE = "wow_mlb_player_identity"
_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_WRITE_LOCK = threading.Lock()


# ── DB connection ──────────────────────────────────────────────────────────────

def _get_conn():
    """Open a psycopg2 connection; raises RuntimeError if unavailable."""
    try:
        import psycopg2  # local import — not available in all environments
    except ImportError as exc:
        raise RuntimeError("psycopg2 not installed") from exc
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(db_url, connect_timeout=10)


# ── Schema bootstrap ───────────────────────────────────────────────────────────

def ensure_schema() -> bool:
    """Create the identity cache table if it doesn't exist.

    Safe to call from multiple gunicorn workers simultaneously — each CREATE /
    ALTER is guarded by IF NOT EXISTS / IF NOT EXISTS (idempotent DDL).

    Returns True on success, False if DB is unavailable (non-fatal).
    """
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return True
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return True
        try:
            conn = _get_conn()
            try:
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(f"""
                            CREATE TABLE IF NOT EXISTS {_TABLE} (
                                player_key    TEXT PRIMARY KEY,
                                mlbam_id      INTEGER NOT NULL,
                                name_first    TEXT NOT NULL,
                                name_last     TEXT NOT NULL,
                                cached_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                                expires_at    TIMESTAMPTZ NOT NULL,
                                schema_version INTEGER NOT NULL DEFAULT {_SCHEMA_VERSION},
                                lookup_source TEXT NOT NULL
                                    DEFAULT 'pybaseball_playerid_lookup',
                                is_valid      BOOLEAN NOT NULL DEFAULT TRUE
                            )
                        """)
                        # Idempotent column additions for future schema upgrades
                        cur.execute(f"""
                            ALTER TABLE {_TABLE}
                            ADD COLUMN IF NOT EXISTS schema_version
                                INTEGER NOT NULL DEFAULT {_SCHEMA_VERSION}
                        """)
                        cur.execute(f"""
                            ALTER TABLE {_TABLE}
                            ADD COLUMN IF NOT EXISTS is_valid
                                BOOLEAN NOT NULL DEFAULT TRUE
                        """)
                        cur.execute(f"""
                            CREATE INDEX IF NOT EXISTS idx_{_TABLE}_expires
                            ON {_TABLE} (expires_at)
                        """)
            finally:
                conn.close()
            _SCHEMA_READY = True
            return True
        except Exception:
            return False


# ── Key normalisation ──────────────────────────────────────────────────────────

def _make_key(first: str, last: str) -> str:
    """Deterministic cache key from player name.  Public names only — no PII."""
    return f"{last.strip().lower()}_{first.strip().lower()}"


# ── Public API ────────────────────────────────────────────────────────────────

def lookup(first: str, last: str) -> Optional[int]:
    """Return cached MLBAM ID or None (cache miss / DB unavailable / expired).

    Corrupt/invalid rows are evicted and None is returned so the caller can
    perform a fresh pybaseball fetch.  Never raises — fail-closed returns None.
    """
    if not first or not last:
        return None
    try:
        ensure_schema()
        conn = _get_conn()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        SELECT mlbam_id, expires_at, is_valid, schema_version
                        FROM {_TABLE}
                        WHERE player_key = %s
                        """,
                        (_make_key(first, last),),
                    )
                    row = cur.fetchone()
                    if row is None:
                        return None  # cache miss
                    mlbam_id, expires_at, is_valid, schema_ver = row

                    # Corrupt / invalid row → evict
                    if not is_valid or not isinstance(mlbam_id, int) or mlbam_id <= 0:
                        _evict_unlocked(cur, first, last)
                        return None

                    # Expired → evict
                    now = datetime.now(timezone.utc)
                    if expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=timezone.utc)
                    if now > expires_at:
                        _evict_unlocked(cur, first, last)
                        return None

                    return int(mlbam_id)
        finally:
            conn.close()
    except Exception:
        return None  # DB unavailable — fail-closed


def store(first: str, last: str, mlbam_id: int,
          source: str = "pybaseball_playerid_lookup") -> bool:
    """Upsert an MLBAM identity record.  Atomic ON CONFLICT DO UPDATE.

    Returns True on success, False on failure (non-fatal).
    Never raises.
    """
    if not first or not last or not isinstance(mlbam_id, int) or mlbam_id <= 0:
        return False
    try:
        ensure_schema()
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=_TTL_DAYS)
        key = _make_key(first, last)
        with _WRITE_LOCK:
            conn = _get_conn()
            try:
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            f"""
                            INSERT INTO {_TABLE}
                                (player_key, mlbam_id, name_first, name_last,
                                 cached_at, expires_at, schema_version,
                                 lookup_source, is_valid)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                            ON CONFLICT (player_key) DO UPDATE SET
                                mlbam_id       = EXCLUDED.mlbam_id,
                                name_first     = EXCLUDED.name_first,
                                name_last      = EXCLUDED.name_last,
                                cached_at      = EXCLUDED.cached_at,
                                expires_at     = EXCLUDED.expires_at,
                                schema_version = EXCLUDED.schema_version,
                                lookup_source  = EXCLUDED.lookup_source,
                                is_valid       = TRUE
                            """,
                            (key, mlbam_id,
                             first.strip(), last.strip(),
                             now, expires_at,
                             _SCHEMA_VERSION, source),
                        )
            finally:
                conn.close()
        return True
    except Exception:
        return False


def invalidate(first: str, last: str) -> bool:
    """Remove a player's cache entry (force re-fetch on next lookup).

    Returns True on success / not-found, False on DB error.
    """
    try:
        ensure_schema()
        with _WRITE_LOCK:
            conn = _get_conn()
            try:
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            f"DELETE FROM {_TABLE} WHERE player_key = %s",
                            (_make_key(first, last),),
                        )
            finally:
                conn.close()
        return True
    except Exception:
        return False


def mark_invalid(first: str, last: str) -> bool:
    """Flag a row as invalid without deleting it (audit trail preserved)."""
    try:
        ensure_schema()
        with _WRITE_LOCK:
            conn = _get_conn()
            try:
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            f"UPDATE {_TABLE} SET is_valid = FALSE WHERE player_key = %s",
                            (_make_key(first, last),),
                        )
            finally:
                conn.close()
        return True
    except Exception:
        return False


# ── Internal helpers ──────────────────────────────────────────────────────────

def _evict_unlocked(cur, first: str, last: str) -> None:
    """Delete the row within an already-open cursor transaction."""
    cur.execute(
        f"DELETE FROM {_TABLE} WHERE player_key = %s",
        (_make_key(first, last),),
    )


def reset_schema_ready() -> None:
    """Force schema re-check on next call (used by gunicorn post_fork hook)."""
    global _SCHEMA_READY
    with _SCHEMA_LOCK:
        _SCHEMA_READY = False
