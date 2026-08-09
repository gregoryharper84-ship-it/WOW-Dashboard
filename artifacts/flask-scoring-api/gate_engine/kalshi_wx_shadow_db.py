"""
gate_engine/kalshi_wx_shadow_db.py
WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW — Step 12.5 (durable DB queue)

Postgres persistence helpers for the Kalshi weather shadow queue tables.

TABLES MANAGED HERE
  kalshi_wx_shadow_snapshot_queue
      Holds one row per incoming weather snapshot from the live route.
      Inserted with status='PENDING'.  A future pilot runner will set
      status='CONSUMED' after processing.

  kalshi_wx_shadow_results
      Schema created now so it is ready for the pilot runner.  Nothing
      writes to it in this increment — that belongs to the future runner.

DESIGN
  - All DDL is idempotent (CREATE TABLE IF NOT EXISTS).
  - _TABLES_ENSURED flag prevents redundant DDL within the same worker process.
    A race between two workers arriving simultaneously is harmless — the DDL
    is idempotent and Postgres serialises the concurrent DDL safely.
  - insert_shadow_snapshot() opens its own connection, runs DDL if needed,
    inserts a single row, commits, and closes — no connection state leaks into
    the caller.
  - snapshot_to_json_dict() converts WeatherResearchSnapshot (frozen dataclass
    with tuple fields) to a plain JSON-serialisable dict.

SHADOW-ONLY INVARIANT
  This module ONLY writes to kalshi_wx_shadow_snapshot_queue and
  kalshi_wx_shadow_results.  It never touches any production table.
  It does not import app.py or any application state.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os

_logger = logging.getLogger(__name__)

# ── DDL ───────────────────────────────────────────────────────────────────────

SHADOW_SCHEMA_DDL: str = """
CREATE TABLE IF NOT EXISTS kalshi_wx_shadow_snapshot_queue (
    id                    SERIAL       PRIMARY KEY,
    research_snapshot_id  TEXT         NOT NULL UNIQUE,
    snapshot_json         JSONB        NOT NULL,
    status                TEXT         NOT NULL DEFAULT 'PENDING',
    inserted_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS kalshi_wx_shadow_results (
    id                    SERIAL       PRIMARY KEY,
    research_snapshot_id  TEXT,
    agent_id              TEXT,
    run_id                TEXT,
    validated_output_json JSONB,
    status                TEXT,
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
"""

# ── Tables-ensured flag ────────────────────────────────────────────────────────
# Prevents repeated DDL execution within the same worker process.
# A race between two concurrent first-callers is harmless: IF NOT EXISTS.
_TABLES_ENSURED: bool = False


# ── Connection helper ─────────────────────────────────────────────────────────

def _get_shadow_conn():
    """
    Open and return a new psycopg2 connection via DATABASE_URL.

    Raises RuntimeError if psycopg2 is not installed or DATABASE_URL is absent.
    Caller is responsible for closing.
    """
    try:
        import psycopg2 as _pg
    except ImportError as exc:
        raise RuntimeError("SHADOW_DB: psycopg2 not installed") from exc
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        raise RuntimeError("SHADOW_DB: DATABASE_URL not set")
    return _pg.connect(db_url, connect_timeout=10)


# ── DDL helper ────────────────────────────────────────────────────────────────

def ensure_shadow_tables(cur) -> None:
    """
    Execute shadow table DDL via an already-open cursor.

    Sets the module-level _TABLES_ENSURED flag so subsequent calls within
    the same process skip the DDL.  Safe to call multiple times; all
    statements are IF NOT EXISTS.
    """
    global _TABLES_ENSURED
    if not _TABLES_ENSURED:
        cur.execute(SHADOW_SCHEMA_DDL)
        _TABLES_ENSURED = True


# ── Serialisation helper ──────────────────────────────────────────────────────

def snapshot_to_json_dict(snapshot) -> dict:
    """
    Convert a WeatherResearchSnapshot (frozen dataclass) to a plain dict
    suitable for json.dumps / JSONB storage.

    dataclasses.asdict() recurses into nested dicts but leaves tuples as
    tuples.  This helper recursively converts tuples to lists so the result
    is fully JSON-serialisable.
    """
    raw = dataclasses.asdict(snapshot)

    def _fix(obj):
        if isinstance(obj, tuple):
            return [_fix(v) for v in obj]
        if isinstance(obj, list):
            return [_fix(v) for v in obj]
        if isinstance(obj, dict):
            return {k: _fix(v) for k, v in obj.items()}
        return obj

    return _fix(raw)


# ── Insert ────────────────────────────────────────────────────────────────────

def insert_shadow_snapshot(snapshot) -> None:
    """
    Persist a WeatherResearchSnapshot to kalshi_wx_shadow_snapshot_queue.

    Opens its own connection, ensures tables exist, inserts one row with
    status='PENDING', commits, and closes the connection.

    ON CONFLICT (research_snapshot_id) DO NOTHING prevents duplicate rows
    if the same uuid-based ID arrives twice (should not happen in practice).

    Raises on any DB or serialisation error.  Caller is responsible for
    catching so the production route is never affected.
    """
    json_dict = snapshot_to_json_dict(snapshot)
    json_str  = json.dumps(json_dict)

    conn = _get_shadow_conn()
    try:
        with conn.cursor() as cur:
            ensure_shadow_tables(cur)
            cur.execute(
                """
                INSERT INTO kalshi_wx_shadow_snapshot_queue
                    (research_snapshot_id, snapshot_json, status)
                VALUES (%s, %s::jsonb, 'PENDING')
                ON CONFLICT (research_snapshot_id) DO NOTHING
                """,
                (snapshot.research_snapshot_id, json_str),
            )
        conn.commit()
    finally:
        conn.close()
