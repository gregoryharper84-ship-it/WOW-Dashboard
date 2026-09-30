"""Governed Class-C NFL full-game total-points challenger.

Initial use case: the Pick'em Monday-night total-points tiebreaker.

The candidate is intentionally parsimonious. It learns only from settled
sporting data available before kickoff. Sportsbook totals, spreads, moneylines,
implied probabilities, consensus projections and generic LLM estimates are not
features and are never substituted for the fitted model.

V1 lifecycle is fixed before looking at the terminal holdout:
* 2021-2023: fit;
* 2024: ridge-alpha selection + empirical residual interval calibration;
* 2025: untouched validation;
* 2026: forward reserve only.

Nothing in this module can publish a betting probability, rank a wager, or
execute a wager/order. Promotion is a separate governed decision.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from math import isfinite, sqrt
from statistics import mean
from typing import Any, Mapping, Sequence

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

CAN_EXECUTE = False
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS = True
AUTOMATIC_PROMOTION = False
AUTOMATIC_CERTIFICATION = False
PROBABILITY_PUBLISHABLE = False
RANK_ELIGIBLE = False
GLOBAL_TERMINAL_REDUCER = "V17_TERMINAL_REDUCER"
MODEL_PROGRAM = "NFL_TOTAL_POINTS_TIEBREAKER_V1"
MODEL_FAMILY = "NFL_TOTAL_POINTS_RIDGE_V1"
FEATURE_SCHEMA_VERSION = "NFL_TOTAL_POINTS_PREGAME_COMPOSITE_V1"
USE_CASE = "PICKEM_TIEBREAKER_ONLY"
TRAIN_SEASONS = (2021, 2022, 2023)
CALIBRATION_SEASON = 2024
VALIDATION_SEASON = 2025
FORWARD_RESERVE_SEASON = 2026
CURRENT_SEASON_REFERENCE_GAMES = 8.0
PREVIOUS_SEASON_GAMES = 18
RECENT_GAMES = 3
MIN_TOTAL_PRIOR_GAMES = 3
RIDGE_ALPHA_GRID = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0)
MIN_TRAIN_N = 700
MIN_CALIBRATION_N = 250
MIN_VALIDATION_N = 250

FEATURE_ORDER = (
    "naive_projection",
    "recent_total_environment",
    "epa_environment",
    "pace_environment",
    "turnover_environment",
    "current_season_share",
    "rest_environment",
    "early_week_1_4",
)


class NFLTotalPointsChallengerUnavailable(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class TotalPointsTrainingRow:
    event_id: str
    event_start_time: str
    feature_as_of: str
    season: int
    week: int
    total_points: float
    features: Mapping[str, float]
    naive_projection: float
    source_manifest_sha256: str


@dataclass(frozen=True)
class TotalPointsArtifact:
    sport: str
    use_case: str
    model_family: str
    feature_schema_version: str
    feature_names: tuple[str, ...]
    scaler_mean: tuple[float, ...]
    scaler_scale: tuple[float, ...]
    coefficients: tuple[float, ...]
    intercept: float
    calibration_residuals: tuple[float, ...]
    ridge_alpha: float
    train_rows: int
    calibration_rows: int
    validation_rows: int
    training_dataset_hash: str
    interval_lower_quantile: float = 0.10
    interval_upper_quantile: float = 0.90
    can_execute: bool = False


def _dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode()).hexdigest()


def _avg(rows: Sequence[Mapping[str, Any]], field: str) -> float:
    vals = [float(row[field]) for row in rows if row.get(field) is not None and isfinite(float(row[field]))]
    return mean(vals) if vals else 0.0


def _team_metrics(history: Sequence[Mapping[str, Any]], *, season: int, target_time: datetime) -> dict[str, float]:
    prior = [r for r in history if _dt(r["event_time"]) < target_time]
    current = [r for r in prior if int(r.get("season") or -1) == season]
    previous = [r for r in prior if int(r.get("season") or -1) == season - 1][-PREVIOUS_SEASON_GAMES:]
    recent = current[-RECENT_GAMES:]
    current_share = min(len(current), int(CURRENT_SEASON_REFERENCE_GAMES)) / CURRENT_SEASON_REFERENCE_GAMES
    previous_share = len(previous) / float(PREVIOUS_SEASON_GAMES)
    last_time = _dt(prior[-1]["event_time"]) if prior else target_time
    rest_days = max(0.0, min(21.0, (target_time - last_time).total_seconds() / 86400.0))
    return {
        "total_prior_games": float(len(prior)),
        "current_season_share": float(current_share),
        "previous_season_share": float(previous_share),
        "season_points_for": _avg(current, "points_for"),
        "season_points_against": _avg(current, "points_against"),
        "previous_points_for": _avg(previous, "points_for"),
        "previous_points_against": _avg(previous, "points_against"),
        "recent3_game_total": _avg(recent, "game_total"),
        "season_offensive_epa": _avg(current, "offensive_epa_mean"),
        "season_defensive_epa": _avg(current, "defensive_epa_mean"),
        "season_pace": _avg(current, "pace"),
        "season_turnovers": _avg(current, "turnovers"),
        "rest_days": float(rest_days),
    }


def _blend(metrics: Mapping[str, float], current_field: str, previous_field: str) -> float:
    current_share = float(metrics["current_season_share"])
    previous_share = float(metrics["previous_season_share"])
    current = float(metrics[current_field])
    previous = float(metrics[previous_field])
    if current_share <= 0 and previous_share <= 0:
        return 0.0
    if current_share <= 0:
        return previous
    if previous_share <= 0:
        return current
    w = min(1.0, current_share)
    return w * current + (1.0 - w) * previous


def _naive_projection(home: Mapping[str, float], away: Mapping[str, float]) -> float:
    home_pf = _blend(home, "season_points_for", "previous_points_for")
    home_pa = _blend(home, "season_points_against", "previous_points_against")
    away_pf = _blend(away, "season_points_for", "previous_points_for")
    away_pa = _blend(away, "season_points_against", "previous_points_against")
    if max(home_pf, home_pa, away_pf, away_pa) <= 0:
        return 44.0
    return float((home_pf + away_pa + away_pf + home_pa) / 2.0)


def _feature_map(*, week: int, home: Mapping[str, float], away: Mapping[str, float]) -> dict[str, float]:
    naive = _naive_projection(home, away)
    recent_values = [float(home["recent3_game_total"]), float(away["recent3_game_total"])]
    recent_nonzero = [v for v in recent_values if v > 0]
    recent = mean(recent_nonzero) if recent_nonzero else naive
    raw = {
        "naive_projection": naive,
        "recent_total_environment": float(recent),
        "epa_environment": float(home["season_offensive_epa"] + away["season_offensive_epa"] + home["season_defensive_epa"] + away["season_defensive_epa"]),
        "pace_environment": float(home["season_pace"] + away["season_pace"]),
        "turnover_environment": float(home["season_turnovers"] + away["season_turnovers"]),
        "current_season_share": float((home["current_season_share"] + away["current_season_share"]) / 2.0),
        "rest_environment": float((home["rest_days"] + away["rest_days"]) / 2.0),
        "early_week_1_4": float(int(week) <= 4),
    }
    return {name: float(raw[name]) for name in FEATURE_ORDER}


def _history_row(event: Mapping[str, Any], *, home: bool) -> dict[str, Any]:
    hs = float(event.get("home_score") or 0.0)
    as_ = float(event.get("away_score") or 0.0)
    if home:
        return {"event_time": _dt(event["event_start_time"]).isoformat(), "season": int(event.get("season") or 0), "points_for": hs, "points_against": as_, "game_total": hs + as_, "offensive_epa_mean": float(event.get("home_attack_style_index") or 0.0), "defensive_epa_mean": -float(event.get("home_defense_style_index") or 0.0), "pace": float(event.get("home_pace_or_tempo_index") or 0.0), "turnovers": float(event.get("home_turnovers") or 0.0)}
    return {"event_time": _dt(event["event_start_time"]).isoformat(), "season": int(event.get("season") or 0), "points_for": as_, "points_against": hs, "game_total": hs + as_, "offensive_epa_mean": float(event.get("away_attack_style_index") or 0.0), "defensive_epa_mean": -float(event.get("away_defense_style_index") or 0.0), "pace": float(event.get("away_pace_or_tempo_index") or 0.0), "turnovers": float(event.get("away_turnovers") or 0.0)}


def build_rows(events: Sequence[Mapping[str, Any]]) -> tuple[list[TotalPointsTrainingRow], list[dict[str, Any]]]:
    history: dict[str, list[dict[str, Any]]] = {}
    rows: list[TotalPointsTrainingRow] = []
    metadata: list[dict[str, Any]] = []
    ordered = sorted(events, key=lambda r: (_dt(r["event_start_time"]), str(r.get("event_id") or "")))
    for event in ordered:
        if str(event.get("game_type") or "REG").strip().upper() not in {"REG", "R"}:
            continue
        start = _dt(event["event_start_time"])
        season = int(event.get("season") or 0)
        week = int(event.get("week") or 0)
        home = str(event.get("home_team") or "").strip().upper()
        away = str(event.get("away_team") or "").strip().upper()
        if not home or not away or home == away or week <= 0:
            continue
        if event.get("home_score") is None or event.get("away_score") is None:
            continue
        hm = _team_metrics(history.get(home, []), season=season, target_time=start)
        am = _team_metrics(history.get(away, []), season=season, target_time=start)
        if hm["total_prior_games"] >= MIN_TOTAL_PRIOR_GAMES and am["total_prior_games"] >= MIN_TOTAL_PRIOR_GAMES:
            features = _feature_map(week=week, home=hm, away=am)
            if not all(isfinite(v) for v in features.values()):
                raise NFLTotalPointsChallengerUnavailable("NFL_TOTAL_POINTS_NONFINITE_FEATURE", str(event.get("event_id")))
            manifest = {"program": MODEL_PROGRAM, "feature_schema_version": FEATURE_SCHEMA_VERSION, "event_id": str(event.get("event_id") or ""), "season": season, "week": week, "source_manifest": dict(event.get("source_manifest") or {}), "feature_as_of": (start - timedelta(seconds=1)).isoformat(), "sportsbook_total_used": False, "market_probability_used": False, "moneyline_probability_used": False, "spread_used": False, "target_result_used_in_features": False}
            rows.append(TotalPointsTrainingRow(event_id=str(event.get("event_id") or ""), event_start_time=start.isoformat(), feature_as_of=(start - timedelta(seconds=1)).isoformat(), season=season, week=week, total_points=float(event["home_score"]) + float(event["away_score"]), features=features, naive_projection=float(features["naive_projection"]), source_manifest_sha256=_hash(manifest)))
            metadata.append({"season": season, "week": week, "manifest": manifest})
        history.setdefault(home, []).append(_history_row(event, home=True))
        history.setdefault(away, []).append(_history_row(event, home=False))
    if not rows:
        raise NFLTotalPointsChallengerUnavailable("NFL_TOTAL_POINTS_ROWS_EMPTY", "no leakage-safe total-points rows")
    return rows, metadata


def _partition(rows: Sequence[TotalPointsTrainingRow]):
    return ([r for r in rows if r.season in TRAIN_SEASONS], [r for r in rows if r.season == CALIBRATION_SEASON], [r for r in rows if r.season == VALIDATION_SEASON], [r for r in rows if r.season == FORWARD_RESERVE_SEASON])


def _matrix(rows: Sequence[TotalPointsTrainingRow]):
    x = np.asarray([[float(r.features[name]) for name in FEATURE_ORDER] for r in rows], dtype=float)
    y = np.asarray([float(r.total_points) for r in rows], dtype=float)
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise NFLTotalPointsChallengerUnavailable("NFL_TOTAL_POINTS_NONFINITE_INPUT", "matrix contains non-finite values")
    return x, y


def _metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    residual = actual - predicted
    ae = np.abs(residual)
    return {"n": float(len(actual)), "mae": float(np.mean(ae)), "rmse": float(sqrt(np.mean(np.square(residual)))), "median_absolute_error": float(np.median(ae)), "bias_actual_minus_predicted": float(np.mean(residual)), "within_7_points": float(np.mean(ae <= 7.0)), "within_10_points": float(np.mean(ae <= 10.0))}


def _worst_errors(rows: Sequence[TotalPointsTrainingRow], predicted: np.ndarray, limit: int = 10) -> list[dict[str, Any]]:
    ranked = sorted(({"event_id": row.event_id, "season": row.season, "week": row.week, "actual_total": row.total_points, "projected_total": float(pred), "absolute_error": abs(row.total_points - float(pred))} for row, pred in zip(rows, predicted)), key=lambda r: (-float(r["absolute_error"]), str(r["event_id"])))
    return ranked[:limit]


def train_candidate(events: Sequence[Mapping[str, Any]], *, ridge_alpha_grid: Sequence[float] = RIDGE_ALPHA_GRID, min_train_n: int = MIN_TRAIN_N, min_calibration_n: int = MIN_CALIBRATION_N, min_validation_n: int = MIN_VALIDATION_N) -> tuple[TotalPointsArtifact, dict[str, Any]]:
    rows, _ = build_rows(events)
    train, calibration, validation, reserve = _partition(rows)
    if len(train) < min_train_n or len(calibration) < min_calibration_n or len(validation) < min_validation_n:
        raise NFLTotalPointsChallengerUnavailable("NFL_TOTAL_POINTS_SEASON_HOLDOUT_INSUFFICIENT", f"train={len(train)} calibration={len(calibration)} validation={len(validation)}")
    x_train, y_train = _matrix(train)
    x_cal, y_cal = _matrix(calibration)
    x_val, y_val = _matrix(validation)
    scaler = StandardScaler().fit(x_train)
    z_train, z_cal, z_val = scaler.transform(x_train), scaler.transform(x_cal), scaler.transform(x_val)
    alpha_results: list[dict[str, Any]] = []
    selected_alpha: float | None = None
    selected_model: Ridge | None = None
    selected_cal_pred: np.ndarray | None = None
    best_rmse = float("inf")
    for alpha in ridge_alpha_grid:
        if float(alpha) <= 0:
            continue
        model = Ridge(alpha=float(alpha)).fit(z_train, y_train)
        cal_pred = model.predict(z_cal)
        metrics = _metrics(y_cal, cal_pred)
        alpha_results.append({"alpha": float(alpha), **metrics})
        if metrics["rmse"] < best_rmse - 1e-12:
            best_rmse = metrics["rmse"]
            selected_alpha, selected_model, selected_cal_pred = float(alpha), model, cal_pred
    if selected_model is None or selected_cal_pred is None or selected_alpha is None:
        raise NFLTotalPointsChallengerUnavailable("NFL_TOTAL_POINTS_ALPHA_GRID_EMPTY", "no positive ridge alpha")
    val_pred = selected_model.predict(z_val)
    cal_residuals = np.asarray(y_cal - selected_cal_pred, dtype=float)
    if len(cal_residuals) < min_calibration_n or float(np.std(cal_residuals)) <= 1e-9:
        raise NFLTotalPointsChallengerUnavailable("NFL_TOTAL_POINTS_CALIBRATION_RESIDUALS_INVALID", "insufficient residual distribution")
    q10, q50, q90 = (float(np.quantile(cal_residuals, q)) for q in (0.10, 0.50, 0.90))
    coverage = float(np.mean((y_val >= val_pred + q10) & (y_val <= val_pred + q90)))
    naive_val = np.asarray([float(r.naive_projection) for r in validation], dtype=float)
    train_mean = float(np.mean(y_train))
    mean_val = np.full_like(y_val, train_mean)
    artifact = TotalPointsArtifact(sport="NFL", use_case=USE_CASE, model_family=MODEL_FAMILY, feature_schema_version=FEATURE_SCHEMA_VERSION, feature_names=tuple(FEATURE_ORDER), scaler_mean=tuple(float(v) for v in scaler.mean_), scaler_scale=tuple(float(v if abs(v) > 1e-12 else 1.0) for v in scaler.scale_), coefficients=tuple(float(v) for v in selected_model.coef_), intercept=float(selected_model.intercept_), calibration_residuals=tuple(float(v) for v in cal_residuals), ridge_alpha=selected_alpha, train_rows=len(train), calibration_rows=len(calibration), validation_rows=len(validation), training_dataset_hash=_hash([asdict(r) for r in train + calibration]))
    validation_metrics = _metrics(y_val, val_pred)
    naive_metrics = _metrics(y_val, naive_val)
    mean_metrics = _metrics(y_val, mean_val)
    early_mask = np.asarray([r.week <= 4 for r in validation], dtype=bool)
    early_metrics = _metrics(y_val[early_mask], val_pred[early_mask]) if bool(np.any(early_mask)) else None
    screen = {"beats_naive_mae": validation_metrics["mae"] < naive_metrics["mae"], "beats_training_mean_mae": validation_metrics["mae"] < mean_metrics["mae"], "absolute_bias_under_2_5": abs(validation_metrics["bias_actual_minus_predicted"]) <= 2.5, "empirical_80_interval_coverage_between_0_70_and_0_90": 0.70 <= coverage <= 0.90}
    screen["passes"] = all(screen.values())
    receipt = {"status": "EXPERIMENT_CREATED", "code": "NFL_TOTAL_POINTS_V1_HOLDOUT_REPLAY_COMPLETE", "model_program": MODEL_PROGRAM, "model_family": MODEL_FAMILY, "feature_schema_version": FEATURE_SCHEMA_VERSION, "use_case": USE_CASE, "train_rows": len(train), "calibration_rows": len(calibration), "validation_rows": len(validation), "forward_reserve_rows": len(reserve), "ridge_alpha_grid": [float(v) for v in ridge_alpha_grid], "calibration_model_selection": alpha_results, "selected_ridge_alpha": selected_alpha, "validation_metrics": validation_metrics, "naive_validation_metrics": naive_metrics, "training_mean_validation_metrics": mean_metrics, "early_week_validation_metrics": early_metrics, "empirical_80_interval_coverage": coverage, "calibration_residual_quantiles": {"q10": q10, "q50": q50, "q90": q90}, "worst_validation_errors": _worst_errors(validation, val_pred), "research_screen": screen, "market_features_used": False, "sportsbook_total_used": False, "market_probability_substitution_used": False, "moneyline_probability_used": False, "automatic_certification": False, "automatic_promotion": False, "probability_publishable": False, "rank_eligible": False, "global_terminal_reducer": GLOBAL_TERMINAL_REDUCER, "can_execute": False}
    return artifact, receipt


def _predict(artifact: TotalPointsArtifact, features: Mapping[str, float]) -> float:
    raw = np.asarray([float(features[name]) for name in artifact.feature_names], dtype=float)
    z = (raw - np.asarray(artifact.scaler_mean)) / np.asarray(artifact.scaler_scale)
    value = float(artifact.intercept + np.dot(z, np.asarray(artifact.coefficients)))
    if not isfinite(value):
        raise NFLTotalPointsChallengerUnavailable("NFL_TOTAL_POINTS_PREDICTION_NONFINITE", "projection is non-finite")
    return value


def score_tiebreaker(artifact: TotalPointsArtifact, *, historical_events: Sequence[Mapping[str, Any]], event_id: str, event_start_time: Any, season: int, week: int, home_team: str, away_team: str) -> dict[str, Any]:
    start = _dt(event_start_time)
    home = str(home_team or "").strip().upper()
    away = str(away_team or "").strip().upper()
    if not home or not away or home == away:
        raise NFLTotalPointsChallengerUnavailable("NFL_TOTAL_POINTS_EVENT_IDENTITY_INVALID", str(event_id))
    history: dict[str, list[dict[str, Any]]] = {}
    for event in sorted(historical_events, key=lambda r: (_dt(r["event_start_time"]), str(r.get("event_id") or ""))):
        if _dt(event["event_start_time"]) >= start:
            break
        if str(event.get("game_type") or "REG").strip().upper() not in {"REG", "R"}:
            continue
        if event.get("home_score") is None or event.get("away_score") is None:
            continue
        h = str(event.get("home_team") or "").strip().upper()
        a = str(event.get("away_team") or "").strip().upper()
        if not h or not a:
            continue
        history.setdefault(h, []).append(_history_row(event, home=True))
        history.setdefault(a, []).append(_history_row(event, home=False))
    hm = _team_metrics(history.get(home, []), season=int(season), target_time=start)
    am = _team_metrics(history.get(away, []), season=int(season), target_time=start)
    if hm["total_prior_games"] < MIN_TOTAL_PRIOR_GAMES or am["total_prior_games"] < MIN_TOTAL_PRIOR_GAMES:
        raise NFLTotalPointsChallengerUnavailable("NFL_TOTAL_POINTS_PREGAME_HISTORY_INSUFFICIENT", f"{away}@{home}: home_prior={hm['total_prior_games']} away_prior={am['total_prior_games']}")
    features = _feature_map(week=int(week), home=hm, away=am)
    projection = _predict(artifact, features)
    residuals = np.asarray(artifact.calibration_residuals, dtype=float)
    q10 = float(np.quantile(residuals, artifact.interval_lower_quantile))
    q90 = float(np.quantile(residuals, artifact.interval_upper_quantile))
    return {"status": "MODEL_PROJECTED_HOLD", "code": "NFL_TOTAL_POINTS_TIEBREAKER_PROJECTED", "event_id": str(event_id), "sport": "NFL", "home_team": home, "away_team": away, "season": int(season), "week": int(week), "projected_total_points": projection, "prediction_interval": {"coverage_target": 0.80, "lower": projection + q10, "upper": projection + q90}, "model_family": artifact.model_family, "feature_schema_version": artifact.feature_schema_version, "training_dataset_hash": artifact.training_dataset_hash, "ridge_alpha": artifact.ridge_alpha, "sportsbook_total_used": False, "market_probability_used": False, "moneyline_probability_used": False, "probability_publishable": False, "rank_eligible": False, "can_execute": False}


def artifact_to_json(artifact: TotalPointsArtifact) -> str:
    return json.dumps(asdict(artifact), sort_keys=True, separators=(",", ":"))


def artifact_from_json(payload: str | bytes) -> TotalPointsArtifact:
    raw = json.loads(payload)
    for key in ("feature_names", "scaler_mean", "scaler_scale", "coefficients", "calibration_residuals"):
        raw[key] = tuple(raw[key])
    artifact = TotalPointsArtifact(**raw)
    if artifact.can_execute is not False or artifact.sport != "NFL" or artifact.use_case != USE_CASE:
        raise NFLTotalPointsChallengerUnavailable("NFL_TOTAL_POINTS_ARTIFACT_INVALID", "artifact governance fields invalid")
    return artifact


__all__ = ["AUTOMATIC_CERTIFICATION", "AUTOMATIC_PROMOTION", "CAN_EXECUTE", "FEATURE_ORDER", "FEATURE_SCHEMA_VERSION", "MODEL_FAMILY", "MODEL_PROGRAM", "NFLTotalPointsChallengerUnavailable", "TotalPointsArtifact", "TotalPointsTrainingRow", "artifact_from_json", "artifact_to_json", "build_rows", "score_tiebreaker", "train_candidate"]
