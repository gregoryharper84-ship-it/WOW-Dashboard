"""
validation/metrics/core.py

Brier score, log loss, calibration buckets, sample coverage, and
line-specific slices for binary prop predictions.

All functions accept a list of (predicted_probability, outcome_bool) pairs
and return plain dicts — no external dependencies beyond the stdlib.
"""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

# (predicted_prob, hit)
Sample = Tuple[Optional[float], bool]


# ── Brier score ──────────────────────────────────────────────────────────────

def brier_score(samples: List[Sample]) -> dict:
    """
    Brier score = mean((p - o)^2) over samples with non-None probability.

    Returns
    -------
    dict with keys: score, n, n_skipped_null_prob
    """
    valid = [(p, o) for p, o in samples if p is not None]
    skipped = len(samples) - len(valid)
    if not valid:
        return {"score": None, "n": 0, "n_skipped_null_prob": skipped,
                "status": "INSUFFICIENT_SAMPLE"}
    score = sum((p - int(o)) ** 2 for p, o in valid) / len(valid)
    return {
        "score":               round(score, 6),
        "n":                   len(valid),
        "n_skipped_null_prob": skipped,
        "status":              "OK",
    }


# ── Log loss ─────────────────────────────────────────────────────────────────

_EPS = 1e-9   # clip to avoid log(0)


def log_loss(samples: List[Sample]) -> dict:
    """
    Log loss = -mean(o*log(p) + (1-o)*log(1-p)).
    Probabilities clipped to [eps, 1-eps] to avoid -inf.

    Returns
    -------
    dict with keys: score, n, n_skipped_null_prob
    """
    valid = [(p, o) for p, o in samples if p is not None]
    skipped = len(samples) - len(valid)
    if not valid:
        return {"score": None, "n": 0, "n_skipped_null_prob": skipped,
                "status": "INSUFFICIENT_SAMPLE"}
    total = 0.0
    for p, o in valid:
        p_clip = max(_EPS, min(1 - _EPS, p))
        o_int  = int(o)
        total += -(o_int * math.log(p_clip) + (1 - o_int) * math.log(1 - p_clip))
    score = total / len(valid)
    return {
        "score":               round(score, 6),
        "n":                   len(valid),
        "n_skipped_null_prob": skipped,
        "status":              "OK",
    }


# ── Calibration buckets ──────────────────────────────────────────────────────

def calibration_buckets(
    samples: List[Sample],
    *,
    n_bins: int = 5,
    min_bin_count: int = 3,
) -> dict:
    """
    Uniform-width calibration bins across [0, 1].

    Returns
    -------
    dict with:
      bins: list of dicts per bin (lower, upper, count, observed_rate,
            mean_predicted_probability, status)
      ece:  expected calibration error (weighted mean |obs - pred|)
      n_total: total samples with non-None probability
      n_sparse_bins: bins flagged SPARSE
    """
    valid = [(p, o) for p, o in samples if p is not None]
    n_total = len(valid)
    bin_width = 1.0 / n_bins
    bins = []
    ece_num = 0.0

    for i in range(n_bins):
        lo = i * bin_width
        hi = lo + bin_width
        # Last bin includes upper edge
        if i == n_bins - 1:
            in_bin = [(p, o) for p, o in valid if lo <= p <= hi]
        else:
            in_bin = [(p, o) for p, o in valid if lo <= p < hi]

        count = len(in_bin)
        obs_rate  = (sum(int(o) for _, o in in_bin) / count) if count else None
        mean_pred = (sum(p for p, _ in in_bin) / count) if count else None
        status    = "SPARSE" if count < min_bin_count else "OK"
        if count and obs_rate is not None and mean_pred is not None:
            ece_num += count * abs(obs_rate - mean_pred)

        bins.append({
            "lower":                     round(lo, 4),
            "upper":                     round(hi, 4),
            "count":                     count,
            "observed_rate":             round(obs_rate, 4) if obs_rate is not None else None,
            "mean_predicted_probability": round(mean_pred, 4) if mean_pred is not None else None,
            "status":                    status,
        })

    ece = round(ece_num / n_total, 6) if n_total else None
    n_sparse = sum(1 for b in bins if b["status"] == "SPARSE")

    return {
        "bins":         bins,
        "ece":          ece,
        "n_total":      n_total,
        "n_sparse_bins": n_sparse,
        "n_bins":       n_bins,
    }


# ── Sample coverage ───────────────────────────────────────────────────────────

def sample_coverage(
    samples: List[Sample],
    *,
    min_total: int = 10,
) -> dict:
    """
    Report how many predictions have a non-None probability vs. total.

    Returns
    -------
    dict with: n_total, n_with_prob, n_null_prob, coverage_rate, status
    """
    n_total    = len(samples)
    n_with     = sum(1 for p, _ in samples if p is not None)
    n_null     = n_total - n_with
    rate       = round(n_with / n_total, 4) if n_total else 0.0
    status     = "INSUFFICIENT_SAMPLE" if n_with < min_total else "OK"
    return {
        "n_total":       n_total,
        "n_with_prob":   n_with,
        "n_null_prob":   n_null,
        "coverage_rate": rate,
        "status":        status,
    }


# ── Line-specific slices ─────────────────────────────────────────────────────

def line_slices(
    samples: List[Sample],
    lines: List[float],
    *,
    min_per_slice: int = 5,
) -> dict:
    """
    Compute Brier score per distinct line value.

    Parameters
    ----------
    samples   List of (prob, hit) pairs.
    lines     Parallel list of line values (same length as samples).

    Returns
    -------
    dict mapping str(line) → {"brier": ..., "n": ..., "status": ...}
    """
    assert len(samples) == len(lines), "samples and lines must have equal length"
    by_line: dict[float, list] = {}
    for (p, o), ln in zip(samples, lines):
        by_line.setdefault(ln, []).append((p, o))

    result = {}
    for ln, subs in sorted(by_line.items()):
        bs = brier_score(subs)
        status = "SPARSE" if bs.get("n", 0) < min_per_slice else bs.get("status", "OK")
        result[str(ln)] = {
            "brier":  bs.get("score"),
            "n":      bs.get("n"),
            "status": status,
        }
    return result


# ── Aggregate evaluation report ───────────────────────────────────────────────

def evaluate(
    samples: List[Sample],
    lines: List[float],
    *,
    n_bins: int = 5,
    min_bin_count: int = 3,
    min_total: int = 10,
    min_per_slice: int = 5,
    split_label: str = "unknown",
) -> dict:
    """
    Run all metrics on a sample set and return a combined report dict.

    Parameters
    ----------
    samples       List of (predicted_prob_or_None, hit_bool).
    lines         Parallel list of line values (same length as samples).
    split_label   One of "train", "validation", "holdout".
    """
    return {
        "split":             split_label,
        "brier":             brier_score(samples),
        "log_loss":          log_loss(samples),
        "calibration":       calibration_buckets(samples, n_bins=n_bins,
                                                  min_bin_count=min_bin_count),
        "coverage":          sample_coverage(samples, min_total=min_total),
        "line_slices":       line_slices(samples, lines,
                                         min_per_slice=min_per_slice),
    }
