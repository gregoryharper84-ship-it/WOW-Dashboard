"""
gate_engine/kalshi_wx_shadow_db.py
WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW — Step 12.5 (durable DB queue)
                                                    + deterministic baseline linkage

Postgres persistence helpers for the Kalshi weather shadow tables.

TABLES MANAGED HERE
  kalshi_wx_shadow_snapshot_queue
      One row per incoming weather snapshot (status='PENDING').
      A future pilot runner will set status='CONSUMED' after processing.

  kalshi_wx_shadow_results
      Schema ready; written by the future pilot runner (Increment B).

  kalshi_wx_shadow_deterministic_outcome
      One row per evaluation, keyed by research_snapshot_id, containing the
      terminal_label and price_gate_disposition produced by the deterministic
      model for the same evaluation whose inputs are in the snapshot row.
      This links snapshot → deterministic outcome without re-fetching anything.

DESIGN
  - All DDL is idempotent (CREATE TABLE IF NOT EXISTS).
  - _TABLES_ENSURED flag prevents redundant DDL within the same worker process.
  - Each insert function opens its own connection, runs DDL if needed, inserts,
    commits, and closes — no connection state leaks to the caller.
  - snapshot_to_json_dict() converts WeatherResearchSnapshot (frozen dataclass
    with tuple fields) to a plain JSON-serialisable dict.

SHADOW-ONLY INVARIANT
  This module ONLY writes to the three shadow-only tables defined here.
  It never touches any production table and does not import app.py.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
from typing import Optional

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

CREATE TABLE IF NOT EXISTS kalshi_wx_shadow_deterministic_outcome (
    id                     SERIAL       PRIMARY KEY,
    research_snapshot_id   TEXT         NOT NULL,
    terminal_label         TEXT,
    price_gate_disposition TEXT,
    can_execute            BOOLEAN,
    recorded_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
"""

# ── Tables-ensured flag ────────────────────────────────────────────────────────
# Prevents repeated DDL execution within the same worker process.
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
    Sets _TABLES_ENSURED so subsequent calls within the same process skip it.
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

    Recursively converts tuples to lists so the result is fully JSON-serialisable.
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


# ── Snapshot insert ───────────────────────────────────────────────────────────

def insert_shadow_snapshot(snapshot) -> None:
    """
    Persist a WeatherResearchSnapshot to kalshi_wx_shadow_snapshot_queue.

    Opens its own connection, ensures tables exist, inserts one row with
    status='PENDING', commits, and closes.

    ON CONFLICT (research_snapshot_id) DO NOTHING prevents duplicate rows.
    Raises on any DB or serialisation error; caller catches and logs.
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


# ── Deterministic outcome insert ──────────────────────────────────────────────

def insert_shadow_deterministic_outcome(
    research_snapshot_id: str,
    terminal_label: Optional[str],
    price_gate_disposition: Optional[str],
    can_execute: bool,
) -> None:
    """
    Persist the deterministic evaluation result to
    kalshi_wx_shadow_deterministic_outcome.

    Links to the snapshot row via research_snapshot_id (no FK constraint —
    the two INSERTs happen in the same request but are separate transactions;
    a plain text key is sufficient for the pilot).

    can_execute is always False in this system (DRY_RUN_ONLY); captured as a
    durable audit record, never as something this code sets or changes.

    Raises on any DB error; caller catches and logs.
    """
    conn = _get_shadow_conn()
    try:
        with conn.cursor() as cur:
            ensure_shadow_tables(cur)
            cur.execute(
                """
                INSERT INTO kalshi_wx_shadow_deterministic_outcome
                    (research_snapshot_id, terminal_label,
                     price_gate_disposition, can_execute)
                VALUES (%s, %s, %s, %s)
                """,
                (research_snapshot_id, terminal_label,
                 price_gate_disposition, bool(can_execute)),
            )
        conn.commit()
    finally:
        conn.close()
