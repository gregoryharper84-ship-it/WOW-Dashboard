"""Research-only NFL spread V2 with separated-season sporting context.

The candidate reuses the leakage-safe NFL event-context feature family that
separates current-season and prior-season state. It fits HOME scoring margin,
uses 2021-2023 for fitting, 2024 only for residual calibration, and leaves 2025
as the terminal untouched validation season. 2026 is forward reserve only.

The sportsbook spread is never a feature. Historical nflverse spread_line is
bound only after the sporting margin distribution is frozen, solely for exact-
line certification research. Nothing here can certify, promote, publish, rank,
or execute a wager.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from nfl_event_data_p1 import download_asset, schedules_asset
from v17.nfl_event_context_challenger import (
    CALIBRATION_SEASON,
    FEATURE_ORDER,
    FORWARD_RESERVE_SEASON,
    MIN_TOTAL_PRIOR_GAMES,
    TRAIN_SEASONS,
    VALIDATION_SEASON,
    _dt,
    _feature_map,
    _side_metrics,
)
from v17.spread_certification_replay import (
    _read_csv,
    _source_manifest,
    bind_nflverse_close_proxies,
)
from v17.spread_historical_close_proxy import (
    NFLVERSE_EVIDENCE_CLASS,
    evaluate_historical_close_proxy,
)
from v17.spread_margin_challenger import (
    MarginDistributionArtifact,
    MarginTrainingRow,
    SpreadChallengerUnavailable,
)
from v17.team_state_challenger_maintenance import _nfl_events

CAN_EXECUTE = False
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS = True
GLOBAL_TERMINAL_REDUCER = "V17_TERMINAL_REDUCER"
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
PROBABILITY_PUBLISHABLE = False
RANK_ELIGIBLE = False
MODEL_PROGRAM = "NFL_SPREAD_CONTEXT_MARGIN_V2"
MODEL_FAMILY = "NFL_SPREAD_CONTEXT_RIDGE_EMPIRICAL_V2"
FEATURE_SCHEMA_VERSION = "NFL_SPREAD_CONTEXT_FEATURES_V2"
RIDGE_ALPHA = 4.0
MIN_TRAIN_N = 750
MIN_CALIBRATION_N = 250
MIN_VALIDATION_N = 250


def _hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def build_margin_rows(events: Sequence[Mapping[str, Any]]) -> tuple[list[MarginTrainingRow], list[dict[str, Any]]]:
    history: dict[str, list[dict[str, Any]]] = {}
    rows: list[MarginTrainingRow] = []
    metadata: list[dict[str, Any]] = []

    ordered = sorted(events, key=lambda row: (_dt(row["event_start_time"]), str(row["event_id"])))
    for event in ordered:
        start = _dt(event["event_start_time"])
        season = int(event.get("season") or 0)
        week = int(event.get("week") or 0)
        home = str(event.get("home_team") or "").strip()
        away = str(event.get("away_team") or "").strip()
        if not home or not away or home == away or week <= 0:
            continue

        hh = history.get(home, [])
        ah = history.get(away, [])
        hm = _side_metrics(hh, season=season, target_time=start)
        am = _side_metrics(ah, season=season, target_time=start)
        home_score = float(event.get("home_score") or 0.0)
        away_score = float(event.get("away_score") or 0.0)

        if hm["total_prior_games"] >= MIN_TOTAL_PRIOR_GAMES and am["total_prior_games"] >= MIN_TOTAL_PRIOR_GAMES:
            features = _feature_map(week=week, home=hm, away=am)
            raw_event_id = str(event.get("event_id") or "")
            event_id = raw_event_id.split(":", 1)[1] if raw_event_id.startswith("NFL:") else raw_event_id
            manifest = {
                "program": MODEL_PROGRAM,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "event_id": event_id,
                "season": season,
                "week": week,
                "source_manifest": dict(event.get("source_manifest") or {}),
                "current_and_previous_season_separated": True,
                "v1_recent8_blend_reused": False,
                "market_features_used": False,
                "spread_line_used_as_feature": False,
                "moneyline_probability_used": False,
                "manual_probability_adjustments": False,
                "target_game_participant_ids_used": False,
            }
            rows.append(MarginTrainingRow(
                event_id=event_id,
                event_start_time=start.isoformat(),
                feature_as_of=(start - timedelta(seconds=1)).isoformat(),
                margin=int(round(home_score - away_score)),
                features={name: float(features[name]) for name in FEATURE_ORDER},
                source_manifest_sha256=_hash(manifest),
            ))
            metadata.append({"season": season, "week": week, "source_manifest": manifest})

        home_prior_strength = float(hm["season_point_diff"])
        away_prior_strength = float(am["season_point_diff"])
        history.setdefault(home, []).append({
            "event_time": start.isoformat(), "season": season, "won": home_score > away_score,
            "point_diff": home_score - away_score,
            "process_margin": float(event.get("home_process_margin") or 0.0),
            "schedule_adjusted_point_diff": home_score - away_score + away_prior_strength,
            "turnovers": float(event.get("home_turnovers") or 0.0),
            "sacks_allowed": float(event.get("home_sacks_allowed") or 0.0),
            "special_teams_epa": float(event.get("home_special_teams_epa") or 0.0),
            "qb_ids": list(event.get("home_history_lineup_ids") or []),
        })
        history.setdefault(away, []).append({
            "event_time": start.isoformat(), "season": season, "won": away_score > home_score,
            "point_diff": away_score - home_score,
            "process_margin": float(event.get("away_process_margin") or 0.0),
            "schedule_adjusted_point_diff": away_score - home_score + home_prior_strength,
            "turnovers": float(event.get("away_turnovers") or 0.0),
            "sacks_allowed": float(event.get("away_sacks_allowed") or 0.0),
            "special_teams_epa": float(event.get("away_special_teams_epa") or 0.0),
            "qb_ids": list(event.get("away_history_lineup_ids") or []),
        })

    if not rows:
        raise SpreadChallengerUnavailable("NFL_SPREAD_CONTEXT_ROWS_EMPTY", "no leakage-safe NFL V2 margin rows")
    return rows, metadata


def _partition(rows: Sequence[MarginTrainingRow], metadata: Sequence[Mapping[str, Any]]):
    train: list[MarginTrainingRow] = []
    calibration: list[MarginTrainingRow] = []
    validation: list[MarginTrainingRow] = []
    forward_reserve = 0
    for row, meta in zip(rows, metadata):
        season = int(meta.get("season") or 0)
        if season in TRAIN_SEASONS:
            train.append(row)
        elif season == CALIBRATION_SEASON:
            calibration.append(row)
        elif season == VALIDATION_SEASON:
            validation.append(row)
        elif season == FORWARD_RESERVE_SEASON:
            forward_reserve += 1
    if len(train) < MIN_TRAIN_N or len(calibration) < MIN_CALIBRATION_N or len(validation) < MIN_VALIDATION_N:
        raise SpreadChallengerUnavailable(
            "NFL_SPREAD_CONTEXT_SEASON_HOLDOUT_INSUFFICIENT",
            f"train={len(train)} calibration={len(calibration)} validation={len(validation)}",
        )
    return train, calibration, validation, forward_reserve


def _matrix(rows: Sequence[MarginTrainingRow]):
    x = np.asarray([[float(row.features[name]) for name in FEATURE_ORDER] for row in rows], dtype=float)
    y = np.asarray([float(row.margin) for row in rows], dtype=float)
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise SpreadChallengerUnavailable("NFL_SPREAD_CONTEXT_NONFINITE_INPUT", "non-finite V2 margin input")
    return x, y


def train_candidate(events: Sequence[Mapping[str, Any]], *, ridge_alpha: float = RIDGE_ALPHA):
    rows, metadata = build_margin_rows(events)
    train, calibration, validation, forward_reserve = _partition(rows, metadata)
    x_train, y_train = _matrix(train)
    x_cal, y_cal = _matrix(calibration)
    scaler = StandardScaler().fit(x_train)
    model = Ridge(alpha=float(ridge_alpha)).fit(scaler.transform(x_train), y_train)
    residuals = tuple(float(actual - pred) for actual, pred in zip(y_cal, model.predict(scaler.transform(x_cal))))
    if len(residuals) < MIN_CALIBRATION_N or float(np.std(np.asarray(residuals))) <= 1e-9:
        raise SpreadChallengerUnavailable("NFL_SPREAD_CONTEXT_RESIDUALS_INVALID", "V2 residual calibration is insufficient")
    artifact = MarginDistributionArtifact(
        sport="NFL",
        model_family=MODEL_FAMILY,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        feature_names=tuple(FEATURE_ORDER),
        scaler_mean=tuple(float(v) for v in scaler.mean_),
        scaler_scale=tuple(float(v if abs(v) > 1e-12 else 1.0) for v in scaler.scale_),
        coefficients=tuple(float(v) for v in model.coef_),
        intercept=float(model.intercept_),
        calibration_residuals=residuals,
        train_rows=len(train),
        calibration_rows=len(calibration),
        test_rows=len(validation),
        training_dataset_hash=_hash([asdict(row) for row in train + calibration]),
        ridge_alpha=float(ridge_alpha),
    )
    return artifact, validation, forward_reserve


def run_nfl_context_v2_close_proxy_replay(*, client: Any, ridge_alpha: float = RIDGE_ALPHA, download_fn: Any = download_asset) -> dict[str, Any]:
    events = _nfl_events(client)
    artifact, validation, forward_reserve = train_candidate(events, ridge_alpha=ridge_alpha)
    from tempfile import TemporaryDirectory
    with TemporaryDirectory(prefix="wow-nfl-spread-v2-") as directory:
        captured = download_fn(schedules_asset(), directory)
        source_rows = _read_csv(captured.local_path)
        evidence, binding_audit = bind_nflverse_close_proxies(
            test_event_ids=[row.event_id for row in validation],
            source_rows=source_rows,
        )
        exact = evaluate_historical_close_proxy(
            artifact=artifact,
            test_rows=validation,
            evidence_by_event=evidence,
            evidence_class=NFLVERSE_EVIDENCE_CLASS,
        )
        source_manifest = _source_manifest(captured)

    no_skill_brier = 0.25
    no_skill_log_loss = float(np.log(2.0))
    screen = {
        "beats_no_skill_brier": bool(exact["cover_brier"] < no_skill_brier),
        "beats_no_skill_log_loss": bool(exact["cover_log_loss"] < no_skill_log_loss),
        "ece_within_0_10": bool(exact["cover_ece"] is not None and exact["cover_ece"] <= 0.10),
    }
    screen["passes"] = all(screen.values())
    return {
        "status": "EXPERIMENT_CREATED",
        "code": "NFL_SPREAD_CONTEXT_V2_EXACT_LINE_REPLAY_COMPLETE",
        "sport": "NFL",
        "model_program": MODEL_PROGRAM,
        "model_family": MODEL_FAMILY,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "artifact_train_rows": artifact.train_rows,
        "artifact_calibration_rows": artifact.calibration_rows,
        "artifact_test_rows": artifact.test_rows,
        "forward_reserve_rows": forward_reserve,
        "training_dataset_hash": artifact.training_dataset_hash,
        "exact_line_metrics": exact,
        "binding_audit": binding_audit,
        "source_manifest": source_manifest,
        "research_screen": screen,
        "reference": {"no_skill_brier": no_skill_brier, "no_skill_log_loss": no_skill_log_loss},
        "market_features_used": False,
        "spread_line_used_as_feature": False,
        "market_probability_substitution_used": False,
        "moneyline_probability_used": False,
        "v1_recent8_blend_reused": False,
        "separated_current_previous_season": True,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "global_terminal_reducer": GLOBAL_TERMINAL_REDUCER,
        "can_execute": False,
    }


__all__ = ["MODEL_FAMILY", "FEATURE_SCHEMA_VERSION", "build_margin_rows", "train_candidate", "run_nfl_context_v2_close_proxy_replay"]
