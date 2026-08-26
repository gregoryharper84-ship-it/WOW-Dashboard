"""
gate_engine/backtesting.py

Historical backtesting framework for the WOW prop probability engine.

Replays historical prediction+outcome pairs through the calibration
and performance metrics pipeline. Does not re-run the gate engine —
it works against already-scored predictions stored in the ledger.

Backtest modes:
  1. CALIBRATION  — bin predictions by probability; measure actual hit rate per bin
  2. CLB_AUDIT    — verify lower-bound reliability across the CLB range
  3. SPORT_SLICE  — per-sport Brier/hit-rate breakdown
  4. LABEL_AUDIT  — per-terminal-label accuracy (did YES_MODEL_QUALIFIED actually hit?)
  5. EDGE_AUDIT   — did higher pure_edge correlate with higher hit rate?

Public API
----------
  run_backtest(conn, mode, **kwargs) → BacktestResult
  calibration_backtest(conn, days, sport) → dict
  clb_reliability_backtest(conn, days, sport) → dict
  sport_slice_backtest(conn, days) → dict
  label_audit_backtest(conn, days) → dict
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Optional

can_execute: bool = False

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _outcome_val(row: dict) -> Optional[float]:
    rl = (row.get("result_label") or "").upper()
    if rl == "HIT":
        return 1.0
    if rl == "MISS":
        return 0.0
    if rl == "PUSH":
        return 0.5
    return None


def _brier(p: float, o: float) -> float:
    return (p - o) ** 2


def _log_loss(p: float, o: float, eps: float = 1e-9) -> float:
    p_c = max(eps, min(1 - eps, p))
    if o >= 1.0:
        return -math.log(p_c)
    if o <= 0.0:
        return -math.log(1 - p_c)
    return -0.5 * (math.log(p_c) + math.log(1 - p_c))


# ---------------------------------------------------------------------------
# Calibration backtest
# ---------------------------------------------------------------------------

def calibration_backtest(
    conn,
    days: int = 90,
    sport: str | None = None,
    bin_size: float = 0.10,
) -> dict:
    """
    Bin settled predictions by calibrated_probability.
    For each bin, compare average predicted probability vs actual hit rate.
    Calibration Error (ECE) = mean |expected − observed|, weighted by n.
    """
    sport_filter = "AND p.sport = %s" if sport else ""
    params: list[Any] = [days]
    if sport:
        params.append(sport.upper())

    sql = f"""
        SELECT
            p.calibrated_probability,
            o.result_label,
            o.brier_score,
            o.clv
        FROM wow_prop_predictions p
        JOIN wow_prop_outcomes o ON o.prediction_id = p.prediction_id
        WHERE p.scored_date >= CURRENT_DATE - INTERVAL '{days} days'
          AND p.calibrated_probability IS NOT NULL
          {sport_filter}
        ORDER BY p.calibrated_probability
    """

    rows = []
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as exc:
        return {"error": str(exc), "n": 0}

    if not rows:
        return {"n": 0, "bins": [], "ece": None, "brier_mean": None}

    # Bin by predicted probability
    bins: dict[float, dict] = {}
    for r in rows:
        p = _safe_float(r.get("calibrated_probability"))
        o = _outcome_val(r)
        if p is None or o is None:
            continue
        bin_lo = math.floor(p / bin_size) * bin_size
        b = bins.setdefault(bin_lo, {"n": 0, "sum_p": 0.0, "sum_o": 0.0, "briersum": 0.0, "clvsum": 0.0, "n_clv": 0})
        b["n"]        += 1
        b["sum_p"]    += p
        b["sum_o"]    += o
        b["briersum"] += _brier(p, o)
        clv = _safe_float(r.get("clv"))
        if clv is not None:
            b["clvsum"] += clv
            b["n_clv"]  += 1

    bin_results = []
    ece_num = 0.0
    ece_den = 0
    total_brier = 0.0
    n_total = 0

    for bin_lo in sorted(bins):
        b = bins[bin_lo]
        n = b["n"]
        expected = b["sum_p"] / n
        observed = b["sum_o"] / n
        brier_bin = b["briersum"] / n
        clv_mean = (b["clvsum"] / b["n_clv"]) if b["n_clv"] > 0 else None
        ece_num   += n * abs(expected - observed)
        ece_den   += n
        total_brier += b["briersum"]
        n_total     += n
        bin_results.append({
            "bin_lo":   round(bin_lo, 2),
            "bin_hi":   round(bin_lo + bin_size, 2),
            "n":        n,
            "expected": round(expected, 4),
            "observed": round(observed, 4),
            "gap":      round(observed - expected, 4),
            "brier":    round(brier_bin, 4),
            "clv_mean": round(clv_mean, 4) if clv_mean is not None else None,
        })

    ece = round(ece_num / ece_den, 4) if ece_den > 0 else None
    brier_mean = round(total_brier / n_total, 4) if n_total > 0 else None

    return {
        "mode":       "CALIBRATION",
        "sport":      sport,
        "days":       days,
        "n":          n_total,
        "bins":       bin_results,
        "ece":        ece,
        "brier_mean": brier_mean,
    }


# ---------------------------------------------------------------------------
# CLB reliability backtest
# ---------------------------------------------------------------------------

def clb_reliability_backtest(
    conn,
    days: int = 90,
    sport: str | None = None,
) -> dict:
    """
    Verify lower-bound reliability.

    The CLB (conservative lower bound) is the stress-tested floor.
    When CLB ≥ 0.65, we claim ≥ 65% actual hit rate.
    When CLB ≥ 0.50, we claim ≥ 50% actual hit rate.

    This backtest measures whether those claims hold empirically.
    """
    sport_filter = "AND p.sport = %s" if sport else ""
    params: list[Any] = [days]
    if sport:
        params.append(sport.upper())

    sql = f"""
        SELECT
            p.lower_bound,
            p.calibrated_probability,
            p.sport,
            o.result_label
        FROM wow_prop_predictions p
        JOIN wow_prop_outcomes o ON o.prediction_id = p.prediction_id
        WHERE p.scored_date >= CURRENT_DATE - INTERVAL '{days} days'
          AND p.lower_bound IS NOT NULL
          {sport_filter}
    """

    rows = []
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as exc:
        return {"error": str(exc), "n": 0}

    if not rows:
        return {"n": 0, "bands": []}

    bands_def = [
        ("lb_50_54", 0.50, 0.55),
        ("lb_55_59", 0.55, 0.60),
        ("lb_60_64", 0.60, 0.65),
        ("lb_65_69", 0.65, 0.70),
        ("lb_70_74", 0.70, 0.75),
        ("lb_75plus", 0.75, 1.01),
    ]

    band_stats: dict[str, dict] = {k: {"n": 0, "hits": 0.0} for k, *_ in bands_def}

    for r in rows:
        lb = _safe_float(r.get("lower_bound"))
        o  = _outcome_val(r)
        if lb is None or o is None:
            continue
        for bname, lo, hi in bands_def:
            if lo <= lb < hi:
                band_stats[bname]["n"]    += 1
                band_stats[bname]["hits"] += o
                break

    bands_out = []
    for bname, lo, hi in bands_def:
        b = band_stats[bname]
        n = b["n"]
        hit_rate = round(b["hits"] / n, 4) if n > 0 else None
        # Reliability: actual hit_rate ≥ band lo
        reliable = (hit_rate is not None and hit_rate >= lo)
        bands_out.append({
            "band":       bname,
            "lb_lo":      lo,
            "lb_hi":      hi,
            "n":          n,
            "hit_rate":   hit_rate,
            "claimed_min": lo,
            "reliable":   reliable,
            "gap":        round(hit_rate - lo, 4) if hit_rate is not None else None,
        })

    # Overall CLB≥0.65 reliability
    hi_band_hits = sum(b["hits"] for k, b in band_stats.items() if k in ("lb_65_69", "lb_70_74", "lb_75plus"))
    hi_band_n    = sum(b["n"]    for k, b in band_stats.items() if k in ("lb_65_69", "lb_70_74", "lb_75plus"))
    hi_rate = round(hi_band_hits / hi_band_n, 4) if hi_band_n > 0 else None

    return {
        "mode":            "CLB_RELIABILITY",
        "sport":           sport,
        "days":            days,
        "n":               len(rows),
        "bands":           bands_out,
        "clb_65plus_rate": hi_rate,
        "clb_65plus_n":    hi_band_n,
        "clb_65plus_reliable": (hi_rate is not None and hi_rate >= 0.65),
    }


# ---------------------------------------------------------------------------
# Sport slice backtest
# ---------------------------------------------------------------------------

def sport_slice_backtest(conn, days: int = 90) -> dict:
    """Per-sport Brier/hit-rate breakdown."""
    sql = f"""
        SELECT
            p.sport,
            COUNT(p.prediction_id)   AS n_predictions,
            COUNT(o.outcome_id)      AS n_settled,
            AVG(o.brier_score)       AS brier_mean,
            AVG(o.clv)               AS clv_mean,
            AVG(CASE WHEN o.result_label = 'HIT' THEN 1.0
                     WHEN o.result_label = 'MISS' THEN 0.0
                     WHEN o.result_label = 'PUSH' THEN 0.5
                     ELSE NULL END) AS hit_rate
        FROM wow_prop_predictions p
        LEFT JOIN wow_prop_outcomes o ON o.prediction_id = p.prediction_id
        WHERE p.scored_date >= CURRENT_DATE - INTERVAL '{days} days'
        GROUP BY p.sport
        ORDER BY p.sport
    """
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (days,))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as exc:
        return {"error": str(exc)}

    return {
        "mode":   "SPORT_SLICE",
        "days":   days,
        "sports": [
            {k: (float(v) if isinstance(v, (int, float)) and v is not None else (int(v) if isinstance(v, int) else v))
             for k, v in r.items()}
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# Label audit backtest
# ---------------------------------------------------------------------------

def label_audit_backtest(conn, days: int = 90) -> dict:
    """Per-terminal-label accuracy."""
    sql = f"""
        SELECT
            p.terminal_label,
            COUNT(p.prediction_id)   AS n_predictions,
            COUNT(o.outcome_id)      AS n_settled,
            AVG(o.brier_score)       AS brier_mean,
            AVG(CASE WHEN o.result_label = 'HIT' THEN 1.0
                     WHEN o.result_label = 'MISS' THEN 0.0
                     WHEN o.result_label = 'PUSH' THEN 0.5
                     ELSE NULL END) AS hit_rate
        FROM wow_prop_predictions p
        LEFT JOIN wow_prop_outcomes o ON o.prediction_id = p.prediction_id
        WHERE p.scored_date >= CURRENT_DATE - INTERVAL '{days} days'
        GROUP BY p.terminal_label
        ORDER BY hit_rate DESC NULLS LAST
    """
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (days,))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as exc:
        return {"error": str(exc)}

    return {
        "mode":   "LABEL_AUDIT",
        "days":   days,
        "labels": rows,
    }


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------

_MODES = {
    "CALIBRATION":    calibration_backtest,
    "CLB_RELIABILITY": clb_reliability_backtest,
    "SPORT_SLICE":    sport_slice_backtest,
    "LABEL_AUDIT":    label_audit_backtest,
}


def run_backtest(
    conn,
    mode: str = "CALIBRATION",
    days: int = 90,
    sport: str | None = None,
) -> dict:
    """
    Main dispatcher for backtesting modes.

    Parameters
    ----------
    conn  : psycopg2 connection
    mode  : one of CALIBRATION | CLB_RELIABILITY | SPORT_SLICE | LABEL_AUDIT
    days  : lookback window in days
    sport : optional sport filter (e.g. "WNBA", "TENNIS", "MLB")

    Returns dict with mode-specific results + can_execute=False invariant.
    """
    mode_u = (mode or "CALIBRATION").upper()
    fn = _MODES.get(mode_u)
    if fn is None:
        return {
            "error":         f"Unknown backtest mode: {mode!r}",
            "valid_modes":   list(_MODES.keys()),
            "can_execute":   False,
        }

    kwargs: dict[str, Any] = {"days": days}
    if mode_u not in ("SPORT_SLICE",):
        kwargs["sport"] = sport

    result = fn(conn, **kwargs)
    result["can_execute"] = False
    result["run_at"] = datetime.now(timezone.utc).isoformat()
    return result
