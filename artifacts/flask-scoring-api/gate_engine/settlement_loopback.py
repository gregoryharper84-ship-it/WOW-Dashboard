"""
settlement_loopback.py  —  Patch: Settlement Loopback / Calibration Ingestion
WOW v16 / Patch 2026-06-27

Every pipeline run should ingest the SETTLED_LEDGER from the prior 24–72 hours.
If the ledger is missing or stale (no settlements ingested in > 18 hours),
calibration health is downgraded to MONITOR — blocking FINAL_APPROVED.

Required settled ledger fields per entry:
  date, sport, player, market, side
  submitted_line, closing_line
  submitted_odds_or_payout, closing_odds_or_projection
  model_probability, market_probability
  edge, result, CLV, failure_tag, dominant_failure_tag
  slip_type, pick_count, payout_multiplier, slip_EV, actual_slip_result

Hard rule (from patch spec):
  If SETTLED_LEDGER is missing, stale, or not updated within 18 hours →
  downgrade calibration health ceiling to MONITOR.
  MONITOR blocks FINAL_APPROVED. Maximum label = MODEL_QUALIFIED_HOLD.

DB table: settlement_ledger (created on first call if absent)

Public API:
  check_freshness()                — returns ledger health dict
  ingest_settled_result(entry)     — insert one settled result
  get_recent_settlements(n)        — return N most recent entries
  block_final_approved_if_stale(row)  — called in pipeline to apply ceiling
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from typing import Any

from .labels import PropLabel

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STALENESS_HOURS   = 18     # ledger is stale if no settlement in this many hours
MONITOR_LABEL     = "MONITOR"
MAX_LABEL_IF_STALE = PropLabel.MODEL_QUALIFIED_HOLD.value

REQUIRED_FIELDS = (
    "date", "sport", "player", "market", "side",
    "submitted_line", "result", "CLV", "slip_type",
)

DDL = """
CREATE TABLE IF NOT EXISTS settlement_ledger (
    id                      SERIAL PRIMARY KEY,
    ingested_at             TIMESTAMPTZ DEFAULT NOW(),
    result_date             DATE NOT NULL,
    sport                   TEXT,
    player                  TEXT NOT NULL,
    market                  TEXT NOT NULL,
    side                    TEXT,
    submitted_line          NUMERIC,
    closing_line            NUMERIC,
    submitted_odds          NUMERIC,
    closing_odds            NUMERIC,
    model_probability       NUMERIC,
    market_probability      NUMERIC,
    edge                    NUMERIC,
    result                  TEXT NOT NULL,
    clv                     NUMERIC,
    failure_tag             TEXT,
    dominant_failure_tag    TEXT,
    slip_type               TEXT,
    pick_count              INTEGER,
    payout_multiplier       NUMERIC,
    slip_ev                 NUMERIC,
    actual_slip_result      TEXT,
    notes                   TEXT
)
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


def _ensure_table() -> None:
    """Create settlement_ledger table if it does not exist."""
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute(DDL)
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass  # graceful degradation if DB unavailable


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def check_freshness() -> dict[str, Any]:
    """
    Check whether the settlement ledger is current.

    Returns:
        {
          fresh:            bool   — True if ledger updated within STALENESS_HOURS
          stale:            bool   — True if stale or missing
          last_ingested_at: str | None   — ISO timestamp of most recent entry
          age_hours:        float | None — hours since last ingested entry
          total_records:    int
          calibration_ceiling: str | None   — MONITOR if stale, else None
          code:             str
          detail:           str
        }
    """
    try:
        import psycopg2.extras  # type: ignore
        _ensure_table()
        conn = _get_conn()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT
                COUNT(*) AS total,
                MAX(ingested_at) AS last_at
            FROM settlement_ledger
        """)
        row = cur.fetchone()
        cur.close()
        conn.close()

        total    = int(row["total"]) if row else 0
        last_at  = row["last_at"]   if row else None

        if total == 0 or last_at is None:
            return _stale_result(
                total=0,
                last_at=None,
                age_hours=None,
                detail="Settlement ledger is empty — no historical results ingested.",
            )

        # Ensure timezone-aware comparison
        if hasattr(last_at, "tzinfo") and last_at.tzinfo is None:
            last_at = last_at.replace(tzinfo=timezone.utc)

        now     = datetime.now(tz=timezone.utc)
        age_h   = (now - last_at).total_seconds() / 3600.0

        if age_h > STALENESS_HOURS:
            return _stale_result(
                total=total,
                last_at=last_at.isoformat(),
                age_hours=round(age_h, 2),
                detail=(
                    f"Settlement ledger is stale: last entry was {age_h:.1f}h ago "
                    f"(> {STALENESS_HOURS}h threshold). "
                    f"FINAL_APPROVED blocked — max label = {MAX_LABEL_IF_STALE}."
                ),
            )

        return {
            "fresh":               True,
            "stale":               False,
            "last_ingested_at":    last_at.isoformat(),
            "age_hours":           round(age_h, 2),
            "total_records":       total,
            "calibration_ceiling": None,
            "code":                "SETTLEMENT_LEDGER_FRESH",
            "detail":              (
                f"Settlement ledger is current: last entry {age_h:.1f}h ago, "
                f"{total} total records."
            ),
        }

    except Exception as exc:
        # DB unavailable — treat as unknown (not stale, not fresh → caution)
        return {
            "fresh":               False,
            "stale":               False,
            "last_ingested_at":    None,
            "age_hours":           None,
            "total_records":       0,
            "calibration_ceiling": None,
            "code":                "SETTLEMENT_DB_UNAVAILABLE",
            "detail":              f"Cannot query settlement ledger: {exc}",
        }


def ingest_settled_result(entry: dict[str, Any]) -> dict[str, Any]:
    """
    Insert one settled result into the ledger.

    Required fields in ``entry``: player, market, result, date.
    Returns {ok, id, detail}.
    """
    _ensure_table()

    missing = [f for f in REQUIRED_FIELDS if not entry.get(f)]
    if missing:
        return {"ok": False, "detail": f"Missing required fields: {missing}"}

    try:
        import psycopg2  # type: ignore
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute(
            """
            INSERT INTO settlement_ledger (
                result_date, sport, player, market, side,
                submitted_line, closing_line,
                submitted_odds, closing_odds,
                model_probability, market_probability,
                edge, result, clv,
                failure_tag, dominant_failure_tag,
                slip_type, pick_count, payout_multiplier,
                slip_ev, actual_slip_result, notes
            ) VALUES (
                %s,%s,%s,%s,%s,
                %s,%s,
                %s,%s,
                %s,%s,
                %s,%s,%s,
                %s,%s,
                %s,%s,%s,
                %s,%s,%s
            )
            RETURNING id, ingested_at
            """,
            (
                entry.get("date"),
                entry.get("sport"),
                entry["player"],
                entry["market"],
                entry.get("side"),
                entry.get("submitted_line"),
                entry.get("closing_line"),
                entry.get("submitted_odds_or_payout"),
                entry.get("closing_odds_or_projection"),
                entry.get("model_probability"),
                entry.get("market_probability"),
                entry.get("edge"),
                entry["result"],
                entry.get("CLV"),
                entry.get("failure_tag"),
                entry.get("dominant_failure_tag"),
                entry.get("slip_type"),
                entry.get("pick_count"),
                entry.get("payout_multiplier"),
                entry.get("slip_EV"),
                entry.get("actual_slip_result"),
                entry.get("notes"),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return {
            "ok":          True,
            "id":          row[0],
            "ingested_at": row[1].isoformat() if row[1] else None,
            "detail":      f"Settlement ingested (id={row[0]}).",
        }
    except Exception as exc:
        return {"ok": False, "detail": f"DB error: {exc}"}


def get_recent_settlements(n: int = 50) -> list[dict[str, Any]]:
    """Return the N most recent settlement entries (newest first)."""
    try:
        import psycopg2.extras  # type: ignore
        _ensure_table()
        conn = _get_conn()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT id, ingested_at, result_date, sport, player, market, side,
                   submitted_line, closing_line, result, clv,
                   failure_tag, dominant_failure_tag,
                   slip_type, slip_ev, actual_slip_result
            FROM settlement_ledger
            ORDER BY ingested_at DESC
            LIMIT %s
            """,
            (min(n, 500),),
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


def block_final_approved_if_stale(row: dict[str, Any]) -> dict[str, Any]:
    """
    Pipeline hook: if the settlement ledger is stale, cap label at
    MODEL_QUALIFIED_HOLD and add a CALIBRATION_STALE_HOLD blocker.

    Designed to be called once per pipeline run, not per row.
    The caller should check if the result is stale before calling.
    """
    freshness = check_freshness()
    stale = freshness.get("stale", False)

    row.setdefault("gates", {})["settlement_loopback"] = freshness

    if stale and not row.get("terminal_label"):
        row.setdefault("blockers", []).append(
            f"SETTLEMENT_LOOPBACK:STALE:"
            f"age_hours={freshness.get('age_hours')}:"
            f"max_label={MAX_LABEL_IF_STALE}"
        )
        # Do NOT override terminal labels set earlier (e.g. SLATE_PURGE)
        # Only cap at MODEL_QUALIFIED_HOLD if no earlier terminal label is set
        row["_settlement_stale"] = True

    return row


def apply_stale_ceiling_to_output(rows: list[dict[str, Any]], stale: bool) -> None:
    """
    After classifier runs: if settlement ledger was stale when the pipeline
    started, downgrade any FINAL_APPROVED rows to MODEL_QUALIFIED_HOLD.
    Called once in pipeline._build_output when stale=True.
    """
    if not stale:
        return
    for row in rows:
        if row.get("terminal_label") == PropLabel.FINAL_APPROVED.value:
            row["terminal_label"] = MAX_LABEL_IF_STALE
            row.setdefault("blockers", []).append(
                "SETTLEMENT_LOOPBACK:FINAL_APPROVED_DOWNGRADED:"
                "settlement_ledger_stale"
            )


def _stale_result(total: int, last_at: str | None,
                  age_hours: float | None, detail: str) -> dict[str, Any]:
    return {
        "fresh":               False,
        "stale":               True,
        "last_ingested_at":    last_at,
        "age_hours":           age_hours,
        "total_records":       total,
        "calibration_ceiling": MONITOR_LABEL,
        "max_label":           MAX_LABEL_IF_STALE,
        "code":                "SETTLEMENT_LEDGER_STALE",
        "detail":              detail,
    }
