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

_MIGRATE_DDL = """
ALTER TABLE kalshi_forecast_ledger
    ADD COLUMN IF NOT EXISTS settlement_grade TEXT;
ALTER TABLE kalshi_forecast_ledger
    ADD COLUMN IF NOT EXISTS blocking_reasons TEXT[];
ALTER TABLE kalshi_forecast_ledger
    ADD COLUMN IF NOT EXISTS warnings TEXT[];
ALTER TABLE kalshi_forecast_ledger
    ADD COLUMN IF NOT EXISTS log_loss NUMERIC;
ALTER TABLE kalshi_forecast_ledger
    ADD COLUMN IF NOT EXISTS calibration_bucket TEXT;
ALTER TABLE kalshi_forecast_ledger
    ADD COLUMN IF NOT EXISTS is_primary_observation BOOLEAN DEFAULT TRUE;
"""


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_conn():
    import psycopg2  # type: ignore
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(db_url, connect_timeout=10)


# ---------------------------------------------------------------------------
# Stage 2 — Item 5 helpers
# ---------------------------------------------------------------------------

# Probability bucket ranges — consistent with ml_reporting.py and llp_stage2_tables.py
_CALIBRATION_BUCKETS = [
    (0.52, 0.55, "52-55%"),
    (0.55, 0.60, "55-60%"),
    (0.60, 0.65, "60-65%"),
    (0.65, 0.70, "65-70%"),
    (0.70, 1.01, "70%+"),
]


def _probability_to_calibration_bucket(prob: float | None) -> str | None:
    """Map a probability to the canonical calibration bucket label."""
    if prob is None:
        return None
    for lo, hi, label in _CALIBRATION_BUCKETS:
        if lo <= prob < hi:
            return label
    return None


def _compute_log_loss(model_prob: float | None, outcome: int | None) -> float | None:
    """
    Binary log loss for one observation.
      outcome = 1 (YES/WIN) or 0 (NO/LOSS).
      log_loss = -(outcome * log(p) + (1-outcome) * log(1-p))
    p is clipped to [1e-7, 1-1e-7] to avoid log(0).
    Returns None when inputs are missing or invalid.
    """
    import math
    if model_prob is None or outcome is None:
        return None
    try:
        p = max(1e-7, min(1 - 1e-7, float(model_prob)))
        o = int(outcome)
        if o not in (0, 1):
            return None
        return round(-(o * math.log(p) + (1 - o) * math.log(1 - p)), 8)
    except (TypeError, ValueError):
        return None


def _is_opposing_side_duplicate(
    conn,
    event_ticker: str | None,
    side_yes_no: str,
) -> bool:
    """
    Unique-event accounting (Stage 2 — Item 5):
    Return True if a record for the SAME event_ticker but OPPOSITE side
    already exists in the ledger (OPEN or SETTLED).

    When True the new record is marked is_primary_observation=False so that
    both sides of the same game are never double-counted in calibration.
    """
    if not event_ticker:
        return False
    opposite = "NO" if side_yes_no.upper() == "YES" else "YES"
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT 1 FROM kalshi_forecast_ledger
            WHERE event_ticker = %s AND side_yes_no = %s
            LIMIT 1
            """,
            (event_ticker, opposite),
        )
        found = cur.fetchone() is not None
        cur.close()
        return found
    except Exception:
        return False


def ensure_table() -> None:
    """Create kalshi_forecast_ledger if it does not exist, and apply migrations."""
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute(DDL)
        cur.execute(_INDEX_DDL)
        cur.execute(_MIGRATE_DDL)
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

        # Stage 2 — Item 5: calibration_bucket (consistent across all ledgers)
        model_prob   = entry["model_probability"]
        cal_bucket   = entry.get("calibration_bucket") or _probability_to_calibration_bucket(float(model_prob))
        market_bucket = entry.get("market_bucket") or cal_bucket  # backward compat alias

        # Stage 2 — Item 5: unique-event accounting
        # Mark as non-primary if the opposing side of the same event is already logged.
        event_ticker = entry.get("event_ticker")
        side_yes_no  = entry["side_yes_no"]
        is_primary   = not _is_opposing_side_duplicate(conn, event_ticker, side_yes_no)

        cur.execute(
            """
            INSERT INTO kalshi_forecast_ledger (
                market_ticker, event_ticker, contract_title, category,
                side_yes_no, model_probability, confidence_low, confidence_high,
                kalshi_price, entry_price, best_bid, best_ask,
                spread, depth_score, fee_estimate, adjusted_edge,
                max_playable_price, label, market_bucket, calibration_bucket,
                settlement_source, settlement_grade, mode, notes,
                blocking_reasons, warnings, is_primary_observation
            ) VALUES (
                %s,%s,%s,%s,
                %s,%s,%s,%s,
                %s,%s,%s,%s,
                %s,%s,%s,%s,
                %s,%s,%s,%s,
                %s,%s,%s,%s,
                %s,%s,%s
            )
            RETURNING id, created_at
            """,
            (
                entry["market_ticker"],
                event_ticker,
                entry.get("contract_title"),
                entry.get("category"),
                side_yes_no,
                model_prob,
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
                market_bucket,
                cal_bucket,
                entry.get("settlement_source"),
                entry.get("settlement_grade"),
                entry.get("mode", "paper"),
                entry.get("notes"),
                entry.get("blocking_reasons") or [],
                entry.get("warnings") or [],
                is_primary,
            ),
        )
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return {
            "ok":                    True,
            "id":                    row[0],
            "created_at":            row[1].isoformat() if row[1] else None,
            "calibration_bucket":    cal_bucket,
            "is_primary_observation": is_primary,
            "detail":                f"Paper trade logged (id={row[0]}).",
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

        # Fetch model_probability for calibration metric computation
        cur.execute(
            "SELECT model_probability FROM kalshi_forecast_ledger WHERE id=%s",
            (record_id,),
        )
        row = cur.fetchone()

        # Stage 2 — Item 5: compute both Brier score and log_loss
        brier:    float | None = None
        log_loss: float | None = None
        outcome_int: int | None = None
        if row and result in ("YES", "NO"):
            mp          = float(row[0])
            outcome_int = 1 if result == "YES" else 0
            brier       = round((mp - outcome_int) ** 2, 6)
            log_loss    = _compute_log_loss(mp, outcome_int)

        cur.execute(
            """
            UPDATE kalshi_forecast_ledger
            SET updated_at           = NOW(),
                settlement_status    = 'SETTLED',
                result               = %s,
                closing_price        = %s,
                brier_score          = %s,
                log_loss             = %s,
                clv                  = %s,
                net_pnl              = %s,
                dominant_failure_tag = %s,
                notes                = COALESCE(%s, notes)
            WHERE id = %s
            RETURNING id, updated_at
            """,
            (result, closing_price, brier, log_loss, clv, net_pnl,
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
            "log_loss":    log_loss,
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
        # Stage 2 — Item 5: include mean_log_loss and primary-observation-only stats
        cur.execute(
            """
            SELECT
                COUNT(*)                              AS total_settled,
                COUNT(*) FILTER (WHERE is_primary_observation IS NOT FALSE)
                                                      AS primary_observations,
                ROUND(AVG(brier_score)::numeric, 5)   AS mean_brier_score,
                ROUND(AVG(brier_score)
                    FILTER (WHERE is_primary_observation IS NOT FALSE)::numeric, 5)
                                                      AS mean_brier_primary,
                ROUND(AVG(log_loss)::numeric, 5)       AS mean_log_loss,
                ROUND(AVG(log_loss)
                    FILTER (WHERE is_primary_observation IS NOT FALSE)::numeric, 5)
                                                      AS mean_log_loss_primary,
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
            "can_execute":      False,
            "execution_rule":   "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS",
        }
    except Exception as exc:
        return {"error": str(exc), "can_approve_bets": False}


def get_brier_score_by_bucket() -> list[dict[str, Any]]:
    """
    Return per-bucket calibration stats for all settled records.

    Each entry contains:
      bucket, count, mean_brier_score, mean_clv, insufficient_data (count < 5)
    Buckets with zero settled rows are omitted.
    """
    ensure_table()
    try:
        import psycopg2.extras  # type: ignore
        conn = _get_conn()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # Stage 2 — Item 5: use calibration_bucket (canonical), log_loss, primary-only
        cur.execute(
            """
            SELECT
                COALESCE(calibration_bucket, market_bucket, 'UNKNOWN') AS bucket,
                COUNT(*)                               AS count,
                COUNT(*) FILTER (WHERE is_primary_observation IS NOT FALSE)
                                                       AS primary_count,
                ROUND(AVG(brier_score)::numeric, 5)    AS mean_brier_score,
                ROUND(AVG(brier_score)
                    FILTER (WHERE is_primary_observation IS NOT FALSE)::numeric, 5)
                                                       AS mean_brier_primary,
                ROUND(AVG(log_loss)::numeric, 5)        AS mean_log_loss,
                ROUND(AVG(log_loss)
                    FILTER (WHERE is_primary_observation IS NOT FALSE)::numeric, 5)
                                                       AS mean_log_loss_primary,
                ROUND(AVG(clv)::numeric, 5)             AS mean_clv,
                COUNT(*) FILTER (WHERE result = 'YES')  AS yes_wins
            FROM kalshi_forecast_ledger
            WHERE settlement_status = 'SETTLED'
            GROUP BY COALESCE(calibration_bucket, market_bucket, 'UNKNOWN')
            ORDER BY count DESC
            """
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()

        result = []
        for r in rows:
            d   = dict(r)
            cnt = int(d["count"])
            result.append({
                "bucket":               d["bucket"],
                "count":                cnt,
                "primary_count":        int(d["primary_count"]) if d["primary_count"] else 0,
                "mean_brier_score":     float(d["mean_brier_score"]) if d["mean_brier_score"] is not None else None,
                "mean_brier_primary":   float(d["mean_brier_primary"]) if d["mean_brier_primary"] is not None else None,
                "mean_log_loss":        float(d["mean_log_loss"]) if d["mean_log_loss"] is not None else None,
                "mean_log_loss_primary": float(d["mean_log_loss_primary"]) if d["mean_log_loss_primary"] is not None else None,
                "mean_clv":             float(d["mean_clv"]) if d["mean_clv"] is not None else None,
                "yes_wins":             int(d["yes_wins"]),
                "insufficient_data":    cnt < 5,
            })
        return result
    except Exception as exc:
        return [{"error": str(exc)}]
