"""
gate_engine/settlement_audit.py

Settlement auditor for the WOW prediction ledger.

Accepts a settled result for a prediction, computes Brier score, log loss,
and CLV, then writes an immutable outcome row to wow_prop_outcomes.

Brier = (p − o)²   where p = calibrated_probability at prediction time,
                          o = 1 if HIT, 0 if MISS, 0.5 if PUSH
CLV   = calibrated_probability − closing_market_probability

Lower-bound reliability: the lower bound is "reliable" if the outcome
is consistent with the advertised conservative floor (i.e., when the model
said LB ≥ 0.65, the actual hit rate across all settled predictions with
LB ≥ 0.65 should be ≥ 65%).

Public API
----------
  write_outcome(conn, prediction_id, result_data)  → outcome_id
  batch_compute_metrics(conn, days=30)             → dict of aggregate stats
"""
from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

can_execute: bool = False

# ---------------------------------------------------------------------------
# Result classification
# ---------------------------------------------------------------------------

_RESULT_LABELS = {
    "HIT":  1.0,
    "MISS": 0.0,
    "PUSH": 0.5,
    "WIN":  1.0,
    "LOSS": 0.0,
}


def _outcome_value(official_result, line, side) -> Optional[float]:
    """
    Convert (official_result, line, side) → observed probability outcome.

    Returns 1.0 (HIT), 0.0 (MISS), 0.5 (PUSH), or None if undetermined.
    """
    try:
        result_val = float(official_result)
        line_val   = float(line)
    except (TypeError, ValueError):
        return None

    side_u = (side or "MORE").upper()
    if side_u in ("MORE", "OVER"):
        if result_val > line_val:
            return 1.0
        if result_val < line_val:
            return 0.0
        return 0.5   # exact match = push
    elif side_u in ("LESS", "UNDER"):
        if result_val < line_val:
            return 1.0
        if result_val > line_val:
            return 0.0
        return 0.5
    elif side_u == "EXACT":
        if abs(result_val - line_val) < 1e-9:
            return 1.0
        return 0.0
    return None


def _brier(p: float, o: float) -> float:
    """Brier score for a single prediction: (p − o)²."""
    return (p - o) ** 2


def _log_loss(p: float, o: float, eps: float = 1e-9) -> float:
    """Binary log-loss for a single prediction."""
    p_c = max(eps, min(1 - eps, p))
    if o >= 1.0:
        return -math.log(p_c)
    elif o <= 0.0:
        return -math.log(1 - p_c)
    # Push (0.5): average both
    return -0.5 * (math.log(p_c) + math.log(1 - p_c))


# ---------------------------------------------------------------------------
# Write outcome
# ---------------------------------------------------------------------------

def write_outcome(
    conn,
    prediction_id: str,
    result_data: dict[str, Any],
) -> str:
    """
    Write a settlement outcome for a prediction.

    result_data keys (all optional except official_result or result_label):
      official_result         — numeric stat total (e.g. 22 points)
      result_label            — "HIT" | "MISS" | "PUSH" (alternative to numeric)
      settlement_source       — e.g. "ESPN_BOX_SCORE", "MANUAL"
      settlement_timestamp    — ISO string or None (defaults to now)
      closing_market_probability — final market no-vig prob at close
      observed_path           — free-text description of how the result occurred
      process_classification  — e.g. "CLEAN_WIN" | "GOOD_PROCESS_LOSS" | "MODEL_FAILURE"

    Returns: outcome_id (UUID string)
    """
    # Fetch prediction to get p_cal, line, side
    pred = _fetch_prediction(conn, prediction_id)
    if pred is None:
        raise ValueError(f"prediction_id not found: {prediction_id}")

    p_cal  = pred.get("calibrated_probability")
    lb     = pred.get("lower_bound")
    line   = pred.get("line")
    side   = pred.get("side")
    mkt_p  = pred.get("market_probability")

    # Determine observed outcome (o)
    official_result = result_data.get("official_result")
    result_label    = (result_data.get("result_label") or "").upper()

    # Resolve o
    o: Optional[float] = None
    if official_result is not None and line is not None and side:
        o = _outcome_value(official_result, line, side)

    if o is None and result_label in _RESULT_LABELS:
        o = _RESULT_LABELS[result_label]

    if result_label not in _RESULT_LABELS:
        if o == 1.0:
            result_label = "HIT"
        elif o == 0.0:
            result_label = "MISS"
        elif o == 0.5:
            result_label = "PUSH"

    # Metrics
    brier: Optional[float] = None
    log_l: Optional[float] = None
    clv:   Optional[float] = None
    lb_reliable: Optional[bool] = None

    if p_cal is not None and o is not None:
        brier = round(_brier(float(p_cal), o), 6)
        log_l = round(_log_loss(float(p_cal), o), 6)

    close_mkt = result_data.get("closing_market_probability")
    if p_cal is not None and close_mkt is not None:
        try:
            clv = round(float(p_cal) - float(close_mkt), 4)
        except (TypeError, ValueError):
            clv = None

    # Lower-bound reliability: when LB ≥ 0.65, result should be HIT
    if lb is not None and float(lb) >= 0.65 and o is not None:
        lb_reliable = (o >= 0.5)  # HIT or PUSH = reliable

    settle_ts = result_data.get("settlement_timestamp")
    if settle_ts is None:
        settle_ts = datetime.now(timezone.utc).isoformat()

    outcome_id = str(uuid.uuid4())

    sql = """
        INSERT INTO wow_prop_outcomes (
            outcome_id, prediction_id,
            official_result, result_label,
            settlement_source, settlement_timestamp,
            closing_market_probability,
            observed_path, process_classification,
            brier_score, log_loss, clv, lower_bound_reliable
        ) VALUES (
            %s, %s,
            %s, %s,
            %s, %s,
            %s,
            %s, %s,
            %s, %s, %s, %s
        )
        ON CONFLICT (outcome_id) DO NOTHING
    """
    params = (
        outcome_id, prediction_id,
        official_result, result_label,
        result_data.get("settlement_source", "MANUAL"), settle_ts,
        close_mkt,
        result_data.get("observed_path"), result_data.get("process_classification"),
        brier, log_l, clv, lb_reliable,
    )

    with conn.cursor() as cur:
        cur.execute(sql, params)
    conn.commit()

    return outcome_id


def _fetch_prediction(conn, prediction_id: str) -> Optional[dict]:
    sql = """
        SELECT prediction_id, calibrated_probability, lower_bound,
               market_probability, line, side
        FROM wow_prop_predictions
        WHERE prediction_id = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (prediction_id,))
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))


# ---------------------------------------------------------------------------
# Aggregate metrics
# ---------------------------------------------------------------------------

def batch_compute_metrics(conn, days: int = 30, sport: str | None = None) -> dict:
    """
    Compute aggregate calibration metrics over settled predictions.

    Returns:
      n_predictions, n_settled, hit_rate, brier_mean, brier_std,
      log_loss_mean, clv_mean, lower_bound_reliability,
      brier_by_sport (dict), calibration_bins (list of {bin_center, expected, observed})
    """
    sport_filter = "AND p.sport = %s" if sport else ""
    params: list[Any] = [days]
    if sport:
        params.append(sport.upper())

    # Aggregate stats
    agg_sql = f"""
        SELECT
            p.sport,
            COUNT(p.prediction_id)                                 AS n_predictions,
            COUNT(o.outcome_id)                                    AS n_settled,
            AVG(CASE WHEN o.result_label = 'HIT' THEN 1.0
                     WHEN o.result_label = 'MISS' THEN 0.0
                     WHEN o.result_label = 'PUSH' THEN 0.5
                     ELSE NULL END)                                AS hit_rate,
            AVG(o.brier_score)                                     AS brier_mean,
            STDDEV(o.brier_score)                                  AS brier_std,
            AVG(o.log_loss)                                        AS log_loss_mean,
            AVG(o.clv)                                             AS clv_mean,
            AVG(CASE WHEN o.lower_bound_reliable THEN 1.0 ELSE 0.0 END) AS lb_reliability
        FROM wow_prop_predictions p
        LEFT JOIN wow_prop_outcomes o ON o.prediction_id = p.prediction_id
        WHERE p.scored_date >= CURRENT_DATE - INTERVAL '{days} days' {sport_filter}
        GROUP BY p.sport
        ORDER BY p.sport
    """

    by_sport: dict[str, dict] = {}
    totals: dict[str, Any] = {
        "n_predictions": 0, "n_settled": 0,
        "brier_mean": None, "log_loss_mean": None, "clv_mean": None,
        "hit_rate": None, "lower_bound_reliability": None,
    }

    with conn.cursor() as cur:
        cur.execute(agg_sql, params)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    for r in rows:
        s = r["sport"] or "UNKNOWN"
        by_sport[s] = {
            k: (float(v) if v is not None else None)
            for k, v in r.items()
        }
        totals["n_predictions"]  += int(r["n_predictions"] or 0)
        totals["n_settled"]      += int(r["n_settled"] or 0)

    # Calibration bins: bucket calibrated_probability into 0.05-wide bins
    cal_sql = f"""
        SELECT
            FLOOR(p.calibrated_probability / 0.1) * 0.1 AS bin_lo,
            AVG(p.calibrated_probability)               AS expected_prob,
            AVG(CASE WHEN o.result_label = 'HIT' THEN 1.0
                     WHEN o.result_label = 'MISS' THEN 0.0
                     WHEN o.result_label = 'PUSH' THEN 0.5
                     ELSE NULL END)                     AS observed_prob,
            COUNT(o.outcome_id)                         AS n
        FROM wow_prop_predictions p
        JOIN wow_prop_outcomes o ON o.prediction_id = p.prediction_id
        WHERE p.scored_date >= CURRENT_DATE - INTERVAL '{days} days'
          AND p.calibrated_probability IS NOT NULL
          {sport_filter}
        GROUP BY bin_lo
        ORDER BY bin_lo
    """

    cal_bins: list[dict] = []
    try:
        with conn.cursor() as cur:
            cur.execute(cal_sql, params)
            for row in cur.fetchall():
                cal_bins.append({
                    "bin_center": float(row[0] or 0) + 0.05,
                    "expected":   float(row[1]) if row[1] else None,
                    "observed":   float(row[2]) if row[2] else None,
                    "n":          int(row[3]),
                })
    except Exception:
        cal_bins = []

    return {
        **totals,
        "brier_by_sport": by_sport,
        "calibration_bins": cal_bins,
        "days_window": days,
    }
