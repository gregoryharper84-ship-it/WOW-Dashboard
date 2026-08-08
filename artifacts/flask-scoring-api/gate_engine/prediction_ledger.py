"""
gate_engine/prediction_ledger.py

Immutable prediction history ledger — write-once, append-only.

Every scored prop that reaches a terminal label is recorded here before
settlement. Records are never updated; the outcome is written separately
via settlement_audit.py. This is the source of truth for backtesting,
calibration, and Brier/CLV computation.

Schema
------
  wow_prop_predictions   — one row per prediction, written at score time
  wow_prop_outcomes      — one row per settled prediction (FK to predictions)

Public API
----------
  ensure_tables(conn)                          — idempotent DDL
  write_prediction(conn, row, pipeline_meta)   — INSERT prediction; returns prediction_id
  read_predictions(conn, **filters)            — SELECT with optional filters
  read_prediction(conn, prediction_id)         — single row
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

# can_execute is unconditional
can_execute: bool = False

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_CREATE_PREDICTIONS = """
CREATE TABLE IF NOT EXISTS wow_prop_predictions (
    prediction_id        TEXT        PRIMARY KEY,
    research_run_id      TEXT,
    candidate_id         TEXT,
    sport                TEXT        NOT NULL,
    event_key            TEXT,
    player_name          TEXT,
    market               TEXT,
    stat_key             TEXT,
    side                 TEXT,
    line                 NUMERIC,
    price                TEXT,
    market_probability   NUMERIC,
    raw_probability      NUMERIC,
    calibrated_probability NUMERIC,
    lower_bound          NUMERIC,
    upper_bound          NUMERIC,
    raw_more             NUMERIC,
    raw_exact            NUMERIC,
    raw_less             NUMERIC,
    cal_more             NUMERIC,
    cal_exact            NUMERIC,
    cal_less             NUMERIC,
    failure_path_score   NUMERIC,
    terminal_label       TEXT,
    model_status         TEXT,
    sources              JSONB,
    pipeline_meta        JSONB,
    created_at           TIMESTAMPTZ DEFAULT NOW(),
    scored_date          DATE        DEFAULT CURRENT_DATE
)
"""

_CREATE_OUTCOMES = """
CREATE TABLE IF NOT EXISTS wow_prop_outcomes (
    outcome_id                 TEXT        PRIMARY KEY,
    prediction_id              TEXT        NOT NULL,
    official_result            NUMERIC,
    result_label               TEXT,          -- HIT / MISS / PUSH
    settlement_source          TEXT,
    settlement_timestamp       TIMESTAMPTZ,
    closing_market_probability NUMERIC,
    observed_path              TEXT,
    process_classification     TEXT,
    brier_score                NUMERIC,
    log_loss                   NUMERIC,
    clv                        NUMERIC,
    lower_bound_reliable       BOOLEAN,
    created_at                 TIMESTAMPTZ DEFAULT NOW()
)
"""

_CREATE_PRED_IDX = "CREATE INDEX IF NOT EXISTS idx_pred_sport_date ON wow_prop_predictions(sport, scored_date)"
_CREATE_PRED_LABEL_IDX = "CREATE INDEX IF NOT EXISTS idx_pred_label ON wow_prop_predictions(terminal_label)"
_CREATE_OUT_PRED_IDX = "CREATE INDEX IF NOT EXISTS idx_out_pred_id ON wow_prop_outcomes(prediction_id)"


def ensure_tables(conn) -> None:
    """Create ledger tables if they don't exist. Idempotent."""
    with conn.cursor() as cur:
        cur.execute(_CREATE_PREDICTIONS)
        cur.execute(_CREATE_OUTCOMES)
        cur.execute(_CREATE_PRED_IDX)
        cur.execute(_CREATE_PRED_LABEL_IDX)
        cur.execute(_CREATE_OUT_PRED_IDX)
    conn.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _gate(row: dict, key: str, default=None):
    """Read from gates dict first, then top-level row."""
    gates = row.get("gates", {}) or {}
    for g in gates.values():
        if isinstance(g, dict) and key in g:
            return g[key]
    return row.get(key, default)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def write_prediction(
    conn,
    row: dict[str, Any],
    pipeline_meta: dict | None = None,
) -> str:
    """
    Insert one immutable prediction record.

    Returns the prediction_id (UUID string).
    Raises on DB error — caller must handle.
    """
    prediction_id = str(uuid.uuid4())
    gates = row.get("gates", {}) or {}

    # Prefer WNBA generative gate output for probability fields
    wnba_g = gates.get("wnba_generative", {}) or {}
    tennis_g = gates.get("tennis_total_games", {}) or {}

    # Pull calibrated probability from whichever gate ran
    cal_prob  = (
        _safe_float(wnba_g.get("cal_selected"))
        or _safe_float(tennis_g.get("cal_selected"))
        or _safe_float(row.get("calibrated_probability"))
    )
    lower_bound = (
        _safe_float(wnba_g.get("cal_lower_bound"))
        or _safe_float(tennis_g.get("cal_lower_bound"))
        or _safe_float(row.get("calibrated_probability_lower_bound"))
    )
    upper_bound = (
        _safe_float(wnba_g.get("cal_upper_bound"))
        or _safe_float(row.get("calibrated_probability_upper_bound"))
    )
    raw_prob = (
        _safe_float(wnba_g.get("raw_selected"))
        or _safe_float(tennis_g.get("raw_selected"))
        or _safe_float(row.get("model_probability"))
    )

    # Three-outcome fields
    raw_more  = _safe_float(wnba_g.get("raw_more")  or tennis_g.get("raw_more"))
    raw_exact = _safe_float(wnba_g.get("raw_exact") or tennis_g.get("raw_exact"))
    raw_less  = _safe_float(wnba_g.get("raw_less")  or tennis_g.get("raw_less"))
    cal_more  = _safe_float(wnba_g.get("cal_more")  or tennis_g.get("cal_more"))
    cal_exact = _safe_float(wnba_g.get("cal_exact") or tennis_g.get("cal_exact"))
    cal_less  = _safe_float(wnba_g.get("cal_less")  or tennis_g.get("cal_less"))

    # Failure path score (dominant failure probability)
    fp_score = _safe_float(
        wnba_g.get("failure_path_prob")
        or row.get("failure_path_probability")
    )

    # Sources
    sources = row.get("sources") or {}
    if not isinstance(sources, dict):
        sources = {}

    meta = pipeline_meta or {}
    meta["scored_at"] = datetime.now(timezone.utc).isoformat()

    sql = """
        INSERT INTO wow_prop_predictions (
            prediction_id, research_run_id, candidate_id,
            sport, event_key, player_name, market, stat_key, side, line, price,
            market_probability, raw_probability, calibrated_probability,
            lower_bound, upper_bound,
            raw_more, raw_exact, raw_less,
            cal_more, cal_exact, cal_less,
            failure_path_score, terminal_label, model_status,
            sources, pipeline_meta
        ) VALUES (
            %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s
        )
        ON CONFLICT (prediction_id) DO NOTHING
    """

    params = (
        prediction_id,
        row.get("research_run_id") or meta.get("research_run_id"),
        row.get("candidate_id") or row.get("id"),
        (row.get("sport") or "").upper(),
        row.get("event_id") or row.get("event_key"),
        row.get("player_name") or row.get("player") or row.get("team"),
        row.get("market") or row.get("prop_type"),
        row.get("stat_key"),
        row.get("side"),
        _safe_float(row.get("line")),
        str(row.get("price") or row.get("odds") or ""),
        _safe_float(row.get("market_no_vig_probability") or row.get("no_vig_prob")),
        raw_prob,
        cal_prob,
        lower_bound,
        upper_bound,
        raw_more, raw_exact, raw_less,
        cal_more, cal_exact, cal_less,
        fp_score,
        row.get("terminal_label") or row.get("final_label"),
        row.get("model_status"),
        json.dumps(sources),
        json.dumps(meta),
    )

    with conn.cursor() as cur:
        cur.execute(sql, params)
    conn.commit()
    return prediction_id


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def read_predictions(
    conn,
    sport: str | None = None,
    since_date: str | None = None,
    terminal_label: str | None = None,
    min_lower_bound: float | None = None,
    limit: int = 200,
) -> list[dict]:
    """
    Return prediction rows with optional filters.
    Returns newest-first.
    """
    conditions: list[str] = []
    params: list[Any] = []

    if sport:
        conditions.append("sport = %s")
        params.append(sport.upper())
    if since_date:
        conditions.append("scored_date >= %s")
        params.append(since_date)
    if terminal_label:
        conditions.append("terminal_label = %s")
        params.append(terminal_label)
    if min_lower_bound is not None:
        conditions.append("lower_bound >= %s")
        params.append(min_lower_bound)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(min(limit, 1000))

    sql = f"""
        SELECT
            prediction_id, research_run_id, candidate_id,
            sport, event_key, player_name, market, stat_key, side, line, price,
            market_probability, raw_probability, calibrated_probability,
            lower_bound, upper_bound,
            raw_more, raw_exact, raw_less,
            cal_more, cal_exact, cal_less,
            failure_path_score, terminal_label, model_status,
            sources, pipeline_meta, created_at, scored_date
        FROM wow_prop_predictions
        {where}
        ORDER BY created_at DESC
        LIMIT %s
    """

    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    # JSON deserialize
    for r in rows:
        for jf in ("sources", "pipeline_meta"):
            if isinstance(r.get(jf), str):
                try:
                    r[jf] = json.loads(r[jf])
                except Exception:
                    r[jf] = {}
        for f in ("created_at", "scored_date"):
            if hasattr(r.get(f), "isoformat"):
                r[f] = r[f].isoformat()

    return rows


def read_prediction(conn, prediction_id: str) -> dict | None:
    """Return a single prediction row by ID, or None."""
    sql = """
        SELECT
            prediction_id, research_run_id, candidate_id,
            sport, event_key, player_name, market, stat_key, side, line, price,
            market_probability, raw_probability, calibrated_probability,
            lower_bound, upper_bound,
            raw_more, raw_exact, raw_less,
            cal_more, cal_exact, cal_less,
            failure_path_score, terminal_label, model_status,
            sources, pipeline_meta, created_at, scored_date
        FROM wow_prop_predictions
        WHERE prediction_id = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (prediction_id,))
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        d = dict(zip(cols, row))
    for jf in ("sources", "pipeline_meta"):
        if isinstance(d.get(jf), str):
            try:
                d[jf] = json.loads(d[jf])
            except Exception:
                d[jf] = {}
    for f in ("created_at", "scored_date"):
        if hasattr(d.get(f), "isoformat"):
            d[f] = d[f].isoformat()
    return d


# ---------------------------------------------------------------------------
# Calibration summary (for health-gate and dashboard)
# ---------------------------------------------------------------------------

def calibration_summary(conn, sport: str | None = None, days: int = 30) -> dict:
    """
    Join predictions → outcomes to compute calibration stats.
    Returns: {n_predictions, n_settled, brier_mean, clv_mean, lower_bound_reliability}
    """
    sport_filter = "AND p.sport = %s" if sport else ""
    params = [days]
    if sport:
        params.append(sport.upper())

    sql = f"""
        SELECT
            COUNT(p.prediction_id)                          AS n_predictions,
            COUNT(o.outcome_id)                             AS n_settled,
            AVG(o.brier_score)                              AS brier_mean,
            AVG(o.clv)                                      AS clv_mean,
            AVG(CASE WHEN o.lower_bound_reliable THEN 1.0 ELSE 0.0 END) AS lb_reliability
        FROM wow_prop_predictions p
        LEFT JOIN wow_prop_outcomes o ON o.prediction_id = p.prediction_id
        WHERE p.scored_date >= CURRENT_DATE - INTERVAL '%s days' {sport_filter}
    """

    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        if row is None:
            return {"n_predictions": 0, "n_settled": 0}
        cols = [d[0] for d in cur.description]
        d = dict(zip(cols, row))

    return {k: (float(v) if v is not None else None) for k, v in d.items()}
