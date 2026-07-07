"""
kalshi_watch_ledger.py  —  Persistent logging for all Kalshi LLP candidates.
WOW-PATCH-2026-07-07-KALSHI-FINAL-LOCK-EDGE-DISCOVERY

Logs EVERY candidate touched by /wow/llp/kalshi/ml-evaluate (WATCH, PLAYABLE,
SCOUT, REJECT) to the `kalshi_candidate_ledger` table for CLV and settlement
tracking. WATCH candidates are first-class log entries — not filtered out.

Settlement / CLV fields (closing_price, settlement_result, clv_beat) are NULL
at scan time and filled in via a future settle endpoint.

Schema init: call ensure_schema(conn) once per app startup (idempotent).
Logging: call log_candidate(conn, ...) once per /wow/llp/kalshi/ml-evaluate call.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any, Optional

_SCHEMA_READY = False
_SCHEMA_LOCK  = threading.Lock()


def ensure_schema(conn) -> None:
    """Idempotent CREATE TABLE IF NOT EXISTS for kalshi_candidate_ledger."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS kalshi_candidate_ledger (
                    id                       BIGSERIAL PRIMARY KEY,
                    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    scan_date                DATE        NOT NULL DEFAULT CURRENT_DATE,
                    sport                    TEXT,
                    home_team                TEXT,
                    away_team                TEXT,
                    ticker                   TEXT,
                    event_ticker             TEXT,
                    market_title             TEXT,
                    market_type              TEXT,
                    market_status            TEXT,
                    trading_active           BOOLEAN,
                    kalshi_orderbook_source  TEXT,
                    orderbook_age_seconds    NUMERIC,
                    staleness_grade          TEXT,
                    scan_price               NUMERIC,
                    no_vig_probability       NUMERIC,
                    model_probability        NUMERIC,
                    adjusted_edge            NUMERIC,
                    edge_floor               NUMERIC,
                    label                    TEXT,
                    blocker_tags             TEXT[],
                    final_lock_checked_at    TIMESTAMPTZ,
                    final_lock_fresh         BOOLEAN,
                    closing_price            NUMERIC,
                    settlement_result        TEXT,
                    clv_beat                 BOOLEAN,
                    notes                    TEXT
                );
                CREATE INDEX IF NOT EXISTS kalshi_candidate_ledger_date_idx
                    ON kalshi_candidate_ledger (scan_date);
                CREATE INDEX IF NOT EXISTS kalshi_candidate_ledger_ticker_idx
                    ON kalshi_candidate_ledger (ticker, scan_date);
                CREATE INDEX IF NOT EXISTS kalshi_candidate_ledger_label_idx
                    ON kalshi_candidate_ledger (label);
            """)
            conn.commit()
        _SCHEMA_READY = True


def log_candidate(
    conn,
    *,
    sport:                   Optional[str],
    home_team:               Optional[str],
    away_team:               Optional[str],
    ticker:                  Optional[str],
    event_ticker:            Optional[str],
    market_title:            Optional[str],
    market_type:             str,
    market_status:           Optional[str],
    trading_active:          Optional[bool],
    kalshi_orderbook_source: str,
    orderbook_age_seconds:   Optional[float],
    staleness_grade:         Optional[str],
    scan_price:              Optional[float],
    no_vig_probability:      Optional[float],
    model_probability:       Optional[float],
    adjusted_edge:           Optional[float],
    edge_floor:              Optional[float],
    label:                   str,
    blocker_tags:            list[str],
    final_lock_checked_at:   Optional[str],
    final_lock_fresh:        bool,
    notes:                   Optional[str] = None,
) -> int | None:
    """
    Insert one row into kalshi_candidate_ledger. Returns the new row id,
    or None if the insert fails (never raises — logging must never break
    the primary evaluation flow).
    """
    fl_ts: Optional[datetime] = None
    if final_lock_checked_at:
        try:
            fl_ts = datetime.fromisoformat(
                final_lock_checked_at.replace("Z", "+00:00")
            )
            if fl_ts.tzinfo is None:
                fl_ts = fl_ts.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            fl_ts = None

    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO kalshi_candidate_ledger (
                    sport, home_team, away_team, ticker, event_ticker,
                    market_title, market_type, market_status, trading_active,
                    kalshi_orderbook_source, orderbook_age_seconds, staleness_grade,
                    scan_price, no_vig_probability, model_probability,
                    adjusted_edge, edge_floor, label, blocker_tags,
                    final_lock_checked_at, final_lock_fresh, notes
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s
                ) RETURNING id
            """, (
                sport, home_team, away_team, ticker, event_ticker,
                market_title, market_type, market_status, trading_active,
                kalshi_orderbook_source, orderbook_age_seconds, staleness_grade,
                scan_price, no_vig_probability, model_probability,
                adjusted_edge, edge_floor, label, blocker_tags,
                fl_ts, final_lock_fresh, notes,
            ))
            row_id = cur.fetchone()[0]
            conn.commit()
            return row_id
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return None
