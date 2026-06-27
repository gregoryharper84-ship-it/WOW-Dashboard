"""
calibration_ledger.py  —  Postgres kalshi_forecast_ledger
WOW v16 Kalshi Exchange Layer

No ledger = no scaling. Every paper-trade and every settled result
must be recorded here before any expansion of Kalshi activity.

Table: kalshi_forecast_ledger
Schema defined in DDL below.

Public API:
  log_paper_trade(entry)     — insert a new paper-trade record
  settle_result(ticker, ...) — update a record with settlement outcome
  get_ledger(limit, status)  — query records
  get_brier_score()          — calibration summary
  ensure_table()             — create table if absent (called lazily)
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

DDL = """
CREATE TABLE IF NOT EXISTS kalshi_forecast_ledger (
    id                  SERIAL PRIMARY KEY,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),

    -- Contract identity
    market_ticker       TEXT NOT NULL,
    event_ticker        TEXT,
    contract_title      TEXT,
    category            TEXT,

    -- Trade parameters
    side_yes_no         TEXT NOT NULL,           -- YES or NO
    model_probability   NUMERIC NOT NULL,
    confidence_low      NUMERIC,
    confidence_high     NUMERIC,

    -- Market data at entry
    kalshi_price        NUMERIC,                 -- price at evaluation time
    entry_price         NUMERIC,                 -- intended limit price
    best_bid            NUMERIC,
    best_ask            NUMERIC,
    spread              NUMERIC,
    depth_score         TEXT,                    -- A/B/C/D/F

    -- Edge
    fee_estimate        NUMERIC,
    adjusted_edge       NUMERIC,
    max_playable_price  NUMERIC,

    -- Labels and state
    label               TEXT NOT NULL,           -- KALSHI_* label
    market_bucket       TEXT,
    settlement_source   TEXT,
    settlement_status   TEXT DEFAULT 'OPEN',     -- OPEN / SETTLED / VOIDED

    -- Settlement outcome
    closing_price       NUMERIC,
    result              TEXT,                    -- YES / NO / VOID / PENDING
    brier_score         NUMERIC,
    clv                 NUMERIC,
    net_pnl             NUMERIC,

    -- Post-mortem
    dominant_failure_tag TEXT,
    notes               TEXT,

    -- Mode
    mode                TEXT DEFAULT 'paper'     -- paper / live
)
"""

_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS kalshi_ledger_ticker_idx ON kalshi_forecast_ledger(market_ticker);
CREATE INDEX IF NOT EXISTS kalshi_ledger_status_idx ON kalshi_forecast_ledger(settlement_status);
CREATE INDEX IF NOT EXISTS kalshi_ledger_created_idx ON kalshi_forecast_ledger(created_at DESC);
"""


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_conn():
    import psycopg2  # type: ignore
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(db_url)


def ensure_table() -> None:
    """Create kalshi_forecast_ledger if it does not exist."""
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute(DDL)
        cur.execute(_INDEX_DDL)
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------

def log_paper_trade(entry: dict[str, Any]) -> dict[str, Any]:
    """
    Insert a paper-trade record into the ledger.

    Required fields: market_ticker, side_yes_no, model_probability, label.
    """
    ensure_table()

    required = ("market_ticker", "side_yes_no", "model_probability", "label")
    missing  = [f for f in required if not entry.get(f) and entry.get(f) != 0]
    if missing:
        return {"ok": False, "detail": f"Missing required fields: {missing}"}

    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute(
            """
            INSERT INTO kalshi_forecast_ledger (
                market_ticker, event_ticker, contract_title, category,
                side_yes_no, model_probability, confidence_low, confidence_high,
                kalshi_price, entry_price, best_bid, best_ask,
                spread, depth_score, fee_estimate, adjusted_edge,
                max_playable_price, label, market_bucket,
                settlement_source, mode, notes
            ) VALUES (
                %s,%s,%s,%s,
                %s,%s,%s,%s,
                %s,%s,%s,%s,
                %s,%s,%s,%s,
                %s,%s,%s,
                %s,%s,%s
            )
            RETURNING id, created_at
            """,
            (
                entry["market_ticker"],
                entry.get("event_ticker"),
                entry.get("contract_title"),
                entry.get("category"),
                entry["side_yes_no"],
                entry["model_probability"],
                entry.get("confidence_low"),
                entry.get("confidence_high"),
                entry.get("kalshi_price"),
                entry.get("entry_price"),
                entry.get("best_bid"),
                entry.get("best_ask"),
                entry.get("spread"),
                entry.get("depth_score"),
                entry.get("fee_estimate"),
                entry.get("adjusted_edge"),
                entry.get("max_playable_price"),
                entry["label"],
                entry.get("market_bucket"),
                entry.get("settlement_source"),
                entry.get("mode", "paper"),
                entry.get("notes"),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return {
            "ok":         True,
            "id":         row[0],
            "created_at": row[1].isoformat() if row[1] else None,
            "detail":     f"Paper trade logged (id={row[0]}).",
        }
    except Exception as exc:
        return {"ok": False, "detail": f"DB error: {exc}"}


def settle_result(
    record_id:     int,
    result:        str,           # YES / NO / VOID
    closing_price: float | None,
    net_pnl:       float | None  = None,
    clv:           float | None  = None,
    dominant_failure_tag: str | None = None,
    notes:         str | None    = None,
) -> dict[str, Any]:
    """Update an existing ledger record with settlement outcome."""
    ensure_table()
    try:
        conn = _get_conn()
        cur  = conn.cursor()

        # Compute Brier score if we have closing price
        brier: float | None = None
        cur.execute(
            "SELECT model_probability FROM kalshi_forecast_ledger WHERE id=%s", (record_id,)
        )
        row = cur.fetchone()
        if row and closing_price is not None:
            mp    = float(row[0])
            # Brier = (model_prob - outcome)^2; outcome = 1 if YES won, 0 if NO won
            outcome = 1.0 if result == "YES" else (0.0 if result == "NO" else None)
            if outcome is not None:
                brier = round((mp - outcome) ** 2, 6)

        cur.execute(
            """
            UPDATE kalshi_forecast_ledger
            SET updated_at = NOW(),
                settlement_status = 'SETTLED',
                result            = %s,
                closing_price     = %s,
                brier_score       = %s,
                clv               = %s,
                net_pnl           = %s,
                dominant_failure_tag = %s,
                notes             = COALESCE(%s, notes)
            WHERE id = %s
            RETURNING id, updated_at
            """,
            (result, closing_price, brier, clv, net_pnl,
             dominant_failure_tag, notes, record_id),
        )
        updated = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        if not updated:
            return {"ok": False, "detail": f"No record found with id={record_id}"}
        return {
            "ok":         True,
            "id":         updated[0],
            "updated_at": updated[1].isoformat() if updated[1] else None,
            "brier_score": brier,
            "detail":     f"Settlement recorded (id={updated[0]}, result={result}).",
        }
    except Exception as exc:
        return {"ok": False, "detail": f"DB error: {exc}"}


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------

def get_ledger(
    limit:            int  = 50,
    settlement_status: str | None = None,
    market_ticker:    str | None  = None,
    mode:             str | None  = None,
) -> list[dict[str, Any]]:
    """Query the ledger with optional filters."""
    ensure_table()
    try:
        import psycopg2.extras  # type: ignore
        conn = _get_conn()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        where_clauses: list[str] = []
        params: list[Any]        = []

        if settlement_status:
            where_clauses.append("settlement_status = %s")
            params.append(settlement_status)
        if market_ticker:
            where_clauses.append("market_ticker = %s")
            params.append(market_ticker)
        if mode:
            where_clauses.append("mode = %s")
            params.append(mode)

        where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        params.append(min(limit, 500))

        cur.execute(
            f"""
            SELECT * FROM kalshi_forecast_ledger
            {where}
            ORDER BY created_at DESC
            LIMIT %s
            """,
            params,
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()

        def _safe(v: Any) -> Any:
            if hasattr(v, "isoformat"):
                return v.isoformat()
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return v

        return [{k: _safe(v) for k, v in dict(r).items()} for r in rows]
    except Exception:
        return []


def get_brier_score() -> dict[str, Any]:
    """Return calibration summary for all settled records."""
    ensure_table()
    try:
        import psycopg2.extras  # type: ignore
        conn = _get_conn()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT
                COUNT(*)                              AS total_settled,
                ROUND(AVG(brier_score)::numeric, 5)   AS mean_brier_score,
                ROUND(AVG(clv)::numeric, 5)            AS mean_clv,
                ROUND(SUM(net_pnl)::numeric, 4)        AS total_pnl,
                COUNT(*) FILTER (WHERE result = 'YES') AS yes_wins,
                COUNT(*) FILTER (WHERE result = 'NO')  AS no_wins,
                COUNT(*) FILTER (WHERE result = 'VOID') AS voids
            FROM kalshi_forecast_ledger
            WHERE settlement_status = 'SETTLED'
            """
        )
        row = dict(cur.fetchone() or {})
        cur.close()
        conn.close()
        return {
            **{k: (float(v) if v is not None else None) for k, v in row.items()},
            "can_approve_bets": False,
        }
    except Exception as exc:
        return {"error": str(exc), "can_approve_bets": False}
