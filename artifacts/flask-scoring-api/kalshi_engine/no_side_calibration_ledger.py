"""
no_side_calibration_ledger.py  —  Postgres kalshi_no_side_calibration table
WOW-PATCH-2026-07-29-KALSHI-NO-SIDE-TAIL-RISK-AND-CALIBRATION

Separate from kalshi_forecast_ledger so NO-side price-bucket calibration
does not pollute the main ledger's Brier / CLV stats.

Price buckets tracked (reviewer-specified):
  50-69c | 70-84c | 85-89c | 90-94c | 95-99c

The 95–99c bucket must be reviewed in isolation because a very high hit rate
can still produce net losses when rare misses are underestimated.

Public API:
  log_entry(entry)         — insert a forward-test observation
  settle_entry(id, ...)    — update with settlement outcome
  get_ledger(...)          — query records
  get_bucket_summary()     — per-bucket calibration stats
  ensure_table()           — idempotent DDL (called lazily)
"""
from __future__ import annotations

import os
from typing import Any

DDL = """
CREATE TABLE IF NOT EXISTS kalshi_no_side_calibration (
    id                      SERIAL PRIMARY KEY,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW(),

    -- Contract identity
    market_ticker           TEXT NOT NULL,
    event_ticker            TEXT,
    category                TEXT,

    -- Side and model
    side_yes_no             TEXT NOT NULL,       -- YES or NO
    model_probability       NUMERIC NOT NULL,    -- P(side)
    calibrated_lb           NUMERIC,             -- model lower bound P(side)
    entry_price             NUMERIC,             -- intended limit price
    price_bucket            TEXT,                -- 50-69c / 70-84c / 85-89c / 90-94c / 95-99c
    fee_adjusted_breakeven  NUMERIC,
    loss_to_win_ratio       NUMERIC,
    wins_required           INTEGER,
    is_high_price_contract  BOOLEAN DEFAULT FALSE,

    -- Labels
    patch_label             TEXT,                -- KALSHI_SINGLE_RESEARCH_ELIGIBLE / KALSHI_WATCH / …
    patch_id                TEXT,

    -- Settlement outcome (filled in by settle_entry)
    settlement_status       TEXT DEFAULT 'OPEN', -- OPEN / SETTLED / VOIDED
    result                  TEXT,                -- YES / NO / VOID / PENDING
    closing_price           NUMERIC,
    brier_score             NUMERIC,             -- (model_prob - outcome)^2
    log_loss                NUMERIC,             -- −[o·ln(p) + (1−o)·ln(1−p)]
    net_roi                 NUMERIC,             -- (payout − cost) / cost
    maximum_drawdown        NUMERIC,

    -- Metadata
    maker_or_taker          TEXT,                -- MAKER / TAKER / UNKNOWN
    time_to_expiry_hours    NUMERIC,
    notes                   TEXT,
    mode                    TEXT DEFAULT 'paper' -- paper / live
)
"""

_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS kns_cal_ticker_idx  ON kalshi_no_side_calibration(market_ticker);
CREATE INDEX IF NOT EXISTS kns_cal_bucket_idx  ON kalshi_no_side_calibration(price_bucket);
CREATE INDEX IF NOT EXISTS kns_cal_status_idx  ON kalshi_no_side_calibration(settlement_status);
CREATE INDEX IF NOT EXISTS kns_cal_created_idx ON kalshi_no_side_calibration(created_at DESC);
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


def _compute_calibration_metrics(
    model_prob: float | None,
    result:     str,
) -> tuple[float | None, float | None]:
    """Return (brier_score, log_loss) for one settled observation."""
    import math
    if model_prob is None or result not in ("YES", "NO"):
        return None, None
    p = max(1e-7, min(1.0 - 1e-7, float(model_prob)))
    o = 1 if result == "YES" else 0
    brier   = round((p - o) ** 2, 6)
    log_los = round(-(o * math.log(p) + (1 - o) * math.log(1 - p)), 8)
    return brier, log_los


# ---------------------------------------------------------------------------
# Table setup
# ---------------------------------------------------------------------------

def ensure_table() -> None:
    """Create kalshi_no_side_calibration if absent. Idempotent — safe to call repeatedly."""
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
# Write — log a forward-test observation
# ---------------------------------------------------------------------------

def log_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """
    Insert a forward-test observation into kalshi_no_side_calibration.

    Required fields: market_ticker, side_yes_no, model_probability.
    All other fields are optional but encouraged.

    Returns {ok, id, created_at, detail} on success or {ok, detail} on failure.
    """
    ensure_table()
    required = ("market_ticker", "side_yes_no", "model_probability")
    missing  = [f for f in required if entry.get(f) is None]
    if missing:
        return {"ok": False, "detail": f"Missing required fields: {missing}"}

    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute(
            """
            INSERT INTO kalshi_no_side_calibration (
                market_ticker, event_ticker, category,
                side_yes_no, model_probability, calibrated_lb,
                entry_price, price_bucket, fee_adjusted_breakeven,
                loss_to_win_ratio, wins_required, is_high_price_contract,
                patch_label, patch_id,
                maker_or_taker, time_to_expiry_hours, notes, mode
            ) VALUES (
                %s,%s,%s,
                %s,%s,%s,
                %s,%s,%s,
                %s,%s,%s,
                %s,%s,
                %s,%s,%s,%s
            )
            RETURNING id, created_at
            """,
            (
                entry["market_ticker"],
                entry.get("event_ticker"),
                entry.get("category"),
                entry["side_yes_no"].upper(),
                entry["model_probability"],
                entry.get("calibrated_lb"),
                entry.get("entry_price"),
                entry.get("price_bucket"),
                entry.get("fee_adjusted_breakeven"),
                entry.get("loss_to_win_ratio"),
                entry.get("wins_required"),
                bool(entry.get("is_high_price_contract", False)),
                entry.get("patch_label"),
                entry.get("patch_id"),
                entry.get("maker_or_taker", "UNKNOWN"),
                entry.get("time_to_expiry_hours"),
                entry.get("notes"),
                entry.get("mode", "paper"),
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
            "detail":     f"NO-side calibration entry logged (id={row[0]}).",
        }
    except Exception as exc:
        return {"ok": False, "detail": f"DB error: {exc}"}


# ---------------------------------------------------------------------------
# Write — settle a record
# ---------------------------------------------------------------------------

def settle_entry(
    record_id:    int,
    result:       str,            # YES / NO / VOID
    closing_price: float | None   = None,
    net_roi:      float | None    = None,
    maximum_drawdown: float | None = None,
    notes:        str | None      = None,
) -> dict[str, Any]:
    """Update a record with settlement outcome and compute calibration metrics."""
    ensure_table()
    try:
        conn = _get_conn()
        cur  = conn.cursor()

        cur.execute(
            "SELECT model_probability FROM kalshi_no_side_calibration WHERE id=%s",
            (record_id,),
        )
        row = cur.fetchone()
        if not row:
            cur.close()
            conn.close()
            return {"ok": False, "detail": f"No record found with id={record_id}"}

        brier, log_loss = _compute_calibration_metrics(float(row[0]), result)

        cur.execute(
            """
            UPDATE kalshi_no_side_calibration
            SET updated_at        = NOW(),
                settlement_status = 'SETTLED',
                result            = %s,
                closing_price     = %s,
                brier_score       = %s,
                log_loss          = %s,
                net_roi           = %s,
                maximum_drawdown  = %s,
                notes             = COALESCE(%s, notes)
            WHERE id = %s
            RETURNING id, updated_at
            """,
            (result, closing_price, brier, log_loss,
             net_roi, maximum_drawdown, notes, record_id),
        )
        updated = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return {
            "ok":          True,
            "id":          updated[0],
            "updated_at":  updated[1].isoformat() if updated[1] else None,
            "brier_score": brier,
            "log_loss":    log_loss,
            "detail":      f"Settlement recorded (id={updated[0]}, result={result}).",
        }
    except Exception as exc:
        return {"ok": False, "detail": f"DB error: {exc}"}


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def get_ledger(
    limit:             int  = 50,
    settlement_status: str | None = None,
    price_bucket:      str | None = None,
    side_yes_no:       str | None = None,
) -> list[dict[str, Any]]:
    """Query the calibration ledger with optional filters."""
    ensure_table()
    try:
        import psycopg2.extras  # type: ignore
        conn = _get_conn()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        clauses: list[str] = []
        params:  list[Any] = []

        if settlement_status:
            clauses.append("settlement_status = %s")
            params.append(settlement_status)
        if price_bucket:
            clauses.append("price_bucket = %s")
            params.append(price_bucket)
        if side_yes_no:
            clauses.append("side_yes_no = %s")
            params.append(side_yes_no.upper())

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(min(limit, 500))

        cur.execute(
            f"SELECT * FROM kalshi_no_side_calibration {where} ORDER BY created_at DESC LIMIT %s",
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


def get_bucket_summary() -> list[dict[str, Any]]:
    """
    Per-bucket calibration stats for all settled records.

    Buckets: 50-69c | 70-84c | 85-89c | 90-94c | 95-99c

    The 95–99c bucket must be interpreted separately — a high hit rate can
    still produce losses if the rare misses are underestimated.
    """
    ensure_table()
    try:
        import psycopg2.extras  # type: ignore
        conn = _get_conn()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT
                COALESCE(price_bucket, 'UNKNOWN')        AS price_bucket,
                side_yes_no,
                COUNT(*)                                  AS total_observations,
                COUNT(*) FILTER (WHERE result = side_yes_no)
                                                          AS wins,
                ROUND(AVG(brier_score)::numeric, 5)       AS mean_brier_score,
                ROUND(AVG(log_loss)::numeric, 5)          AS mean_log_loss,
                ROUND(AVG(net_roi)::numeric, 5)           AS mean_net_roi,
                ROUND(AVG(loss_to_win_ratio)::numeric, 4) AS mean_loss_to_win_ratio,
                ROUND(MAX(loss_to_win_ratio)::numeric, 4) AS max_loss_to_win_ratio
            FROM kalshi_no_side_calibration
            WHERE settlement_status = 'SETTLED'
            GROUP BY COALESCE(price_bucket, 'UNKNOWN'), side_yes_no
            ORDER BY price_bucket, side_yes_no
            """
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()

        result = []
        for r in rows:
            d   = dict(r)
            cnt = int(d["total_observations"])
            result.append({
                "price_bucket":           d["price_bucket"],
                "side_yes_no":            d["side_yes_no"],
                "total_observations":     cnt,
                "wins":                   int(d["wins"]) if d["wins"] else 0,
                "hit_rate":               round(int(d["wins"] or 0) / cnt, 4) if cnt else None,
                "mean_brier_score":       float(d["mean_brier_score"])       if d["mean_brier_score"]       is not None else None,
                "mean_log_loss":          float(d["mean_log_loss"])          if d["mean_log_loss"]          is not None else None,
                "mean_net_roi":           float(d["mean_net_roi"])           if d["mean_net_roi"]           is not None else None,
                "mean_loss_to_win_ratio": float(d["mean_loss_to_win_ratio"]) if d["mean_loss_to_win_ratio"] is not None else None,
                "max_loss_to_win_ratio":  float(d["max_loss_to_win_ratio"])  if d["max_loss_to_win_ratio"]  is not None else None,
                "insufficient_data":      cnt < 5,
                "extreme_bucket_warning": d["price_bucket"] == "95-99c",
            })
        return result
    except Exception as exc:
        return [{"error": str(exc)}]
