"""
unified_calibration_ledger.py  —  wow_unified_calibration_ledger
WOW v16 — Linemakers Presentation & Self-Audit Patch

Cross-lane calibration table that stores every candidate evaluated (qualified
AND rejected) so the engine can detect whether a gate is improving decisions
or merely suppressing visible losses.

Lanes:
  KALSHI_WEATHER   — Gaussian temperature / threshold markets
  KALSHI_SPORTS    — full-game outright-winner sports markets
  SPORTS_LLP       — LLP moneyline probability / upset candidates
  PROP             — player prop scoring (future)

Entry types:
  QUALIFIED  — passed all gates, reached final pool
  REJECTED   — failed at least one gate; rejection_reason set
  WATCH      — passed gates but below minimum edge floor

Public API:
  ensure_table()     — DDL + migrations; idempotent
  log_candidate()    — insert QUALIFIED / REJECTED / WATCH record
  settle_result()    — update with settlement outcome
  get_ledger()       — query records with filters
  get_calibration_summary()  — Brier / log-loss by lane and bucket
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

DDL = """
CREATE TABLE IF NOT EXISTS wow_unified_calibration_ledger (
    id                              SERIAL PRIMARY KEY,
    created_at                      TIMESTAMPTZ DEFAULT NOW(),
    updated_at                      TIMESTAMPTZ DEFAULT NOW(),

    -- Classification
    lane                            TEXT NOT NULL,  -- KALSHI_WEATHER / KALSHI_SPORTS / SPORTS_LLP / PROP
    entry_type                      TEXT NOT NULL DEFAULT 'QUALIFIED',  -- QUALIFIED / REJECTED / WATCH
    model_version                   TEXT,
    calibration_version             TEXT,

    -- Identity
    candidate_id                    TEXT,           -- run_id + ticker/prop slug
    ticker_or_prop                  TEXT NOT NULL,
    event_ticker                    TEXT,
    category                        TEXT,
    sport                           TEXT,
    league                          TEXT,
    side_yes_no                     TEXT,           -- YES / NO / OVER / UNDER / LESS / MORE

    -- Probability at entry
    model_probability               NUMERIC,
    confidence_low                  NUMERIC,
    confidence_high                 NUMERIC,
    probability_at_first_observation    NUMERIC,    -- snapshot when first eligible
    probability_at_final_eligible_snapshot NUMERIC, -- snapshot used for final ranking

    -- Edge
    market_price                    NUMERIC,        -- no-vig or executable price at evaluation
    lower_bound_edge                NUMERIC,        -- calibrated_lower_bound − no_vig − friction

    -- Rejection
    rejection_reason                TEXT,           -- gate code or blocker label when REJECTED
    contract_identity_warning       TEXT,           -- CONTRACT_IDENTITY_UNVERIFIED if ticker mismatch

    -- Settlement
    settlement_status               TEXT DEFAULT 'OPEN',   -- OPEN / SETTLED / VOIDED
    settled_result                  TEXT,           -- YES / NO / OVER / UNDER / VOID
    settled_at                      TIMESTAMPTZ,

    -- Calibration metrics (populated at settlement)
    brier_contribution              NUMERIC,        -- (p − o)²
    log_loss_contribution           NUMERIC,        -- −(o·log p + (1−o)·log(1−p))
    price_movement_after_snapshot   NUMERIC,        -- closing_price − market_price (CLV proxy)

    -- Post-mortem
    dominant_failure_tag            TEXT,
    notes                           TEXT,
    run_id                          TEXT            -- category-scan run_id for traceability
)
"""

_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS ucl_lane_entry_idx  ON wow_unified_calibration_ledger (lane, entry_type);
CREATE INDEX IF NOT EXISTS ucl_ticker_idx      ON wow_unified_calibration_ledger (ticker_or_prop);
CREATE INDEX IF NOT EXISTS ucl_status_idx      ON wow_unified_calibration_ledger (settlement_status);
CREATE INDEX IF NOT EXISTS ucl_created_idx     ON wow_unified_calibration_ledger (created_at DESC);
CREATE INDEX IF NOT EXISTS ucl_run_idx         ON wow_unified_calibration_ledger (run_id)
    WHERE run_id IS NOT NULL;
"""

# ---------------------------------------------------------------------------
# Calibration bucket ranges (consistent with kalshi_forecast_ledger)
# ---------------------------------------------------------------------------

_CALIBRATION_BUCKETS = [
    (0.52, 0.55, "52-55%"),
    (0.55, 0.60, "55-60%"),
    (0.60, 0.65, "60-65%"),
    (0.65, 0.70, "65-70%"),
    (0.70, 1.01, "70%+"),
]


def _bucket(prob: float | None) -> str | None:
    if prob is None:
        return None
    for lo, hi, label in _CALIBRATION_BUCKETS:
        if lo <= prob < hi:
            return label
    return None


def _brier(p: float | None, outcome: int | None) -> float | None:
    if p is None or outcome is None:
        return None
    try:
        return round((float(p) - int(outcome)) ** 2, 6)
    except (TypeError, ValueError):
        return None


def _log_loss(p: float | None, outcome: int | None) -> float | None:
    if p is None or outcome is None:
        return None
    try:
        pc = max(1e-7, min(1 - 1e-7, float(p)))
        o  = int(outcome)
        if o not in (0, 1):
            return None
        return round(-(o * math.log(pc) + (1 - o) * math.log(1 - pc)), 8)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_conn():
    import psycopg2  # type: ignore
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(db_url, connect_timeout=10)


def ensure_table() -> None:
    """Create wow_unified_calibration_ledger if absent, apply indexes."""
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

def log_candidate(entry: dict[str, Any]) -> dict[str, Any]:
    """
    Insert a calibration record (QUALIFIED, REJECTED, or WATCH).

    Required fields: lane, entry_type, ticker_or_prop.
    Optional but strongly recommended: model_probability, lower_bound_edge,
    rejection_reason (when REJECTED), run_id.

    Stores rejected candidates so the engine can detect gate bias.
    """
    ensure_table()

    required = ("lane", "entry_type", "ticker_or_prop")
    missing  = [f for f in required if not entry.get(f)]
    if missing:
        return {"ok": False, "detail": f"Missing required fields: {missing}"}

    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute(
            """
            INSERT INTO wow_unified_calibration_ledger (
                lane, entry_type, model_version, calibration_version,
                candidate_id, ticker_or_prop, event_ticker, category, sport, league, side_yes_no,
                model_probability, confidence_low, confidence_high,
                probability_at_first_observation, probability_at_final_eligible_snapshot,
                market_price, lower_bound_edge,
                rejection_reason, contract_identity_warning,
                settlement_status, run_id, notes
            ) VALUES (
                %s,%s,%s,%s,
                %s,%s,%s,%s,%s,%s,%s,
                %s,%s,%s,
                %s,%s,
                %s,%s,
                %s,%s,
                %s,%s,%s
            )
            RETURNING id, created_at
            """,
            (
                entry["lane"],
                entry["entry_type"],
                entry.get("model_version"),
                entry.get("calibration_version"),
                entry.get("candidate_id"),
                entry["ticker_or_prop"],
                entry.get("event_ticker"),
                entry.get("category"),
                entry.get("sport"),
                entry.get("league"),
                entry.get("side_yes_no"),
                entry.get("model_probability"),
                entry.get("confidence_low"),
                entry.get("confidence_high"),
                entry.get("probability_at_first_observation"),
                entry.get("probability_at_final_eligible_snapshot"),
                entry.get("market_price"),
                entry.get("lower_bound_edge"),
                entry.get("rejection_reason"),
                entry.get("contract_identity_warning"),
                "OPEN",
                entry.get("run_id"),
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
            "detail":     f"Calibration record logged (id={row[0]}, entry_type={entry['entry_type']}).",
        }
    except Exception as exc:
        return {"ok": False, "detail": f"DB error: {exc}"}


def settle_result(
    record_id:               int,
    result:                  str,            # YES / NO / OVER / UNDER / VOID
    closing_price:           float | None,
    dominant_failure_tag:    str | None = None,
    notes:                   str | None = None,
) -> dict[str, Any]:
    """
    Update a calibration record with settlement outcome.
    Computes brier_contribution, log_loss_contribution, price_movement_after_snapshot.
    """
    ensure_table()
    try:
        conn = _get_conn()
        cur  = conn.cursor()

        cur.execute(
            "SELECT model_probability, market_price FROM wow_unified_calibration_ledger WHERE id=%s",
            (record_id,),
        )
        row = cur.fetchone()

        brier: float | None    = None
        log_loss: float | None = None
        price_move: float | None = None
        if row:
            mp    = float(row[0]) if row[0] is not None else None
            mp_at = float(row[1]) if row[1] is not None else None
            outcome = 1 if result in ("YES", "OVER", "MORE") else (0 if result in ("NO", "UNDER", "LESS") else None)
            brier    = _brier(mp, outcome)
            log_loss = _log_loss(mp, outcome)
            if mp_at is not None and closing_price is not None:
                price_move = round(float(closing_price) - mp_at, 4)

        cur.execute(
            """
            UPDATE wow_unified_calibration_ledger
            SET updated_at                  = NOW(),
                settlement_status           = 'SETTLED',
                settled_result              = %s,
                settled_at                  = NOW(),
                brier_contribution          = %s,
                log_loss_contribution       = %s,
                price_movement_after_snapshot = %s,
                dominant_failure_tag        = %s,
                notes                       = COALESCE(%s, notes)
            WHERE id = %s
            RETURNING id, updated_at
            """,
            (result, brier, log_loss, price_move, dominant_failure_tag, notes, record_id),
        )
        updated = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        if not updated:
            return {"ok": False, "detail": f"No record found with id={record_id}"}
        return {
            "ok":              True,
            "id":              updated[0],
            "updated_at":      updated[1].isoformat() if updated[1] else None,
            "brier":           brier,
            "log_loss":        log_loss,
            "price_movement":  price_move,
        }
    except Exception as exc:
        return {"ok": False, "detail": f"DB error: {exc}"}


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------

def get_ledger(
    limit:            int       = 50,
    lane:             str | None = None,
    entry_type:       str | None = None,
    settlement_status: str | None = None,
    run_id:           str | None = None,
) -> list[dict[str, Any]]:
    """Query calibration records with optional filters."""
    ensure_table()
    try:
        import psycopg2.extras  # type: ignore
        conn = _get_conn()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        clauses: list[str] = []
        params:  list[Any] = []

        for col, val in [
            ("lane", lane), ("entry_type", entry_type),
            ("settlement_status", settlement_status), ("run_id", run_id),
        ]:
            if val:
                clauses.append(f"{col} = %s")
                params.append(val)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(min(limit, 500))

        cur.execute(
            f"SELECT * FROM wow_unified_calibration_ledger {where} ORDER BY created_at DESC LIMIT %s",
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


def get_calibration_summary() -> dict[str, Any]:
    """
    Return Brier / log-loss summary grouped by lane and entry_type.
    Includes settled counts and overall means.
    """
    ensure_table()
    try:
        import psycopg2.extras  # type: ignore
        conn = _get_conn()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT
                lane,
                entry_type,
                COUNT(*)                                                AS total,
                COUNT(*) FILTER (WHERE settlement_status = 'SETTLED')  AS settled,
                ROUND(AVG(brier_contribution)::numeric, 5)             AS mean_brier,
                ROUND(AVG(log_loss_contribution)::numeric, 5)          AS mean_log_loss,
                ROUND(AVG(price_movement_after_snapshot)::numeric, 5)  AS mean_price_movement
            FROM wow_unified_calibration_ledger
            GROUP BY lane, entry_type
            ORDER BY lane, entry_type
            """
        )
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        return {
            "by_lane_and_type": rows,
            "can_execute":      False,
            "execution_rule":   "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS",
        }
    except Exception as exc:
        return {"error": str(exc), "can_execute": False}
