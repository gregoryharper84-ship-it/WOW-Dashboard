"""Research-only NCAAF player-prop challenger builder.

Class C boundary: this module may fit inert CANDIDATE artifacts from governed
CFBD sporting outcomes. It cannot certify, promote, activate, publish, rank, or
execute. 2023 is training, 2024 calibration, 2025 untouched holdout, and 2026 is
reserved for forward evidence.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from typing import Any, Sequence

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error
from sklearn.preprocessing import StandardScaler

from ncaaf_cfbd_hydrator import SourceSnapshot
from ncaaf_prop_historical_adapter import PHASE1_STAT_TYPES, build_schedule_index, normalize_player_stats_corpus

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
PROVIDER_IDENTITY = "WOW_PROP_FITTED_MODEL_V1"
MODEL_FAMILY = "NCAAF_PROP_EMPIRICAL_RESIDUAL_RIDGE_V1"
CALIBRATOR_VERSION = "NCAAF_PROP_EMPIRICAL_RESIDUAL_CAL_V1"
FEATURE_SCHEMA_VERSION = "NCAAF_PROP_FEATURES_V1"
FEATURE_TRANSFORM_VERSION = "NCAAF_PROP_ROLLING_FORM_V1"
SPECIALIST_VERSION = "wow.ncaaf-player-prop-probability-expert@candidate-1"
ARTIFACT_FORMAT = "JSON_NCAAF_PROP_EMPIRICAL_RESIDUAL_V1"
TRAIN_SEASON = 2023
CALIBRATION_SEASON = 2024
HOLDOUT_SEASON = 2025
FORWARD_SEASON = 2026
MIN_PRIOR_GAMES = 4
MIN_TRAIN_ROWS = 100
MIN_CAL_ROWS = 75
MIN_HOLDOUT_ROWS = 75
RIDGE_ALPHA = 8.0
MAX_MAE_RATIO = 1.05
MAX_COVERAGE_ERROR = 0.12
BLEND_GRID = tuple(round(float(v), 2) for v in np.linspace(0.10, 1.0, 10))
QUANTILE_GRID = tuple(round(float(v), 3) for v in np.linspace(0.0, 1.0, 101))

ROUTES: dict[str, dict[str, str]] = {
    "PASSING_YARDS": {"aux": "PASS_ATTEMPTS", "scope": "PLAYER"},
    "COMPLETIONS": {"aux": "PASS_ATTEMPTS", "scope": "PLAYER"},
    "RUSHING_YARDS": {"aux": "RUSH_ATTEMPTS", "scope": "PLAYER"},
    "RECEPTIONS": {"aux": "PASS_ATTEMPTS", "scope": "TEAM"},
    "RECEIVING_YARDS": {"aux": "RECEPTIONS", "scope": "PLAYER"},
    "RUSH_ATTEMPTS": {"aux": "RUSH_ATTEMPTS", "scope": "TEAM"},
    "PASS_ATTEMPTS": {"aux": "PASS_ATTEMPTS", "scope": "TEAM"},
}
COUNT_ROUTES = frozenset({"COMPLETIONS", "RECEPTIONS", "RUSH_ATTEMPTS", "PASS_ATTEMPTS"})
FEATURE_NAMES = (
    "l5_stat_mean", "l3_stat_mean", "last_stat",
    "l5_aux_mean", "l3_aux_mean", "last_aux",
)


class NCAAFPropChallengerError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}:{detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class FeatureRow:
    stat_type: str
    participant_id: str
    team_id: str
    opponent_id: str
    event_id: str
    season: int
    event_start_time: str
    features: tuple[float, ...]
    target: float
    prior_game_n: int
    historical_reconstruction: bool = True
    market_features_used: bool = False
    can_execute: bool = False


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _mean(values: Sequence[float]) -> float:
    return float(sum(values) / len(values))


def build_feature_rows(
    player_snapshots: Sequence[SourceSnapshot],
    game_snapshots: Sequence[SourceSnapshot],
    *,
    stat_type: str,
) -> list[FeatureRow]:
    route = str(stat_type or "").strip().upper()
    if route not in ROUTES:
        raise NCAAFPropChallengerError("NCAAF_PROP_ROUTE_UNSUPPORTED", route)
    schedule = build_schedule_index(game_snapshots)
    outcomes = normalize_player_stats_corpus(
        player_snapshots,
        game_snapshots=game_snapshots,
        stat_types=PHASE1_STAT_TYPES,
    )
    by_player: dict[tuple[str, str, str], float] = {}
    team_total: dict[tuple[str, str, str], float] = {}
    for out in outcomes:
        key = (out.identity.event_id, out.identity.participant_id, out.stat_type)
        by_player[key] = float(out.actual_value)
        team_key = (out.identity.event_id, out.identity.team_id, out.stat_type)
        team_total[team_key] = team_total.get(team_key, 0.0) + float(out.actual_value)

    cfg = ROUTES[route]
    per_player: dict[str, list[dict[str, Any]]] = {}
    explicit_zero_n = 0
    for out in outcomes:
        if out.stat_type != route:
            continue
        game = schedule.get(out.identity.event_id)
        if game is None:
            raise NCAAFPropChallengerError("NCAAF_PROP_SCHEDULE_UNRESOLVED", out.identity.event_id)
        target = float(out.actual_value)
        if not math.isfinite(target) or (route in COUNT_ROUTES and target < 0):
            raise NCAAFPropChallengerError("NCAAF_PROP_TARGET_INVALID", route)
        explicit_zero_n += int(target == 0.0)
        if cfg["scope"] == "PLAYER":
            aux = by_player.get((out.identity.event_id, out.identity.participant_id, cfg["aux"]))
        else:
            aux = team_total.get((out.identity.event_id, out.identity.team_id, cfg["aux"]))
        if aux is None or not math.isfinite(float(aux)) or float(aux) <= 0:
            continue
        per_player.setdefault(out.identity.participant_id, []).append({
            "target": target,
            "aux": float(aux),
            "event_id": out.identity.event_id,
            "team_id": out.identity.team_id,
            "opponent_id": out.identity.opponent_id,
            "season": int(game.season),
            "start": game.event_start_time,
        })

    if route in {"RECEPTIONS", "RECEIVING_YARDS"} and explicit_zero_n == 0:
        raise NCAAFPropChallengerError("NCAAF_PROP_ZERO_OUTCOME_COVERAGE_UNPROVEN", route)

    rows: list[FeatureRow] = []
    for participant_id, records in per_player.items():
        records.sort(key=lambda r: (r["start"], r["event_id"]))
        history: list[tuple[float, float]] = []
        for rec in records:
            if len(history) >= MIN_PRIOR_GAMES:
                h5 = history[-5:]
                h3 = h5[-3:]
                stats5 = [x[0] for x in h5]
                stats3 = [x[0] for x in h3]
                aux5 = [x[1] for x in h5]
                aux3 = [x[1] for x in h3]
                rows.append(FeatureRow(
                    stat_type=route,
                    participant_id=participant_id,
                    team_id=str(rec["team_id"]),
                    opponent_id=str(rec["opponent_id"]),
                    event_id=str(rec["event_id"]),
                    season=int(rec["season"]),
                    event_start_time=rec["start"].isoformat(),
                    features=(
                        _mean(stats5), _mean(stats3), stats5[-1],
                        _mean(aux5), _mean(aux3), aux5[-1],
                    ),
                    target=float(rec["target"]),
                    prior_game_n=len(history),
                ))
            history.append((float(rec["target"]), float(rec["aux"])))
    rows.sort(key=lambda r: (r.event_start_time, r.event_id, r.participant_id))
    if not rows:
        raise NCAAFPropChallengerError("NCAAF_PROP_FEATURE_ROWS_EMPTY", route)
    return rows


def _split(rows: Sequence[FeatureRow]) -> tuple[list[FeatureRow], list[FeatureRow], list[FeatureRow]]:
    train = [r for r in rows if r.season == TRAIN_SEASON]
    cal = [r for r in rows if r.season == CALIBRATION_SEASON]
    holdout = [r for r in rows if r.season == HOLDOUT_SEASON]
    if len(train) < MIN_TRAIN_ROWS:
        raise NCAAFPropChallengerError("NCAAF_PROP_TRAIN_ROWS_BELOW_MINIMUM", str(len(train)))
    if len(cal) < MIN_CAL_ROWS:
        raise NCAAFPropChallengerError("NCAAF_PROP_CAL_ROWS_BELOW_MINIMUM", str(len(cal)))
    if len(holdout) < MIN_HOLDOUT_ROWS:
        raise NCAAFPropChallengerError("NCAAF_PROP_HOLDOUT_ROWS_BELOW_MINIMUM", str(len(holdout)))
    if max(r.event_start_time for r in train) >= min(r.event_start_time for r in cal):
        raise NCAAFPropChallengerError("NCAAF_PROP_TRAIN_CAL_CHRONOLOGY_INVALID")
    if max(r.event_start_time for r in cal) >= min(r.event_start_time for r in holdout):
        raise NCAAFPropChallengerError("NCAAF_PROP_CAL_HOLDOUT_CHRONOLOGY_INVALID")
    return train, cal, holdout


def _matrix(rows: Sequence[FeatureRow]) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray([r.features for r in rows], dtype=float)
    y = np.asarray([r.target for r in rows], dtype=float)
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise NCAAFPropChallengerError("NCAAF_PROP_NONFINITE_MATRIX")
    return x, y


def _coverage(actual: np.ndarray, pred: np.ndarray, residuals: np.ndarray, level: float) -> float:
    tail = (1.0 - level) / 2.0
    lo, hi = np.quantile(residuals, [tail, 1.0 - tail])
    return float(np.mean((actual >= pred + lo) & (actual <= pred + hi)))


def fit_candidate(rows: Sequence[FeatureRow], *, stat_type: str, training_code_sha: str) -> dict[str, Any]:
    route = str(stat_type or "").strip().upper()
    train, cal, holdout = _split(rows)
    x_train, y_train = _matrix(train)
    x_cal, y_cal = _matrix(cal)
    x_hold, y_hold = _matrix(holdout)
    scaler = StandardScaler()
    z_train = scaler.fit_transform(x_train)
    z_cal = scaler.transform(x_cal)
    z_hold = scaler.transform(x_hold)
    model = Ridge(alpha=RIDGE_ALPHA, fit_intercept=True).fit(z_train, y_train)
    fitted_cal = model.predict(z_cal)
    fitted_hold = model.predict(z_hold)
    baseline_cal = x_cal[:, 0]
    baseline_hold = x_hold[:, 0]
    best_weight, best_mae = 0.10, float("inf")
    for weight in BLEND_GRID:
        pred = (1.0 - weight) * baseline_cal + weight * fitted_cal
        loss = float(mean_absolute_error(y_cal, pred))
        if loss < best_mae:
            best_weight, best_mae = weight, loss
    pred_cal = (1.0 - best_weight) * baseline_cal + best_weight * fitted_cal
    pred_hold = (1.0 - best_weight) * baseline_hold + best_weight * fitted_hold
    residuals = y_cal - pred_cal
    hold_mae = float(mean_absolute_error(y_hold, pred_hold))
    base_mae = float(mean_absolute_error(y_hold, baseline_hold))
    mae_ratio = hold_mae / base_mae if base_mae > 0 else float("inf")
    cov80 = _coverage(y_hold, pred_hold, residuals, 0.80)
    cov90 = _coverage(y_hold, pred_hold, residuals, 0.90)
    blockers: list[str] = []
    if not math.isfinite(mae_ratio) or mae_ratio > MAX_MAE_RATIO:
        blockers.append("NCAAF_PROP_FAILS_NAIVE_MAE_GATE")
    if abs(cov80 - 0.80) > MAX_COVERAGE_ERROR:
        blockers.append("NCAAF_PROP_80P_COVERAGE_OUT_OF_TOLERANCE")
    if abs(cov90 - 0.90) > MAX_COVERAGE_ERROR:
        blockers.append("NCAAF_PROP_90P_COVERAGE_OUT_OF_TOLERANCE")
    quantiles = [float(v) for v in np.quantile(residuals, QUANTILE_GRID)]
    dkw_epsilon_95 = math.sqrt(math.log(40.0) / (2.0 * len(cal)))
    dataset_hash = _hash([asdict(r) for r in rows if r.season <= HOLDOUT_SEASON])
    payload = {
        "model_kind": "EMPIRICAL_RESIDUAL_RIDGE_BLEND_V1",
        "stat_type": route,
        "feature_names": list(FEATURE_NAMES),
        "feature_mean": [float(v) for v in scaler.mean_],
        "feature_scale": [float(v if v > 1e-12 else 1.0) for v in scaler.scale_],
        "coef": [float(v) for v in model.coef_],
        "intercept": float(model.intercept_),
        "blend_weight_fitted": float(best_weight),
        "residual_quantile_grid": list(QUANTILE_GRID),
        "residual_quantiles": quantiles,
        "dkw_epsilon_95": float(dkw_epsilon_95),
        "count_route": route in COUNT_ROUTES,
        "support_min": 0.0,
        "min_prior_games": MIN_PRIOR_GAMES,
        "aux_stat_type": ROUTES[route]["aux"],
        "aux_scope": ROUTES[route]["scope"],
    }
    checksum = _hash(payload)
    metrics = {
        "split_policy": "2023_TRAIN_2024_CAL_2025_UNTOUCHED_HOLDOUT_2026_FORWARD_RESERVED",
        "train_n": len(train),
        "calibration_n": len(cal),
        "holdout_n": len(holdout),
        "holdout_mae": hold_mae,
        "baseline_l5_mae": base_mae,
        "mae_ratio_to_baseline": mae_ratio,
        "holdout_80_interval_coverage": cov80,
        "holdout_90_interval_coverage": cov90,
        "forward_season_reserved": FORWARD_SEASON,
        "market_features_used": False,
        "research_screen_pass": not blockers,
        "blockers": blockers,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }
    return {
        "provider_identity": PROVIDER_IDENTITY,
        "model_family": MODEL_FAMILY,
        "model_artifact_version": f"NCAAF_{route}_CHALLENGER_V1_{dataset_hash[:12]}_{checksum[:12]}",
        "calibrator_version": CALIBRATOR_VERSION,
        "sport": "NCAAF",
        "stat_type": route,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_transform_version": FEATURE_TRANSFORM_VERSION,
        "specialist_version": SPECIALIST_VERSION,
        "certification_id": f"CANDIDATE-NOT-CERTIFIED-{checksum[:16]}",
        "lifecycle_state": "CANDIDATE",
        "training_dataset_hash": dataset_hash,
        "training_code_sha": str(training_code_sha),
        "artifact_checksum": checksum,
        "artifact_format": ARTIFACT_FORMAT,
        "artifact_payload": payload,
        "supported_line_min": None,
        "supported_line_max": None,
        "training_rows": len(rows),
        "validation_metrics": metrics,
        "promoted": False,
        "active": False,
        "probability_publishable": False,
        "can_execute": False,
        "candidate_research_active": True,
    }


def build_candidate_package(
    player_snapshots: Sequence[SourceSnapshot],
    game_snapshots: Sequence[SourceSnapshot],
    *,
    training_code_sha: str,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    blocked: dict[str, dict[str, str]] = {}
    for route in PHASE1_STAT_TYPES:
        try:
            rows = build_feature_rows(player_snapshots, game_snapshots, stat_type=route)
            candidates.append(fit_candidate(rows, stat_type=route, training_code_sha=training_code_sha))
        except NCAAFPropChallengerError as exc:
            blocked[route] = {"code": exc.code, "detail": exc.detail}
    return {
        "status": "NCAAF_PROP_CHALLENGER_PACKAGE_BUILT" if candidates else "BLOCKED",
        "candidate_n": len(candidates),
        "blocked_routes": blocked,
        "candidates": candidates,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


__all__ = [
    "AUTOMATIC_CERTIFICATION", "AUTOMATIC_PROMOTION", "CAN_EXECUTE",
    "CALIBRATOR_VERSION", "FEATURE_NAMES", "FORWARD_SEASON", "MODEL_FAMILY",
    "NCAAFPropChallengerError", "PROBABILITY_PUBLISHABLE", "ROUTES",
    "FeatureRow", "build_candidate_package", "build_feature_rows", "fit_candidate",
]
