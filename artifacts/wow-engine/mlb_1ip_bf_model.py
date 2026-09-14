"""Research-only fitted MLB first-inning batters-faced model.

The model predicts the first-inning batter-count bucket (3, 4, 5+) with a
Dirichlet-shrunk pitcher history around a league prior. Pitcher state is capped
at the most recent 10 starts so historical validation matches the live 1IP
evidence contract (which hydrates at most 10 prior starts).

This module never publishes a governed probability and can never execute a
wager/order.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

CAN_EXECUTE = False
MODEL_FAMILY = "MLB_1IP_BF_DIRICHLET_SHRINKAGE_V1"
CATEGORIES = ("3", "4", "5_PLUS")
RECENT_HISTORY_LIMIT = 10


def bf_bucket(bf: int) -> str:
    value = int(bf)
    if value == 3:
        return "3"
    if value == 4:
        return "4"
    if value >= 5:
        return "5_PLUS"
    raise ValueError("MLB_1IP_BF_INVALID")


@dataclass(frozen=True)
class BFObservation:
    pitcher_id: int
    bf: int
    event_time: str
    game_pk: int


def _normalize_counts(counts: dict[str, float]) -> dict[str, float]:
    total = sum(float(counts.get(k, 0.0)) for k in CATEGORIES)
    if total <= 0:
        raise ValueError("MLB_1IP_BF_COUNTS_EMPTY")
    return {k: float(counts.get(k, 0.0)) / total for k in CATEGORIES}


def fit_league_prior(rows: Iterable[BFObservation]) -> dict[str, float]:
    counts = {k: 0.0 for k in CATEGORIES}
    n = 0
    for row in rows:
        counts[bf_bucket(row.bf)] += 1.0
        n += 1
    if n < 100:
        raise ValueError("MLB_1IP_BF_TRAINING_ROWS_INSUFFICIENT")
    return _normalize_counts(counts)


def history_buckets(
    rows: Iterable[BFObservation],
    *,
    limit: int = RECENT_HISTORY_LIMIT,
) -> dict[int, list[str]]:
    """Build chronological per-pitcher BF histories capped to recent starts.

    Callers must pass rows in chronological order. Keeping bucket sequences,
    rather than lifetime counts, makes the temporal update rule explicit and
    lets live/shadow scoring use exactly the same recent-start window.
    """
    if limit <= 0:
        raise ValueError("MLB_1IP_BF_HISTORY_LIMIT_INVALID")
    out: dict[int, list[str]] = defaultdict(list)
    for row in rows:
        pid = int(row.pitcher_id)
        out[pid].append(bf_bucket(row.bf))
        if len(out[pid]) > limit:
            del out[pid][:-limit]
    return {pid: list(values) for pid, values in out.items()}


def counts_from_buckets(
    histories: dict[int, list[str]],
    pitcher_id: int,
) -> dict[str, int]:
    counts = {k: 0 for k in CATEGORIES}
    for bucket in histories.get(int(pitcher_id), []):
        if bucket not in CATEGORIES:
            raise ValueError("MLB_1IP_BF_HISTORY_BUCKET_INVALID")
        counts[bucket] += 1
    return counts


def history_counts(
    rows: Iterable[BFObservation],
    *,
    limit: int = RECENT_HISTORY_LIMIT,
) -> dict[int, dict[str, int]]:
    """Compatibility helper returning counts from the capped recent history."""
    histories = history_buckets(rows, limit=limit)
    return {pid: counts_from_buckets(histories, pid) for pid in histories}


def score_pitcher(
    *,
    pitcher_id: int,
    league_prior: dict[str, float],
    pitcher_counts: dict[int, dict[str, int]],
    alpha: float,
) -> dict[str, Any]:
    """Return a three-bucket posterior and exact 3.5/4.5 derivative lines.

    ``alpha`` is the effective league-prior sample size. ``pitcher_counts``
    must represent only the certified recent-history window. No market data
    enters the sporting distribution.
    """
    if not math.isfinite(float(alpha)) or float(alpha) <= 0:
        raise ValueError("MLB_1IP_BF_ALPHA_INVALID")
    prior = _normalize_counts({k: float(league_prior.get(k, 0.0)) for k in CATEGORIES})
    observed = pitcher_counts.get(int(pitcher_id), {k: 0 for k in CATEGORIES})
    if any(int(observed.get(k, 0)) < 0 for k in CATEGORIES):
        raise ValueError("MLB_1IP_BF_HISTORY_COUNT_INVALID")
    n = sum(int(observed.get(k, 0)) for k in CATEGORIES)
    if n > RECENT_HISTORY_LIMIT:
        raise ValueError("MLB_1IP_BF_HISTORY_EXCEEDS_CERTIFIED_WINDOW")
    denom = float(alpha) + n
    posterior = {
        k: (float(alpha) * prior[k] + int(observed.get(k, 0))) / denom
        for k in CATEGORIES
    }
    p3, p4, p5 = posterior["3"], posterior["4"], posterior["5_PLUS"]
    return {
        "model_family": MODEL_FAMILY,
        "pitcher_id": int(pitcher_id),
        "alpha": float(alpha),
        "pitcher_history_n": n,
        "history_limit": RECENT_HISTORY_LIMIT,
        "P_BF_3": p3,
        "P_BF_4": p4,
        "P_BF_GE_5": p5,
        "P_MORE_3_5": p4 + p5,
        "P_LESS_3_5": p3,
        "P_MORE_4_5": p5,
        "P_LESS_4_5": p3 + p4,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def update_history_buckets(
    histories: dict[int, list[str]],
    row: BFObservation,
    *,
    limit: int = RECENT_HISTORY_LIMIT,
) -> None:
    """Admit one settled row only after its prediction, then trim to limit."""
    if limit <= 0:
        raise ValueError("MLB_1IP_BF_HISTORY_LIMIT_INVALID")
    pid = int(row.pitcher_id)
    histories.setdefault(pid, []).append(bf_bucket(row.bf))
    if len(histories[pid]) > limit:
        del histories[pid][:-limit]


def multiclass_brier(actual: list[str], predicted: list[dict[str, float]]) -> float:
    if len(actual) != len(predicted) or not actual:
        raise ValueError("MLB_1IP_BF_METRIC_INPUT_INVALID")
    total = 0.0
    for y, probs in zip(actual, predicted):
        total += sum((float(probs[k]) - (1.0 if y == k else 0.0)) ** 2 for k in CATEGORIES)
    return total / len(actual)


def multiclass_log_loss(actual: list[str], predicted: list[dict[str, float]]) -> float:
    if len(actual) != len(predicted) or not actual:
        raise ValueError("MLB_1IP_BF_METRIC_INPUT_INVALID")
    eps = 1e-12
    return -sum(math.log(max(eps, min(1.0, float(p[y])))) for y, p in zip(actual, predicted)) / len(actual)


def binary_metrics(actual: list[int], predicted: list[float], *, bins: int = 10) -> dict[str, float]:
    if len(actual) != len(predicted) or not actual:
        raise ValueError("MLB_1IP_BF_METRIC_INPUT_INVALID")
    n = len(actual)
    brier = sum((float(p) - int(y)) ** 2 for y, p in zip(actual, predicted)) / n
    eps = 1e-12
    log_loss = -sum(
        int(y) * math.log(max(eps, min(1.0 - eps, float(p))))
        + (1 - int(y)) * math.log(max(eps, min(1.0 - eps, 1.0 - float(p))))
        for y, p in zip(actual, predicted)
    ) / n
    ece = 0.0
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        idx = [j for j, p in enumerate(predicted) if (lo <= p < hi) or (i == bins - 1 and p == 1.0)]
        if not idx:
            continue
        conf = sum(predicted[j] for j in idx) / len(idx)
        hit = sum(actual[j] for j in idx) / len(idx)
        ece += len(idx) / n * abs(conf - hit)
    mean_p = sum(predicted) / n
    hit_rate = sum(actual) / n
    return {
        "n": float(n),
        "brier": brier,
        "log_loss": log_loss,
        "ece": ece,
        "mean_probability": mean_p,
        "observed_hit_rate": hit_rate,
        "calibration_bias": mean_p - hit_rate,
    }
