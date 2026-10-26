"""NHL total-goals distribution *research challenger*, never a serving scorer.

Consumes source-hashed historical game_record() rows from nhl_candidate_pipeline.
Only prior settled results contribute to reconstructed features; within the
same scheduled timestamp, no outcomes are visible to any other fixture.
These retrospectively reconstructed features are NOT archived pregame snapshots.

Pre-registered season split: 2021-22 fit, 2023 calibration, 2024 untouched
validation, >=2025 forward reserve. Poisson regression is a testable baseline,
NOT an approved/governed NHL totals probability or goalie-aware specialist.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from math import isfinite
from typing import Any, Mapping, Sequence
import json

import numpy as np
from scipy.stats import poisson
from sklearn.linear_model import PoissonRegressor
from sklearn.preprocessing import StandardScaler

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
RANK_ELIGIBLE = False
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
CONTROLLING_TERMINAL = "V17_TERMINAL_REDUCER"
MODEL_FAMILY = "NHL_TOTAL_GOALS_POISSON_RESEARCH_V1"
SCHEMA_VERSION = "NHL_TOTALS_PRIOR_GAMES_V1"
TRAIN_SEASONS = frozenset({2021, 2022})
CALIBRATION_SEASON = 2023
VALIDATION_SEASON = 2024
FORWARD_RESERVE_FROM = 2025
MIN_PRIOR = 5
WINDOW = 10
FIXED_ALPHA = 1.0  # predefined; neither calibration nor validation selects this
FEATURES = (
    "home_goals_for_prior10", "home_goals_against_prior10",
    "away_goals_for_prior10", "away_goals_against_prior10",
    "home_rest_days", "away_rest_days",
)


class NHLTotalsResearchError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


@dataclass(frozen=True)
class TotalRow:
    event_id: str
    start_utc: str
    season_start_year: int
    home: str
    away: str
    total_goals: int
    reconstructed_as_of: str
    features: tuple[float, ...]
    source_hash: str
    historically_reconstructed: bool = True


@dataclass(frozen=True)
class TotalsResearchArtifact:
    family: str
    schema: str
    train_n: int
    calibration_n: int
    validation_n: int
    scaler_mean: tuple[float, ...]
    scaler_scale: tuple[float, ...]
    coefficients: tuple[float, ...]
    intercept: float
    calibration_mean_multiplier: float
    train_dataset_hash: str
    calibration_dataset_hash: str
    validation_dataset_hash: str
    research_only: bool = True
    probability_publishable: bool = False
    can_execute: bool = False


def _hash(obj: Any) -> str:
    return sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _timestamp(value: Any) -> datetime:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("timezone required")
        return stamp.astimezone(timezone.utc)
    except (ValueError, TypeError) as exc:
        raise NHLTotalsResearchError("NHL_TOTALS_EVENT_TIME_INVALID", str(value)) from exc


def _game(obj: Mapping[str, Any]) -> dict[str, Any]:
    """Strictly accept first-party settled NHL research pipeline output."""
    try:
        if obj.get("sport") != "NHL" or obj.get("source_provider") != "NHL_PUBLIC_WEB_API":
            raise ValueError("wrong source or sport")
        if obj.get("historical_reconstruction") is not True or obj.get("can_execute") is not False:
            raise ValueError("not historical research data")
        event_id = str(obj["official_event_id"]).strip()
        home, away = str(obj["home_team"]).strip().upper(), str(obj["away_team"]).strip().upper()
        source_hash = str(obj["source_payload_sha256"]).lower()
        season = str(obj["season"])
        if len(season) != 8 or not season.isdigit() or int(season[4:]) != int(season[:4]) + 1:
            raise ValueError("invalid season identity")
        if not event_id or not home or not away or home == away:
            raise ValueError("invalid event identity")
        if len(source_hash) != 64 or any(c not in "0123456789abcdef" for c in source_hash):
            raise ValueError("source hash missing")
        if obj.get("game_type") not in (None, 2, "2"):
            raise ValueError("not NHL regular season")
        goals = (obj["home_score"], obj["away_score"])
        if any(type(n) is not int or n < 0 for n in goals) or goals[0] == goals[1]:
            raise ValueError("invalid final scores")
        start = _timestamp(obj["event_start_time"])
        return {
            "id": event_id, "start": start, "season": int(season[:4]),
            "home": home, "away": away, "hgoals": goals[0], "agoals": goals[1],
            "source_hash": source_hash,
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise NHLTotalsResearchError("NHL_TOTALS_SOURCE_ROW_INVALID", str(exc)) from exc


def build_total_rows(games: Sequence[Mapping[str, Any]]) -> tuple[list[TotalRow], dict[str, Any]]:
    if not games:
        raise NHLTotalsResearchError("NHL_TOTALS_SOURCE_ROWS_EMPTY", "no records")
    parsed = [_game(row) for row in games]
    by_id: dict[str, dict[str, Any]] = {}
    for g in parsed:
        prev = by_id.get(g["id"])
        if prev is not None and prev != g:
            raise NHLTotalsResearchError("NHL_TOTALS_SOURCE_IDENTITY_CONFLICT", g["id"])
        if prev is not None:
            raise NHLTotalsResearchError("NHL_TOTALS_DUPLICATE_EVENT", g["id"])
        by_id[g["id"]] = g
    ordered = sorted(parsed, key=lambda r: (r["start"], r["id"]))
    history: dict[tuple[int, str], deque[tuple[int, int, datetime]]] = defaultdict(
        lambda: deque(maxlen=WINDOW)
    )
    rows: list[TotalRow] = []
    i = 0
    while i < len(ordered):
        stamp = ordered[i]["start"]
        j = i
        while j < len(ordered) and ordered[j]["start"] == stamp:
            j += 1
        group = ordered[i:j]
        appearing = [name for g in group for name in (g["home"], g["away"])]
        if len(set(appearing)) != len(appearing):
            raise NHLTotalsResearchError("NHL_TOTALS_OVERLAPPING_TEAM_EVENT", stamp.isoformat())
        # Snapshot every event first; only THEN apply the group of settled results.
        for g in group:
            h = history[(g["season"], g["home"])]
            a = history[(g["season"], g["away"])]
            if len(h) < MIN_PRIOR or len(a) < MIN_PRIOR:
                continue
            if h[-1][2] >= stamp or a[-1][2] >= stamp:
                raise NHLTotalsResearchError("NHL_TOTALS_CHRONOLOGY_INVALID", g["id"])
            feat = (
                sum(x[0] for x in h) / len(h), sum(x[1] for x in h) / len(h),
                sum(x[0] for x in a) / len(a), sum(x[1] for x in a) / len(a),
                min(14.0, (stamp - h[-1][2]).total_seconds() / 86400),
                min(14.0, (stamp - a[-1][2]).total_seconds() / 86400),
            )
            if not all(isfinite(x) and x >= 0 for x in feat):
                raise NHLTotalsResearchError("NHL_TOTALS_FEATURE_INVALID", g["id"])
            rows.append(TotalRow(
                event_id=g["id"], start_utc=stamp.isoformat(),
                season_start_year=g["season"], home=g["home"], away=g["away"],
                total_goals=g["hgoals"] + g["agoals"],
                reconstructed_as_of=(stamp - timedelta(microseconds=1)).isoformat(),
                features=feat, source_hash=g["source_hash"],
            ))
        for g in group:
            history[(g["season"], g["home"])].append((g["hgoals"], g["agoals"], stamp))
            history[(g["season"], g["away"])].append((g["agoals"], g["hgoals"], stamp))
        i = j
    return rows, {"source_rows": len(parsed), "eligible_rows": len(rows),
                  "historical_reconstruction": True,
                  "archive_pregame_snapshot_claimed": False,
                  "market_features_used": False,
                  "complete_source_hash": _hash([(g["id"], g["source_hash"]) for g in ordered])}


def _matrix(rows: Sequence[TotalRow]) -> tuple[np.ndarray, np.ndarray]:
    x = np.array([r.features for r in rows], dtype=float)
    y = np.array([r.total_goals for r in rows], dtype=float)
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise NHLTotalsResearchError("NHL_TOTALS_NONFINITE_DATA", "feature matrix invalid")
    return x, y


def _mean(features: np.ndarray, artifact: TotalsResearchArtifact) -> np.ndarray:
    scaled = (features - np.array(artifact.scaler_mean)) / np.array(artifact.scaler_scale)
    logits = artifact.intercept + scaled @ np.array(artifact.coefficients)
    if not np.isfinite(logits).all() or (abs(logits) > 80).any():
        raise NHLTotalsResearchError("NHL_TOTALS_MEAN_INVALID", "extreme/nonfinite model output")
    return np.exp(logits) * artifact.calibration_mean_multiplier


def _metrics(rows: Sequence[TotalRow], mean_goals: np.ndarray) -> dict[str, float]:
    true_goals = np.array([r.total_goals for r in rows], dtype=int)
    if len(true_goals) != len(mean_goals) or not np.isfinite(mean_goals).all() or (mean_goals <= 0).any():
        raise NHLTotalsResearchError("NHL_TOTALS_VALIDATION_INVALID", "invalid validation distribution")
    p_under_55 = poisson.cdf(5, mean_goals)
    p_under_60 = poisson.cdf(5, mean_goals)
    y_under = (true_goals <= 5).astype(float)
    return {
        "n": int(len(rows)),
        "mean_absolute_error": float(np.mean(np.abs(true_goals - mean_goals))),
        "mean_actual_goals": float(np.mean(true_goals)),
        "mean_predicted_goals": float(np.mean(mean_goals)),
        "poisson_negative_log_likelihood": float(-np.mean(poisson.logpmf(true_goals, mean_goals))),
        "under_5_5_brier": float(np.mean((p_under_55 - y_under) ** 2)),
        "under_6_0_unconditional_win_brier": float(np.mean((p_under_60 - y_under) ** 2)),
        "under_6_0_mean_push_probability": float(np.mean(poisson.pmf(6, mean_goals))),
        "under_6_0_observed_push_frequency": float(np.mean(true_goals == 6)),
        "under_6_0_push_calibration_bias": float(np.mean(poisson.pmf(6, mean_goals)) - np.mean(true_goals == 6)),
    }


def fit_total_goals_candidate(
    rows: Sequence[TotalRow], *, min_train: int = 300,
    min_calibration: int = 120, min_validation: int = 120,
) -> tuple[TotalsResearchArtifact, dict[str, Any]]:
    train = [r for r in rows if r.season_start_year in TRAIN_SEASONS]
    cal = [r for r in rows if r.season_start_year == CALIBRATION_SEASON]
    val = [r for r in rows if r.season_start_year == VALIDATION_SEASON]
    reserve = [r for r in rows if r.season_start_year >= FORWARD_RESERVE_FROM]
    if min(len(train) / min_train, len(cal) / min_calibration, len(val) / min_validation) < 1:
        raise NHLTotalsResearchError("NHL_TOTALS_HOLDOUT_INSUFFICIENT", f"train={len(train)} cal={len(cal)} val={len(val)}")
    ids = [r.event_id for r in train + cal + val + reserve]
    if len(set(ids)) != len(ids):
        raise NHLTotalsResearchError("NHL_TOTALS_SPLIT_OVERLAP", "repeated event across cohorts")
    train_x, train_y = _matrix(train)
    cal_x, cal_y = _matrix(cal)
    val_x, _ = _matrix(val)
    scaler = StandardScaler().fit(train_x)
    model = PoissonRegressor(alpha=FIXED_ALPHA, max_iter=1000).fit(scaler.transform(train_x), train_y)
    cal_base = model.predict(scaler.transform(cal_x))
    multiplier = float(np.mean(cal_y) / np.mean(cal_base))
    if not isfinite(multiplier) or multiplier <= 0 or not (0.25 <= multiplier <= 4):
        raise NHLTotalsResearchError("NHL_TOTALS_CALIBRATION_INVALID", "invalid empirical mean offset")
    artifact = TotalsResearchArtifact(
        family=MODEL_FAMILY, schema=SCHEMA_VERSION, train_n=len(train),
        calibration_n=len(cal), validation_n=len(val),
        scaler_mean=tuple(float(v) for v in scaler.mean_),
        scaler_scale=tuple(float(v) for v in scaler.scale_),
        coefficients=tuple(float(v) for v in model.coef_), intercept=float(model.intercept_),
        calibration_mean_multiplier=multiplier,
        train_dataset_hash=_hash([asdict(r) for r in train]),
        calibration_dataset_hash=_hash([asdict(r) for r in cal]),
        validation_dataset_hash=_hash([asdict(r) for r in val]),
    )
    val_mean = _mean(val_x, artifact)
    # Exploratory parity comparison. The 2024-25 holdout was consulted when
    # selecting parity as a challenger: it is NO LONGER an untouched test set.
    from v17.nhl_total_goals_parity_challenger import (
        fit_parity_calibration, parity_adjusted_research_outcomes,
    )
    parity_cal = fit_parity_calibration(cal, season=CALIBRATION_SEASON)
    odd_share = parity_cal["odd_total_share"]
    predicted_parity_u55 = np.asarray([
        parity_adjusted_research_outcomes(float(mu), odd_share, line=5.5, side="UNDER")[
            "research_win_probability"
        ] for mu in val_mean
    ])
    predicted_parity_six = np.asarray([
        parity_adjusted_research_outcomes(float(mu), odd_share, line=6.0, side="UNDER")[
            "research_push_probability"
        ] for mu in val_mean
    ])
    observed_under = np.asarray([r.total_goals <= 5 for r in val], dtype=float)
    observed_six = np.asarray([r.total_goals == 6 for r in val], dtype=float)
    predicted_poisson_u55 = poisson.cdf(5, val_mean)
    brier_gain = (
        np.square(predicted_poisson_u55 - observed_under)
        - np.square(predicted_parity_u55 - observed_under)
    )
    parity_comparison = {
        "status": "EXPLORATORY_DEVELOPMENT_COMPARISON_ONLY",
        "holdout_used_during_model_selection": True,
        "calibration_odd_share": float(odd_share),
        "under55_parity_brier": float(np.mean(np.square(predicted_parity_u55 - observed_under))),
        "under55_poisson_brier": float(np.mean(np.square(predicted_poisson_u55 - observed_under))),
        "paired_under55_brier_improvement": float(np.mean(brier_gain)),
        "paired_under55_brier_standard_error": float(
            np.std(brier_gain, ddof=1) / np.sqrt(len(brier_gain))
        ),
        "parity_predicted_exact_six_probability": float(np.mean(predicted_parity_six)),
        "poisson_predicted_exact_six_probability": float(np.mean(poisson.pmf(6, val_mean))),
        "observed_exact_six_frequency": float(np.mean(observed_six)),
        "calibrated_lower_bound": None,
        "probability_publishable": False, "can_execute": False,
    }
    # Historical constant-rate Poisson is a research baseline only, not an input/prior.
    baseline_rate = float(np.mean(train_y))
    baseline_mean = np.full(len(val), baseline_rate)
    receipt = {
        "status": "EXPERIMENT_CREATED",
        "model_family": MODEL_FAMILY, "feature_schema_version": SCHEMA_VERSION,
        "train_n": len(train), "calibration_n": len(cal), "validation_n": len(val),
        "forward_reserve_n": len(reserve), "fixed_alpha": FIXED_ALPHA,
        "validation": _metrics(val, val_mean),
        "constant_baseline_validation": _metrics(val, baseline_mean),
        "parity_research_comparison": parity_comparison,
        "training_dataset_hash": artifact.train_dataset_hash,
        "calibration_dataset_hash": artifact.calibration_dataset_hash,
        "validation_dataset_hash": artifact.validation_dataset_hash,
        "input_goalie_status": "NOT_MODELED_RESEARCH_ONLY",
        "historical_reconstruction": True, "immutable_pregame_snapshots": False,
        "certified_calibrated_probability": False,
        "calibrated_lower_bound": None,
        "automatic_certification": False, "automatic_promotion": False,
        "probability_publishable": False, "rank_eligible": False,
        "terminal_authority": CONTROLLING_TERMINAL, "can_execute": False,
    }
    return artifact, receipt


def research_outcomes_from_mean(mean_goals: float, *, line: float, side: str) -> dict[str, Any]:
    """Count-CDF demonstration only; not certified, not publishable."""
    if not isinstance(side, str) or side.upper() not in {"UNDER", "OVER"}:
        raise NHLTotalsResearchError("NHL_TOTALS_DIRECTION_INVALID", str(side))
    if type(line) not in (float, int) or not isfinite(line) or line < 0 or line * 2 != round(line * 2):
        raise NHLTotalsResearchError("NHL_TOTALS_LINE_UNSUPPORTED", str(line))
    if type(mean_goals) not in (float, int) or not isfinite(mean_goals) or mean_goals <= 0:
        raise NHLTotalsResearchError("NHL_TOTALS_MEAN_INVALID", str(mean_goals))
    p_under = float(poisson.cdf(int(np.ceil(line)) - 1, mean_goals))
    p_push = float(poisson.pmf(int(line), mean_goals)) if float(line).is_integer() else 0.0
    p_over = max(0.0, 1.0 - p_under - p_push)
    win, loss = (p_under, p_over) if side.upper() == "UNDER" else (p_over, p_under)
    if not abs(win + loss + p_push - 1.0) < 1e-8:
        raise NHLTotalsResearchError("NHL_TOTALS_PROBABILITY_INVALID", "probability mass")
    return {"research_win_probability": win, "research_push_probability": p_push,
            "research_loss_probability": loss, "line": float(line), "side": side.upper(),
            "model_family": MODEL_FAMILY, "certified_model_probability": None,
            "calibrated_probability": None, "calibrated_lower_bound": None,
            "probability_publishable": False, "rank_eligible": False, "can_execute": False}
