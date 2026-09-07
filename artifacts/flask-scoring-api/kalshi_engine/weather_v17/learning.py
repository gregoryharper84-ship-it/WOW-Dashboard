"""Offline Weather V17 learning utilities.

These functions learn station/model error cohorts and calibration health from
immutable historical predictions. They never mutate production weights or place orders.
"""
from __future__ import annotations

from collections import defaultdict
import math
from typing import Any

EPS = 1e-12


def _quantile(values: list[float], q: float) -> float:
    if not values: return float("nan")
    xs = sorted(values)
    pos = max(0.0, min(1.0, q)) * (len(xs) - 1)
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    if lo == hi: return xs[lo]
    f = pos - lo
    return xs[lo] * (1.0 - f) + xs[hi] * f


def horizon_bucket(hours: float | int | None) -> str:
    if hours is None: return "UNKNOWN"
    h = float(hours)
    if h <= 3: return "0_3H"
    if h <= 6: return "3_6H"
    if h <= 12: return "6_12H"
    if h <= 24: return "12_24H"
    if h <= 48: return "24_48H"
    return "48H_PLUS"


def build_station_error_profiles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build station × model × horizon empirical forecast-error profiles.

    Expected rows carry station_id, model/source identity, forecast_high_f,
    official_final_high_f, and optionally forecast_horizon_hours.
    Error convention is official_final_high - forecast_high; the scoring engine
    therefore adds mean_error to a future forecast to debias it.
    """
    grouped: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for row in rows:
        station = str(row.get("station_id") or "").upper()
        model = str(row.get("model_name") or row.get("source_family") or "").upper()
        if not station or not model: continue
        try:
            err = float(row["official_final_high_f"]) - float(row["forecast_high_f"])
        except (KeyError, TypeError, ValueError):
            continue
        grouped[(station, model, horizon_bucket(row.get("forecast_horizon_hours")))].append(err)
    out = []
    for (station, model, bucket), errors in sorted(grouped.items()):
        n = len(errors); mean = sum(errors) / n
        rmse = math.sqrt(sum(e * e for e in errors) / n)
        mae = sum(abs(e) for e in errors) / n
        variance = sum((e - mean) ** 2 for e in errors) / max(1, n - 1)
        out.append({"station_id": station, "model_name": model, "forecast_horizon_bucket": bucket,
            "sample_size": n, "mean_error": mean, "median_error": _quantile(errors, .50),
            "mae": mae, "rmse": rmse, "error_sigma_f": math.sqrt(max(0.0, variance)),
            "p10_error": _quantile(errors, .10), "p25_error": _quantile(errors, .25),
            "p75_error": _quantile(errors, .75), "p90_error": _quantile(errors, .90),
            "profile_status": "STATION_SPECIFIC" if n >= 30 else "THIN_SAMPLE_SHRINK_REQUIRED"})
    return out


def calibration_health(rows: list[dict[str, Any]], bins: int = 10) -> dict[str, Any]:
    """Compute Brier, log loss, calibration bias, ECE and lower-bound reliability."""
    valid = []
    for row in rows:
        p = row.get("calibrated_probability", row.get("raw_probability")); y = row.get("outcome")
        try:
            p = max(EPS, min(1.0 - EPS, float(p)))
            y = 1 if y in (1, True, "YES", "WIN") else 0 if y in (0, False, "NO", "LOSS") else None
        except (TypeError, ValueError): y = None
        if y is not None: valid.append((p, y, row.get("calibrated_lower_bound")))
    if not valid:
        return {"sample_size": 0, "status": "NO_SETTLED_CALIBRATION_SAMPLE"}
    n = len(valid)
    brier = sum((p - y) ** 2 for p, y, _ in valid) / n
    log_loss = -sum(y * math.log(p) + (1 - y) * math.log(1 - p) for p, y, _ in valid) / n
    bias = sum(p - y for p, y, _ in valid) / n
    buckets: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for p, y, _ in valid: buckets[min(bins - 1, int(p * bins))].append((p, y))
    ece = 0.0
    for vals in buckets.values():
        conf = sum(p for p, _ in vals) / len(vals); acc = sum(y for _, y in vals) / len(vals)
        ece += len(vals) / n * abs(conf - acc)
    bound_rows = [(float(lb), y) for _, y, lb in valid if lb is not None]
    lower_bound_reliability = (sum(y for _, y in bound_rows) / len(bound_rows)) if bound_rows else None
    mean_lb = (sum(lb for lb, _ in bound_rows) / len(bound_rows)) if bound_rows else None
    return {"sample_size": n, "brier_score": brier, "log_loss": log_loss,
        "calibration_bias": bias, "expected_calibration_error": ece,
        "lower_bound_reliability": lower_bound_reliability, "mean_published_lower_bound": mean_lb,
        "status": "CALIBRATION_HEALTH_COMPUTED"}


def fit_isotonic_points(rows: list[dict[str, Any]], min_samples: int = 20) -> dict[str, Any]:
    """Fit a simple pool-adjacent-violators isotonic calibration map offline."""
    pairs = []
    for row in rows:
        try:
            p = float(row.get("raw_probability")); raw_y = row.get("outcome")
            y = 1.0 if raw_y in (1, True, "YES", "WIN") else 0.0 if raw_y in (0, False, "NO", "LOSS") else None
            if y is not None: pairs.append((max(0.0, min(1.0, p)), y))
        except (TypeError, ValueError): pass
    if len(pairs) < min_samples:
        return {"status": "CALIBRATION_SAMPLE_INSUFFICIENT", "sample_size": len(pairs), "points": []}
    pairs.sort()
    blocks = [{"x_sum": p, "y_sum": y, "n": 1} for p, y in pairs]
    i = 0
    while i < len(blocks) - 1:
        a, b = blocks[i], blocks[i + 1]
        if a["y_sum"] / a["n"] <= b["y_sum"] / b["n"] + EPS:
            i += 1; continue
        a["x_sum"] += b["x_sum"]; a["y_sum"] += b["y_sum"]; a["n"] += b["n"]
        blocks.pop(i + 1); i = max(0, i - 1)
    points = [[b["x_sum"] / b["n"], b["y_sum"] / b["n"]] for b in blocks]
    return {"status": "FITTED_OFFLINE_CHALLENGER", "method": "ISOTONIC_POINTS", "sample_size": len(pairs), "points": points}
