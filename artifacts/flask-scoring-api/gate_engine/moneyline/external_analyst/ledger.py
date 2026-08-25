"""
gate_engine/moneyline/external_analyst/ledger.py
WOW-PATCH-2026-08-08-EXTERNAL-ANALYST-INTELLIGENCE

Source-performance ledger for external analyst opinions.

Stores a timestamped row for each captured analyst pick. Rows are
NEVER rewritten after settlement — only the settlement fields are filled
via a separate settle call.

Primary: Postgres table wow_analyst_intelligence_ledger
Fallback: JSONL file at ANALYST_LEDGER_JSONL_PATH

Performance metrics supported:
  - Straight-up accuracy
  - Favorite / underdog accuracy
  - By sport, source, analyst, price bucket
  - Agreement / contradiction vs WOW
  - Closing-market movement direction

Promotional / advertised records from source are never trusted as
performance evidence — only reconciled ledger rows are authoritative.

can_execute=False unconditional.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

can_execute: bool = False  # UNCONDITIONAL

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ANALYST_LEDGER_JSONL_PATH: str = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "data", "analyst_intelligence_ledger.jsonl"
)

_DDL_LOCK  = threading.Lock()
_TABLE_READY = False

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS wow_analyst_intelligence_ledger (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    logged_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Event & source identity
    event_id                   TEXT,
    event_date                 TEXT,
    sport                      TEXT,
    league                     TEXT,
    source_name                TEXT,
    source_family              TEXT,
    analyst_name               TEXT,
    analyst_family             TEXT,
    source_url                 TEXT,
    source_status              TEXT,

    -- Analyst pick
    analyst_side               TEXT,
    analyst_team               TEXT,
    analyst_opponent           TEXT,
    analyst_favorite_role      TEXT,
    displayed_line             TEXT,
    market_type                TEXT,
    published_at               TEXT,

    -- WOW state at capture
    wow_side                   TEXT,
    wow_independent_prob       DOUBLE PRECISION,
    wow_calibrated_lower_bound DOUBLE PRECISION,
    market_no_vig_prob         DOUBLE PRECISION,

    -- Thesis summary (JSON)
    thesis_tags_json           TEXT,

    -- Post-settlement fields (filled by settle call, never retroactively edited)
    official_result            TEXT,       -- HOME_WIN | AWAY_WIN | DRAW | VOID
    settled_at                 TIMESTAMPTZ,
    was_analyst_correct        BOOLEAN,
    closing_market_prob        DOUBLE PRECISION,
    market_moved_toward_analyst BOOLEAN,
    wow_agreed                 BOOLEAN,
    wow_disagreed              BOOLEAN,
    process_classification     TEXT,       -- e.g. AGREEMENT_CORRECT, CONTRADICTION_CORRECT, ...

    -- Deduplication
    canonical_opinion_key      TEXT,
    is_syndicated_copy         BOOLEAN DEFAULT FALSE
);
"""

_CREATE_IDX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_analyst_ledger_event ON wow_analyst_intelligence_ledger(event_id);",
    "CREATE INDEX IF NOT EXISTS idx_analyst_ledger_source ON wow_analyst_intelligence_ledger(source_family);",
    "CREATE INDEX IF NOT EXISTS idx_analyst_ledger_sport ON wow_analyst_intelligence_ledger(sport);",
    "CREATE INDEX IF NOT EXISTS idx_analyst_ledger_logged ON wow_analyst_intelligence_ledger(logged_at);",
]


def _get_conn():
    import psycopg2
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(db_url, connect_timeout=10)


def ensure_analyst_ledger_table() -> bool:
    """
    Create table and indexes idempotently.
    Returns True on success, False on any error (non-fatal).
    """
    global _TABLE_READY
    if _TABLE_READY:
        return True
    with _DDL_LOCK:
        if _TABLE_READY:
            return True
        try:
            conn = _get_conn()
            cur  = conn.cursor()
            cur.execute(_CREATE_TABLE_SQL)
            for idx_sql in _CREATE_IDX_SQL:
                cur.execute(idx_sql)
            conn.commit()
            cur.close()
            conn.close()
            _TABLE_READY = True
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Write (capture)
# ---------------------------------------------------------------------------

def log_analyst_opinion(
    opinion:              "AnalystOpinion",   # noqa: F821
    wow_side:             str | None,
    wow_independent_prob: float | None,
    wow_calibrated_lb:    float | None,
    market_no_vig:        float | None,
) -> bool:
    """
    Write a ledger row for one analyst opinion.

    Best-effort: errors are swallowed and False is returned.
    NEVER raises — must not break the base model.
    """
    from gate_engine.moneyline.external_analyst.types import AnalystOpinion

    record: dict[str, Any] = {
        "logged_at":                  datetime.now(timezone.utc).isoformat(),
        "event_id":                   opinion.event_id,
        "event_date":                 opinion.event_date,
        "sport":                      opinion.sport,
        "league":                     opinion.league,
        "source_name":                opinion.source_name,
        "source_family":              opinion.source_family,
        "analyst_name":               opinion.analyst_name,
        "analyst_family":             opinion.analyst_family,
        "source_url":                 opinion.source_url,
        "source_status":              opinion.source_status,
        "analyst_side":               opinion.side,
        "analyst_team":               opinion.team,
        "analyst_opponent":           opinion.opponent,
        "analyst_favorite_role":      opinion.favorite_role,
        "displayed_line":             opinion.displayed_line,
        "market_type":                opinion.market_type,
        "published_at":               opinion.published_at,
        "wow_side":                   wow_side,
        "wow_independent_prob":       wow_independent_prob,
        "wow_calibrated_lower_bound": wow_calibrated_lb,
        "market_no_vig_prob":         market_no_vig,
        "thesis_tags_json":           json.dumps(opinion.thesis_tags.to_dict()),
        "canonical_opinion_key":      opinion.canonical_opinion_key,
        "is_syndicated_copy":         opinion.is_syndicated_copy,
    }

    # Postgres primary
    pg_ok = _log_pg(record)
    if pg_ok:
        return True

    # JSONL fallback
    return _log_jsonl(record)


def _log_pg(record: dict[str, Any]) -> bool:
    try:
        ensure_analyst_ledger_table()
        conn = _get_conn()
        cur  = conn.cursor()
        cols = ", ".join(record.keys())
        vals = ", ".join(["%s"] * len(record))
        cur.execute(
            f"INSERT INTO wow_analyst_intelligence_ledger ({cols}) VALUES ({vals})",
            list(record.values()),
        )
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception:
        return False


def _log_jsonl(record: dict[str, Any]) -> bool:
    try:
        os.makedirs(os.path.dirname(ANALYST_LEDGER_JSONL_PATH), exist_ok=True)
        with open(ANALYST_LEDGER_JSONL_PATH, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Settle (post-event, append-only — never rewrites historical rows)
# ---------------------------------------------------------------------------

def settle_analyst_opinion(
    canonical_opinion_key: str,
    official_result:       str,
    closing_market_prob:   float | None = None,
) -> bool:
    """
    Mark a ledger row as settled. Updates only settlement fields.
    Original capture fields are NEVER modified.
    """
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute("""
            UPDATE wow_analyst_intelligence_ledger
            SET
                official_result   = %s,
                closing_market_prob = %s,
                settled_at        = NOW(),
                process_classification = CASE
                    WHEN analyst_side = wow_side AND %s IN ('HOME_WIN', 'AWAY_WIN') THEN
                        CASE WHEN
                            (analyst_side = 'home' AND %s = 'HOME_WIN') OR
                            (analyst_side = 'away' AND %s = 'AWAY_WIN')
                            THEN 'AGREEMENT_CORRECT'
                            ELSE 'AGREEMENT_INCORRECT'
                        END
                    ELSE 'CONTRADICTION_SETTLED'
                END,
                was_analyst_correct = CASE
                    WHEN (analyst_side = 'home' AND %s = 'HOME_WIN') OR
                         (analyst_side = 'away' AND %s = 'AWAY_WIN') THEN TRUE
                    ELSE FALSE
                END,
                wow_agreed    = (analyst_side = wow_side),
                wow_disagreed = (analyst_side != wow_side)
            WHERE canonical_opinion_key = %s
              AND settled_at IS NULL
        """, [
            official_result,
            closing_market_prob,
            official_result, official_result, official_result,
            official_result, official_result,
            canonical_opinion_key,
        ])
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception:
        return False
