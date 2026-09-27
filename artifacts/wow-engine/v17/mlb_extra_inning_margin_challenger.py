"""Class C research challenger for MLB extra-inning final margins.

This model is intentionally separate from the production moneyline simulator.
It learns only the absolute final margin conditional on which side wins an
extra-inning game. It does not learn winner probability, use market data, or
publish probabilities.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
import math
import random
from typing import Iterable, Sequence

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
MODEL_FAMILY = "MLB_EXTRA_INNING_FINAL_MARGIN_EMPIRICAL_V1"
FEATURE_SCHEMA_VERSION = "MLB_EXTRA_INNING_MARGIN_EVIDENCE_V1"
_VALID_WINNERS = frozenset({"HOME", "AWAY"})


@dataclass(frozen=True)
class ExtraInningMarginRow:
    game_key: str
    game_date: date
    winner: str
    absolute_final_margin: int


@dataclass(frozen=True)
class ExtraInningMarginArtifact:
    train_start: str
    train_end: str
    train_rows: int
    home_histogram: tuple[tuple[int, int], ...]
    away_histogram: tuple[tuple[int, int], ...]
    training_dataset_hash: str

    def payload(self) -> dict:
        return {
            "model_family": MODEL_FAMILY,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "train_start": self.train_start,
            "train_end": self.train_end,
            "train_rows": self.train_rows,
            "home_histogram": [list(v) for v in self.home_histogram],
            "away_histogram": [list(v) for v in self.away_histogram],
            "training_dataset_hash": self.training_dataset_hash,
            "market_features_used": False,
            "moneyline_probability_used": False,
            "probability_publishable": False,
            "automatic_certification": False,
            "automatic_promotion": False,
            "can_execute": False,
        }


class ExtraInningMarginUnavailable(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


def _validate_row(row: ExtraInningMarginRow) -> None:
    if not isinstance(row.game_date, date):
        raise ExtraInningMarginUnavailable(
            "MLB_EXTRA_INNING_MARGIN_DATE_INVALID", "game_date must be a date"
        )
    if not str(row.game_key).strip():
        raise ExtraInningMarginUnavailable(
            "MLB_EXTRA_INNING_MARGIN_GAME_KEY_INVALID", "game_key must be non-empty"
        )
    if row.winner not in _VALID_WINNERS:
        raise ExtraInningMarginUnavailable(
            "MLB_EXTRA_INNING_MARGIN_WINNER_INVALID", "winner must be HOME or AWAY"
        )
    try:
        margin = int(row.absolute_final_margin)
    except (TypeError, ValueError) as exc:
        raise ExtraInningMarginUnavailable(
            "MLB_EXTRA_INNING_MARGIN_INVALID", "final margin must be a positive integer"
        ) from exc
    if margin < 1 or margin != row.absolute_final_margin:
        raise ExtraInningMarginUnavailable(
            "MLB_EXTRA_INNING_MARGIN_INVALID", "final margin must be a positive integer"
        )


def _hist(values: Iterable[int]) -> tuple[tuple[int, int], ...]:
    counts: dict[int, int] = {}
    for raw in values:
        value = int(raw)
        if value < 1:
            raise ExtraInningMarginUnavailable(
                "MLB_EXTRA_INNING_MARGIN_INVALID", "final margin must be positive"
            )
        counts[value] = counts.get(value, 0) + 1
    return tuple(sorted(counts.items()))


def fit_extra_inning_margin_candidate(rows: Sequence[ExtraInningMarginRow]) -> ExtraInningMarginArtifact:
    for row in rows:
        _validate_row(row)
    ordered = sorted(rows, key=lambda r: (r.game_date, r.game_key))
    if len(ordered) < 100:
        raise ExtraInningMarginUnavailable(
            "MLB_EXTRA_INNING_MARGIN_TRAINING_INSUFFICIENT",
            "need at least 100 extra-inning games",
        )
    game_keys = [row.game_key for row in ordered]
    if len(set(game_keys)) != len(game_keys):
        raise ExtraInningMarginUnavailable(
            "MLB_EXTRA_INNING_MARGIN_DUPLICATE_GAME",
            "training evidence contains duplicate game keys",
        )
    home = [r.absolute_final_margin for r in ordered if r.winner == "HOME"]
    away = [r.absolute_final_margin for r in ordered if r.winner == "AWAY"]
    if len(home) < 40 or len(away) < 40:
        raise ExtraInningMarginUnavailable(
            "MLB_EXTRA_INNING_MARGIN_SIDE_SAMPLE_INSUFFICIENT",
            "need at least 40 winner-conditioned rows per side",
        )
    raw = [
        {
            "game_key": r.game_key,
            "game_date": r.game_date.isoformat(),
            "winner": r.winner,
            "margin": r.absolute_final_margin,
        }
        for r in ordered
    ]
    digest = sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return ExtraInningMarginArtifact(
        train_start=ordered[0].game_date.isoformat(),
        train_end=ordered[-1].game_date.isoformat(),
        train_rows=len(ordered),
        home_histogram=_hist(home),
        away_histogram=_hist(away),
        training_dataset_hash=digest,
    )


def _bucket_probs(
    histogram: Sequence[tuple[int, int]], *, smoothing: float = 1.0
) -> tuple[float, float, float]:
    c1 = sum(c for m, c in histogram if m == 1)
    c2 = sum(c for m, c in histogram if m == 2)
    c3 = sum(c for m, c in histogram if m >= 3)
    total = c1 + c2 + c3 + 3.0 * smoothing
    return (
        (c1 + smoothing) / total,
        (c2 + smoothing) / total,
        (c3 + smoothing) / total,
    )


def evaluate_extra_inning_margin_candidate(
    artifact: ExtraInningMarginArtifact, holdout: Sequence[ExtraInningMarginRow]
) -> dict:
    if not holdout:
        raise ExtraInningMarginUnavailable(
            "MLB_EXTRA_INNING_MARGIN_HOLDOUT_EMPTY", "holdout is empty"
        )
    for row in holdout:
        _validate_row(row)
    cutoff = date.fromisoformat(artifact.train_end)
    if any(row.game_date <= cutoff for row in holdout):
        raise ExtraInningMarginUnavailable(
            "MLB_EXTRA_INNING_MARGIN_HOLDOUT_LEAKAGE",
            "every holdout game must be strictly after the training cutoff",
        )
    holdout_keys = [row.game_key for row in holdout]
    if len(set(holdout_keys)) != len(holdout_keys):
        raise ExtraInningMarginUnavailable(
            "MLB_EXTRA_INNING_MARGIN_DUPLICATE_GAME",
            "holdout evidence contains duplicate game keys",
        )

    home_probs = _bucket_probs(artifact.home_histogram)
    away_probs = _bucket_probs(artifact.away_histogram)
    pooled_hist = _hist(
        [
            margin
            for margin, count in (*artifact.home_histogram, *artifact.away_histogram)
            for _ in range(count)
        ]
    )
    pooled_probs = _bucket_probs(pooled_hist)
    nll = pooled_nll = brier = pooled_brier = 0.0
    for row in holdout:
        p = home_probs if row.winner == "HOME" else away_probs
        j = (
            0
            if row.absolute_final_margin == 1
            else 1
            if row.absolute_final_margin == 2
            else 2
        )
        nll -= math.log(max(p[j], 1e-12))
        pooled_nll -= math.log(max(pooled_probs[j], 1e-12))
        y = (
            1.0 if j == 0 else 0.0,
            1.0 if j == 1 else 0.0,
            1.0 if j == 2 else 0.0,
        )
        brier += sum((p[k] - y[k]) ** 2 for k in range(3)) / 3.0
        pooled_brier += sum((pooled_probs[k] - y[k]) ** 2 for k in range(3)) / 3.0
    n = len(holdout)
    return {
        "holdout_rows": n,
        "categorical_log_loss": nll / n,
        "pooled_baseline_log_loss": pooled_nll / n,
        "categorical_brier": brier / n,
        "pooled_baseline_brier": pooled_brier / n,
        "home_margin_ge2_probability": 1.0 - home_probs[0],
        "away_margin_ge2_probability": 1.0 - away_probs[0],
        "market_features_used": False,
        "moneyline_probability_used": False,
        "probability_publishable": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "can_execute": False,
    }


def sample_absolute_margin(
    artifact: ExtraInningMarginArtifact, *, winner: str, rng: random.Random
) -> int:
    histogram = (
        artifact.home_histogram
        if winner == "HOME"
        else artifact.away_histogram
        if winner == "AWAY"
        else ()
    )
    if not histogram:
        raise ExtraInningMarginUnavailable(
            "MLB_EXTRA_INNING_MARGIN_WINNER_INVALID", "winner must be HOME or AWAY"
        )
    total = sum(c for _, c in histogram)
    pick = rng.randrange(total)
    cursor = 0
    for margin, count in histogram:
        cursor += count
        if pick < cursor:
            return margin
    raise AssertionError("unreachable")


def resolve_tied_nine_inning_samples(
    *,
    home_runs9: Sequence[int],
    away_runs9: Sequence[int],
    extra_inning_home_win_probability: float,
    artifact: ExtraInningMarginArtifact,
    seed: int,
) -> tuple[list[int], list[int]]:
    if len(home_runs9) != len(away_runs9):
        raise ExtraInningMarginUnavailable(
            "MLB_EXTRA_INNING_SAMPLES_INVALID", "home/away samples must align"
        )
    p_home = float(extra_inning_home_win_probability)
    if not 0.0 <= p_home <= 1.0:
        raise ExtraInningMarginUnavailable(
            "MLB_EXTRA_INNING_WIN_PROBABILITY_INVALID",
            "extra-inning home-win probability must be in [0,1]",
        )
    rng = random.Random(int(seed))
    home_final, away_final = [], []
    for h, a in zip(home_runs9, away_runs9):
        h, a = int(h), int(a)
        if h != a:
            home_final.append(h)
            away_final.append(a)
            continue
        winner = "HOME" if rng.random() < p_home else "AWAY"
        margin = sample_absolute_margin(artifact, winner=winner, rng=rng)
        if winner == "HOME":
            home_final.append(h + margin)
            away_final.append(a)
        else:
            home_final.append(h)
            away_final.append(a + margin)
    return home_final, away_final


__all__ = [
    "ExtraInningMarginArtifact",
    "ExtraInningMarginRow",
    "ExtraInningMarginUnavailable",
    "evaluate_extra_inning_margin_candidate",
    "fit_extra_inning_margin_candidate",
    "resolve_tied_nine_inning_samples",
    "sample_absolute_margin",
]
