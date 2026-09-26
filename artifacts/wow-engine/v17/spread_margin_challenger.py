"""Governed V17 point-spread margin-distribution challenger.

Class C research/shadow infrastructure only.  This module fits a scoring-margin
model from leakage-safe pregame sporting features, converts the fitted margin
plus a held-out residual distribution into an exact-line cover distribution,
and evaluates calibration on a chronological holdout.

The exact sportsbook spread is a *query threshold*.  It is never a training
feature and is never converted from moneyline probability or implied odds.
Nothing in this module self-certifies, self-promotes, publishes a sporting
probability to production, or executes a wager.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from typing import Any, Mapping, Sequence

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from v17.team_state_intelligence import FEATURE_FAMILY_VERSION, build_team_state, paired_matchup_features

CAN_EXECUTE = False
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS = True
GLOBAL_TERMINAL_REDUCER = "V17_TERMINAL_REDUCER"
RUNTIME_GENERATION = "V17_ACTIVE"
MODEL_PROGRAM = "WOW_V17_SPREAD_MARGIN_DISTRIBUTION_CHALLENGER_V1"
LIFECYCLE_STATE = "CHALLENGER"
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
PROBABILITY_PUBLISHABLE = False
MARKET_PROBABILITY_SUBSTITUTION_ALLOWED = False
MONEYLINE_TO_SPREAD_CONVERSION_ALLOWED = False
MANUAL_PROBABILITY_ADJUSTMENTS_ALLOWED = False

SUPPORTED_SPORTS = ("NFL", "NBA", "NCAAF", "NCAAB", "WNBA")
SPORT_CONFIG: dict[str, dict[str, Any]] = {
    "NFL": {"expected_season_games": 17, "large_spread": 7.0, "line_min": -21.0, "line_max": 21.0},
    "NBA": {"expected_season_games": 82, "large_spread": 7.0, "line_min": -15.0, "line_max": 15.0},
    "NCAAF": {"expected_season_games": 12, "large_spread": 10.0, "line_min": -28.0, "line_max": 28.0},
    "NCAAB": {"expected_season_games": 31, "large_spread": 8.0, "line_min": -20.0, "line_max": 20.0},
    "WNBA": {"expected_season_games": 44, "large_spread": 7.0, "line_min": -15.0, "line_max": 15.0},
}


class SpreadChallengerUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class MarginTrainingRow:
    event_id: str
    event_start_time: str
    feature_as_of: str
    margin: int
    features: Mapping[str, float]
    source_manifest_sha256: str


@dataclass(frozen=True)
class MarginDistributionArtifact:
    sport: str
    model_family: str
    feature_schema_version: str
    feature_names: tuple[str, ...]
    scaler_mean: tuple[float, ...]
    scaler_scale: tuple[float, ...]
    coefficients: tuple[float, ...]
    intercept: float
    calibration_residuals: tuple[float, ...]
    train_rows: int
    calibration_rows: int
    test_rows: int
    training_dataset_hash: str
    ridge_alpha: float

    def payload(self) -> dict[str, Any]:
        out = asdict(self)
        out.update({
            "program": MODEL_PROGRAM,
            "lifecycle_state": LIFECYCLE_STATE,
            "automatic_certification": AUTOMATIC_CERTIFICATION,
            "automatic_promotion": AUTOMATIC_PROMOTION,
            "probability_publishable": PROBABILITY_PUBLISHABLE,
            "market_probability_substitution_allowed": MARKET_PROBABILITY_SUBSTITUTION_ALLOWED,
            "moneyline_to_spread_conversion_allowed": MONEYLINE_TO_SPREAD_CONVERSION_ALLOWED,
            "manual_probability_adjustments_allowed": MANUAL_PROBABILITY_ADJUSTMENTS_ALLOWED,
            "can_execute": CAN_EXECUTE,
        })
        return out


def _dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _hash(payload: Any) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _prior_strength(history: Sequence[Mapping[str, Any]]) -> float:
    prior = list(history)[-10:]
    return sum(float(row.get("point_diff") or 0.0) for row in prior) / len(prior) if prior else 0.0


def _append_history(history: dict[str, list[dict[str, Any]]], event: Mapping[str, Any], *,
                    home: str, away: str, start: datetime, home_score: int, away_score: int) -> None:
    home_tags = event.get("home_structural_tags") or []
    away_tags = event.get("away_structural_tags") or []
    home_tags = [home_tags] if isinstance(home_tags, str) else list(home_tags)
    away_tags = [away_tags] if isinstance(away_tags, str) else list(away_tags)
    hline = list(event.get("home_history_lineup_ids") or event.get("home_lineup_ids") or [])
    aline = list(event.get("away_history_lineup_ids") or event.get("away_lineup_ids") or [])
    prev_hline = list((history.get(home) or [{}])[-1].get("lineup_ids") or [])
    prev_aline = list((history.get(away) or [{}])[-1].get("lineup_ids") or [])
    if hline and prev_hline and set(hline) != set(prev_hline):
        home_tags.append("LINEUP_OR_STARTER_CHANGE")
    if aline and prev_aline and set(aline) != set(prev_aline):
        away_tags.append("LINEUP_OR_STARTER_CHANGE")

    def style(side: str) -> dict[str, float]:
        return {
            "attack_style_index": float(event.get(f"{side}_attack_style_index") or 0.0),
            "defense_style_index": float(event.get(f"{side}_defense_style_index") or 0.0),
            "pace_or_tempo_index": float(event.get(f"{side}_pace_or_tempo_index") or 0.0),
        }

    history.setdefault(home, []).append({
        "event_time": start.isoformat(), "season": event.get("season"), "won": home_score > away_score,
        "point_diff": home_score - away_score,
        "process_margin": float(event.get("home_process_margin") if event.get("home_process_margin") is not None else home_score - away_score),
        "opponent_strength_prior": _prior_strength(history.get(away, [])),
        "travel_load": float(event.get("home_travel_load") or 0.0),
        "congestion": float(event.get("home_congestion") or 0.0),
        "roster_ids": list(event.get("home_history_roster_ids") or event.get("home_roster_ids") or []),
        "lineup_ids": hline, "structural_tags": list(event.get("home_history_structural_tags") or home_tags),
        **style("home"),
    })
    history.setdefault(away, []).append({
        "event_time": start.isoformat(), "season": event.get("season"), "won": away_score > home_score,
        "point_diff": away_score - home_score,
        "process_margin": float(event.get("away_process_margin") if event.get("away_process_margin") is not None else away_score - home_score),
        "opponent_strength_prior": _prior_strength(history.get(home, [])),
        "travel_load": float(event.get("away_travel_load") or 0.0),
        "congestion": float(event.get("away_congestion") or 0.0),
        "roster_ids": list(event.get("away_history_roster_ids") or event.get("away_roster_ids") or []),
        "lineup_ids": aline, "structural_tags": list(event.get("away_history_structural_tags") or away_tags),
        **style("away"),
    })


def build_dynamic_margin_rows(events: Sequence[Mapping[str, Any]], *, sport: str,
                              min_prior_games: int = 5) -> tuple[list[MarginTrainingRow], tuple[str, ...]]:
    """Build leakage-safe pregame margin rows from prior team-state only."""
    sport = str(sport).upper()
    if sport not in SPORT_CONFIG:
        raise SpreadChallengerUnavailable("SPREAD_SPORT_UNSUPPORTED", f"unsupported spread sport: {sport}")
    expected = int(SPORT_CONFIG[sport]["expected_season_games"])
    history: dict[str, list[dict[str, Any]]] = {}
    rows: list[MarginTrainingRow] = []
    feature_names: tuple[str, ...] | None = None

    for event in sorted(events, key=lambda r: (_dt(r["event_start_time"]), str(r["event_id"]))):
        start = _dt(event["event_start_time"])
        home, away = str(event["home_team"]), str(event["away_team"])
        home_score, away_score = int(event["home_score"]), int(event["away_score"])
        hh, ah = history.get(home, []), history.get(away, [])
        if len(hh) >= min_prior_games and len(ah) >= min_prior_games:
            home_state = build_team_state(
                hh, target_time=start, expected_season_games=expected,
                current_roster=event.get("home_roster_ids"), current_lineup=event.get("home_lineup_ids"),
                target_season=event.get("season"),
            )
            away_state = build_team_state(
                ah, target_time=start, expected_season_games=expected,
                current_roster=event.get("away_roster_ids"), current_lineup=event.get("away_lineup_ids"),
                target_season=event.get("season"),
            )
            features = paired_matchup_features(home_state, away_state)
            feature_names = feature_names or tuple(sorted(features))
            vector = {name: float(features[name]) for name in feature_names}
            feature_as_of = (start - timedelta(seconds=1)).isoformat()
            manifest = {
                "program": MODEL_PROGRAM,
                "sport": sport,
                "feature_family_version": FEATURE_FAMILY_VERSION,
                "event_id": str(event["event_id"]),
                "feature_as_of": feature_as_of,
                "home_prior_events": len(hh),
                "away_prior_events": len(ah),
                "market_features_used": False,
                "moneyline_probability_used": False,
                "spread_line_used_as_feature": False,
                "manual_probability_adjustments": False,
                "source_manifest": dict(event.get("source_manifest") or {}),
            }
            rows.append(MarginTrainingRow(
                event_id=str(event["event_id"]),
                event_start_time=start.isoformat(),
                feature_as_of=feature_as_of,
                margin=home_score - away_score,
                features=vector,
                source_manifest_sha256=_hash(manifest),
            ))
        _append_history(history, event, home=home, away=away, start=start, home_score=home_score, away_score=away_score)

    if not rows or feature_names is None:
        raise SpreadChallengerUnavailable("SPREAD_MARGIN_ROWS_EMPTY", "no leakage-safe spread margin rows")
    return rows, feature_names


def _dataset_hash(rows: Sequence[MarginTrainingRow], feature_names: Sequence[str]) -> str:
    payload = [{
        "event_id": row.event_id,
        "event_start_time": row.event_start_time,
        "feature_as_of": row.feature_as_of,
        "margin": row.margin,
        "features": [float(row.features[name]) for name in feature_names],
        "source_manifest_sha256": row.source_manifest_sha256,
    } for row in rows]
    return _hash(payload)


def _chronological_split(rows: Sequence[MarginTrainingRow], *, min_rows: int) -> tuple[list[MarginTrainingRow], list[MarginTrainingRow], list[MarginTrainingRow]]:
    ordered = sorted(rows, key=lambda r: (_dt(r.event_start_time), r.event_id))
    if len(ordered) < min_rows:
        raise SpreadChallengerUnavailable("SPREAD_MARGIN_ROWS_INSUFFICIENT", f"need at least {min_rows} rows; got {len(ordered)}")
    n = len(ordered)
    train_end = max(1, int(n * 0.60))
    calibration_end = max(train_end + 1, int(n * 0.80))
    calibration_end = min(calibration_end, n - 1)
    train, calibration, test = ordered[:train_end], ordered[train_end:calibration_end], ordered[calibration_end:]
    if not train or not calibration or not test:
        raise SpreadChallengerUnavailable("SPREAD_CHRONOLOGICAL_SPLIT_EMPTY", "train/calibration/test split must all be non-empty")
    if _dt(train[-1].event_start_time) > _dt(calibration[0].event_start_time) or _dt(calibration[-1].event_start_time) > _dt(test[0].event_start_time):
        raise SpreadChallengerUnavailable("SPREAD_CHRONOLOGICAL_SPLIT_INVALID", "chronological split ordering violated")
    return train, calibration, test


def _matrix(rows: Sequence[MarginTrainingRow], feature_names: Sequence[str]) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray([[float(row.features[name]) for name in feature_names] for row in rows], dtype=float)
    y = np.asarray([float(row.margin) for row in rows], dtype=float)
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise SpreadChallengerUnavailable("SPREAD_MARGIN_NONFINITE_INPUT", "non-finite margin training input")
    return x, y


def train_margin_distribution_candidate(rows: Sequence[MarginTrainingRow], *, sport: str,
                                        min_rows: int = 300, ridge_alpha: float = 4.0) -> tuple[MarginDistributionArtifact, dict[str, Any]]:
    sport = str(sport).upper()
    if sport not in SPORT_CONFIG:
        raise SpreadChallengerUnavailable("SPREAD_SPORT_UNSUPPORTED", f"unsupported spread sport: {sport}")
    if ridge_alpha <= 0:
        raise SpreadChallengerUnavailable("SPREAD_RIDGE_ALPHA_INVALID", "ridge alpha must be positive")
    if not rows:
        raise SpreadChallengerUnavailable("SPREAD_MARGIN_ROWS_EMPTY", "no spread rows supplied")
    names = tuple(sorted(rows[0].features))
    if not names:
        raise SpreadChallengerUnavailable("SPREAD_FEATURES_EMPTY", "spread rows have no features")
    for row in rows:
        if tuple(sorted(row.features)) != names:
            raise SpreadChallengerUnavailable("SPREAD_FEATURE_SCHEMA_MISMATCH", "spread feature keys differ across rows")
        if _dt(row.feature_as_of) >= _dt(row.event_start_time):
            raise SpreadChallengerUnavailable("SPREAD_FEATURE_LEAKAGE", f"feature_as_of must precede event start: {row.event_id}")

    train, calibration, test = _chronological_split(rows, min_rows=min_rows)
    x_train, y_train = _matrix(train, names)
    x_cal, y_cal = _matrix(calibration, names)
    scaler = StandardScaler().fit(x_train)
    model = Ridge(alpha=float(ridge_alpha)).fit(scaler.transform(x_train), y_train)
    calibration_pred = model.predict(scaler.transform(x_cal))
    residuals = tuple(float(actual - pred) for actual, pred in zip(y_cal, calibration_pred))
    if len(residuals) < 2 or float(np.std(np.asarray(residuals, dtype=float))) <= 1e-9:
        raise SpreadChallengerUnavailable("SPREAD_RESIDUAL_DISTRIBUTION_DEGENERATE", "calibration residual distribution is degenerate")

    artifact = MarginDistributionArtifact(
        sport=sport,
        model_family=f"{sport}_SPREAD_MARGIN_RIDGE_EMPIRICAL_V1",
        feature_schema_version=f"{sport}_SPREAD_MARGIN_TEAM_STATE_V1",
        feature_names=names,
        scaler_mean=tuple(float(v) for v in scaler.mean_),
        scaler_scale=tuple(float(v if abs(v) > 1e-12 else 1.0) for v in scaler.scale_),
        coefficients=tuple(float(v) for v in model.coef_),
        intercept=float(model.intercept_),
        calibration_residuals=residuals,
        train_rows=len(train), calibration_rows=len(calibration), test_rows=len(test),
        training_dataset_hash=_dataset_hash(rows, names),
        ridge_alpha=float(ridge_alpha),
    )
    metrics = evaluate_candidate(artifact, test)
    metrics.update({
        "feature_family_version": FEATURE_FAMILY_VERSION,
        "market_features_used": False,
        "moneyline_probability_used": False,
        "spread_line_used_as_feature": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    })
    return artifact, metrics


def predict_margin_center(artifact: MarginDistributionArtifact, features: Mapping[str, float]) -> float:
    x = np.asarray([float(features[name]) for name in artifact.feature_names], dtype=float)
    mean = np.asarray(artifact.scaler_mean, dtype=float)
    scale = np.asarray(artifact.scaler_scale, dtype=float)
    coef = np.asarray(artifact.coefficients, dtype=float)
    return float(artifact.intercept + np.dot((x - mean) / scale, coef))


def _discrete_margin_samples(artifact: MarginDistributionArtifact, features: Mapping[str, float]) -> np.ndarray:
    center = predict_margin_center(artifact, features)
    residuals = np.asarray(artifact.calibration_residuals, dtype=float)
    return np.rint(center + residuals).astype(int)


def _wilson_lower(successes: int, total: int, z: float = 1.959963984540054) -> float:
    if total <= 0:
        return 0.0
    p = successes / total
    denom = 1.0 + (z * z / total)
    center = p + (z * z / (2.0 * total))
    radius = z * math.sqrt((p * (1.0 - p) / total) + (z * z / (4.0 * total * total)))
    return max(0.0, min(1.0, (center - radius) / denom))


def score_home_spread(artifact: MarginDistributionArtifact, features: Mapping[str, float], *, home_spread: float) -> dict[str, Any]:
    """Score an exact signed home spread from the fitted margin distribution.

    Convention: home margin = home_score - away_score.  The home side covers
    when `margin + home_spread > 0`; a push occurs when it equals zero.
    """
    line = float(home_spread)
    if not math.isfinite(line):
        raise SpreadChallengerUnavailable("SPREAD_LINE_INVALID", "spread line must be finite")
    samples = _discrete_margin_samples(artifact, features)
    adjusted = samples.astype(float) + line
    wins = int(np.sum(adjusted > 1e-12))
    pushes = int(np.sum(np.abs(adjusted) <= 1e-12))
    losses = int(len(samples) - wins - pushes)
    total = int(len(samples))
    p_cover, p_push, p_not_cover = wins / total, pushes / total, losses / total
    non_push = wins + losses
    p_cover_given_no_push = wins / non_push if non_push else 0.5
    return {
        "sport": artifact.sport,
        "model_family": artifact.model_family,
        "home_spread": line,
        "predicted_home_margin_center": predict_margin_center(artifact, features),
        "p_cover": p_cover,
        "p_push": p_push,
        "p_not_cover": p_not_cover,
        "p_cover_given_no_push": p_cover_given_no_push,
        "research_lower_bound_cover": _wilson_lower(wins, total),
        "distribution_sample_n": total,
        "probability_sum": p_cover + p_push + p_not_cover,
        "market_probability_substitution_used": False,
        "moneyline_to_spread_conversion_used": False,
        "manual_probability_adjustment_used": False,
        "lifecycle_state": LIFECYCLE_STATE,
        "probability_publishable": False,
        "can_execute": False,
    }


def _line_grid(sport: str) -> tuple[float, ...]:
    cfg = SPORT_CONFIG[sport]
    low, high = float(cfg["line_min"]), float(cfg["line_max"])
    count = int(round((high - low) * 2)) + 1
    return tuple(low + 0.5 * i for i in range(count))


def _binary_ece(probabilities: Sequence[float], outcomes: Sequence[int], bins: int = 10) -> float:
    if not probabilities:
        return 0.0
    p = np.asarray(probabilities, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = len(p)
    ece = 0.0
    for idx in range(bins):
        if idx == bins - 1:
            mask = (p >= edges[idx]) & (p <= edges[idx + 1])
        else:
            mask = (p >= edges[idx]) & (p < edges[idx + 1])
        if not np.any(mask):
            continue
        ece += float(np.sum(mask)) / total * abs(float(np.mean(p[mask])) - float(np.mean(y[mask])))
    return float(ece)


def _cohort_metrics(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    non_push = [row for row in records if not row["observed_push"]]
    if not non_push:
        return {"n": len(records), "non_push_n": 0, "cover_brier": None, "cover_log_loss": None, "cover_ece": None}
    p = np.asarray([float(row["p_cover_given_no_push"]) for row in non_push], dtype=float)
    y = np.asarray([1.0 if row["observed_cover"] else 0.0 for row in non_push], dtype=float)
    clipped = np.clip(p, 1e-9, 1.0 - 1e-9)
    return {
        "n": len(records),
        "non_push_n": len(non_push),
        "cover_brier": float(np.mean((p - y) ** 2)),
        "cover_log_loss": float(np.mean(-(y * np.log(clipped) + (1.0 - y) * np.log(1.0 - clipped)))),
        "cover_ece": _binary_ece(p.tolist(), y.astype(int).tolist()),
    }


def evaluate_candidate(artifact: MarginDistributionArtifact, test_rows: Sequence[MarginTrainingRow], *,
                       line_grid: Sequence[float] | None = None) -> dict[str, Any]:
    if not test_rows:
        raise SpreadChallengerUnavailable("SPREAD_TEST_ROWS_EMPTY", "spread evaluation requires test rows")
    grid = tuple(float(v) for v in (line_grid or _line_grid(artifact.sport)))
    actual_margin = np.asarray([float(row.margin) for row in test_rows], dtype=float)
    predicted_margin = np.asarray([predict_margin_center(artifact, row.features) for row in test_rows], dtype=float)
    records: list[dict[str, Any]] = []
    three_way_scores: list[float] = []
    large_cutoff = float(SPORT_CONFIG[artifact.sport]["large_spread"])

    for row in test_rows:
        for line in grid:
            scored = score_home_spread(artifact, row.features, home_spread=line)
            adjusted = float(row.margin) + line
            observed_cover = adjusted > 1e-12
            observed_push = abs(adjusted) <= 1e-12
            observed_loss = adjusted < -1e-12
            y = np.asarray([1.0 if observed_cover else 0.0, 1.0 if observed_push else 0.0, 1.0 if observed_loss else 0.0])
            p = np.asarray([scored["p_cover"], scored["p_push"], scored["p_not_cover"]])
            three_way_scores.append(float(np.sum((p - y) ** 2)))
            records.append({
                "line": line,
                "p_cover_given_no_push": scored["p_cover_given_no_push"],
                "observed_cover": observed_cover,
                "observed_push": observed_push,
                "favorite": line < 0.0,
                "underdog": line > 0.0,
                "large": abs(line) >= large_cutoff,
                "integer": abs(line - round(line)) <= 1e-12,
            })

    cohorts = {
        "all": _cohort_metrics(records),
        "favorites": _cohort_metrics([r for r in records if r["favorite"]]),
        "underdogs": _cohort_metrics([r for r in records if r["underdog"]]),
        "small_spreads": _cohort_metrics([r for r in records if not r["large"]]),
        "large_spreads": _cohort_metrics([r for r in records if r["large"]]),
        "integer_lines": _cohort_metrics([r for r in records if r["integer"]]),
        "half_lines": _cohort_metrics([r for r in records if not r["integer"]]),
    }
    return {
        "margin_mae": float(np.mean(np.abs(predicted_margin - actual_margin))),
        "margin_rmse": float(np.sqrt(np.mean((predicted_margin - actual_margin) ** 2))),
        "three_way_brier": float(np.mean(three_way_scores)),
        "cover_brier": cohorts["all"]["cover_brier"],
        "cover_log_loss": cohorts["all"]["cover_log_loss"],
        "cover_ece": cohorts["all"]["cover_ece"],
        "evaluation_rows": len(test_rows),
        "evaluation_lines": len(grid),
        "evaluation_predictions": len(records),
        "cohorts": cohorts,
    }


def research_receipt(artifact: MarginDistributionArtifact, metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "program": MODEL_PROGRAM,
        "sport": artifact.sport,
        "model_family": artifact.model_family,
        "feature_schema_version": artifact.feature_schema_version,
        "training_dataset_hash": artifact.training_dataset_hash,
        "artifact_checksum": _hash(artifact.payload()),
        "metrics": dict(metrics),
        "status": "EXPERIMENT_CREATED",
        "next_required_stage": "REAL_HISTORICAL_REPLAY_AND_GOVERNED_CERTIFICATION",
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
        "global_terminal_reducer": GLOBAL_TERMINAL_REDUCER,
        "dry_run_only": DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS,
    }


__all__ = [
    "AUTOMATIC_CERTIFICATION", "AUTOMATIC_PROMOTION", "CAN_EXECUTE", "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS",
    "GLOBAL_TERMINAL_REDUCER", "LIFECYCLE_STATE", "MarginDistributionArtifact", "MarginTrainingRow", "MODEL_PROGRAM",
    "MONEYLINE_TO_SPREAD_CONVERSION_ALLOWED", "PROBABILITY_PUBLISHABLE", "SPORT_CONFIG", "SUPPORTED_SPORTS",
    "SpreadChallengerUnavailable", "build_dynamic_margin_rows", "evaluate_candidate", "predict_margin_center",
    "research_receipt", "score_home_spread", "train_margin_distribution_candidate",
]
