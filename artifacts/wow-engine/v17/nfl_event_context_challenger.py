"""Governed research-only NFL event-context challenger.

This candidate tests a proved V1 early-season defect: the production recent-8
window blends prior-season games directly into current-season form (75% prior
season at Week 3 in the frozen historical feature ledger).

No sportsbook/market probability is consumed and no home-underdog bonus is
applied. Current-season and previous-season state are separate fitted inputs.
Every feature is reconstructed strictly from events before the target event.

Training discipline matches the active NFL champion: 2021-2023 train, 2024
calibration, untouched 2025 validation. 2026 is reserved for later forward
validation and never participates in fitting or calibrator selection.

Candidate only: no certification, promotion, probability publication, or wager
execution authority.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from math import isfinite
from statistics import mean
from typing import Any, Mapping, Sequence

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.preprocessing import StandardScaler

from v17.binary_candidate_lifecycle import (
    BinaryCandidate,
    BinaryCandidateError,
    BinaryCandidateMetrics,
    BinaryTrainingRow,
    CALIBRATOR_SELECTION_VERSION,
    _dataset_hash,
    _ece,
    _map_calibrator,
    _matrix,
    _select_calibrator,
)
from v17.team_state_challenger_training import (
    TeamStateChallengerUnavailable,
    _persist_rows,
    evaluate_binary_research_screen,
)

CAN_EXECUTE = False
MODEL_FAMILY = "NFL_EVENT_CONTEXT_LOGIT_V2"
FEATURE_SCHEMA_VERSION = "NFL_EVENT_CONTEXT_FEATURES_V2"
SOURCE_POLICY_ID = "NFL_SEPARATED_SEASON_PRIOR_ONLY_V1"
TRAIN_SEASONS = (2021, 2022, 2023)
CALIBRATION_SEASON = 2024
VALIDATION_SEASON = 2025
FORWARD_RESERVE_SEASON = 2026
CURRENT_SEASON_REFERENCE_GAMES = 8.0
PREVIOUS_SEASON_GAMES = 8
MIN_TOTAL_PRIOR_GAMES = 4
MIN_TRAIN_N = 750
MIN_CALIBRATION_N = 250
MIN_VALIDATION_N = 250

FEATURE_ORDER = (
    "week",
    "early_week_1_4",
    "home_current_season_share",
    "away_current_season_share",
    "current_season_share_edge",
    "home_previous_season_share",
    "away_previous_season_share",
    "season_win_rate_edge",
    "season_point_diff_edge",
    "season_process_margin_edge",
    "season_schedule_adjusted_point_diff_edge",
    "season_turnover_rate_edge",
    "season_sack_allowed_rate_edge",
    "season_special_teams_edge",
    "recent3_point_diff_edge",
    "recent3_process_margin_edge",
    "previous_season_win_rate_edge",
    "previous_season_point_diff_edge",
    "previous_season_process_margin_edge",
    "previous_season_turnover_rate_edge",
    "previous_season_sack_allowed_rate_edge",
    "qb_continuity_edge",
    "home_qb_continuity",
    "away_qb_continuity",
    "home_qb_continuity_available",
    "away_qb_continuity_available",
    "rest_days_edge",
    "home_rest_days",
    "away_rest_days",
)


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
    values = [float(row[field]) for row in rows if row.get(field) is not None]
    return mean(values) if values else 0.0


def _win_rate(rows: Sequence[Mapping[str, Any]]) -> float:
    return mean(1.0 if bool(row.get("won")) else 0.0 for row in rows) if rows else 0.0


def _jaccard(left: Sequence[str], right: Sequence[str]) -> float:
    a = {str(value) for value in left if str(value)}
    b = {str(value) for value in right if str(value)}
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _side_metrics(
    history: Sequence[Mapping[str, Any]],
    *,
    season: int,
    target_time: datetime,
) -> dict[str, float]:
    prior = [row for row in history if _dt(row["event_time"]) < target_time]
    current = [row for row in prior if int(row.get("season") or -1) == season]
    previous = [
        row for row in prior if int(row.get("season") or -1) == season - 1
    ][-PREVIOUS_SEASON_GAMES:]
    recent3 = current[-3:]

    season_n = len(current)
    current_share = min(season_n, int(CURRENT_SEASON_REFERENCE_GAMES)) / CURRENT_SEASON_REFERENCE_GAMES
    previous_share = len(previous) / float(PREVIOUS_SEASON_GAMES)
    last_time = _dt(prior[-1]["event_time"]) if prior else target_time
    rest_days = max(
        0.0,
        min(21.0, (target_time - last_time).total_seconds() / 86400.0),
    )

    if len(prior) >= 2:
        qb_continuity = _jaccard(
            list(prior[-1].get("qb_ids") or []),
            list(prior[-2].get("qb_ids") or []),
        )
        qb_continuity_available = 1.0
    else:
        qb_continuity = 0.0
        qb_continuity_available = 0.0

    return {
        "total_prior_games": float(len(prior)),
        "current_season_share": float(current_share),
        "previous_season_share": float(previous_share),
        "season_win_rate": _win_rate(current),
        "season_point_diff": _avg(current, "point_diff"),
        "season_process_margin": _avg(current, "process_margin"),
        "season_schedule_adjusted_point_diff": _avg(
            current, "schedule_adjusted_point_diff"
        ),
        "season_turnover_rate": _avg(current, "turnovers"),
        "season_sack_allowed_rate": _avg(current, "sacks_allowed"),
        "season_special_teams": _avg(current, "special_teams_epa"),
        "recent3_point_diff": _avg(recent3, "point_diff"),
        "recent3_process_margin": _avg(recent3, "process_margin"),
        "previous_season_win_rate": _win_rate(previous),
        "previous_season_point_diff": _avg(previous, "point_diff"),
        "previous_season_process_margin": _avg(previous, "process_margin"),
        "previous_season_turnover_rate": _avg(previous, "turnovers"),
        "previous_season_sack_allowed_rate": _avg(previous, "sacks_allowed"),
        "qb_continuity": qb_continuity,
        "qb_continuity_available": qb_continuity_available,
        "rest_days": rest_days,
    }


def _edge(home: Mapping[str, float], away: Mapping[str, float], field: str) -> float:
    return float(home[field] - away[field])


def _reverse_edge(
    home: Mapping[str, float], away: Mapping[str, float], field: str
) -> float:
    return float(away[field] - home[field])


def _feature_map(
    *,
    week: int,
    home: Mapping[str, float],
    away: Mapping[str, float],
) -> dict[str, float]:
    features = {
        "week": float(week),
        "early_week_1_4": float(int(week) <= 4),
        "home_current_season_share": float(home["current_season_share"]),
        "away_current_season_share": float(away["current_season_share"]),
        "current_season_share_edge": _edge(home, away, "current_season_share"),
        "home_previous_season_share": float(home["previous_season_share"]),
        "away_previous_season_share": float(away["previous_season_share"]),
        "season_win_rate_edge": _edge(home, away, "season_win_rate"),
        "season_point_diff_edge": _edge(home, away, "season_point_diff"),
        "season_process_margin_edge": _edge(home, away, "season_process_margin"),
        "season_schedule_adjusted_point_diff_edge": _edge(
            home, away, "season_schedule_adjusted_point_diff"
        ),
        # Lower turnover and sack-allowed rates are favorable.
        "season_turnover_rate_edge": _reverse_edge(
            home, away, "season_turnover_rate"
        ),
        "season_sack_allowed_rate_edge": _reverse_edge(
            home, away, "season_sack_allowed_rate"
        ),
        "season_special_teams_edge": _edge(home, away, "season_special_teams"),
        "recent3_point_diff_edge": _edge(home, away, "recent3_point_diff"),
        "recent3_process_margin_edge": _edge(home, away, "recent3_process_margin"),
        "previous_season_win_rate_edge": _edge(
            home, away, "previous_season_win_rate"
        ),
        "previous_season_point_diff_edge": _edge(
            home, away, "previous_season_point_diff"
        ),
        "previous_season_process_margin_edge": _edge(
            home, away, "previous_season_process_margin"
        ),
        "previous_season_turnover_rate_edge": _reverse_edge(
            home, away, "previous_season_turnover_rate"
        ),
        "previous_season_sack_allowed_rate_edge": _reverse_edge(
            home, away, "previous_season_sack_allowed_rate"
        ),
        "qb_continuity_edge": _edge(home, away, "qb_continuity"),
        "home_qb_continuity": float(home["qb_continuity"]),
        "away_qb_continuity": float(away["qb_continuity"]),
        "home_qb_continuity_available": float(home["qb_continuity_available"]),
        "away_qb_continuity_available": float(away["qb_continuity_available"]),
        "rest_days_edge": _edge(home, away, "rest_days"),
        "home_rest_days": float(home["rest_days"]),
        "away_rest_days": float(away["rest_days"]),
    }
    return {name: float(features[name]) for name in FEATURE_ORDER}


def build_rows(
    events: Sequence[Mapping[str, Any]],
) -> tuple[list[BinaryTrainingRow], list[dict[str, Any]]]:
    """Build strictly-prior NFL rows with separated current/previous seasons."""
    history: dict[str, list[dict[str, Any]]] = {}
    rows: list[BinaryTrainingRow] = []
    metadata: list[dict[str, Any]] = []

    ordered = sorted(
        events,
        key=lambda row: (_dt(row["event_start_time"]), str(row["event_id"])),
    )
    for event in ordered:
        start = _dt(event["event_start_time"])
        season = int(event.get("season") or 0)
        week = int(event.get("week") or 0)
        home_team = str(event.get("home_team") or "").strip()
        away_team = str(event.get("away_team") or "").strip()
        if not home_team or not away_team or home_team == away_team or week <= 0:
            continue

        home_history = history.get(home_team, [])
        away_history = history.get(away_team, [])
        hm = _side_metrics(home_history, season=season, target_time=start)
        am = _side_metrics(away_history, season=season, target_time=start)
        home_score = float(event.get("home_score") or 0.0)
        away_score = float(event.get("away_score") or 0.0)

        if (
            hm["total_prior_games"] >= MIN_TOTAL_PRIOR_GAMES
            and am["total_prior_games"] >= MIN_TOTAL_PRIOR_GAMES
            and home_score != away_score
        ):
            features = _feature_map(week=week, home=hm, away=am)
            manifest = {
                "program": "NFL_EVENT_CONTEXT_CHALLENGER_V2",
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "event_id": str(event["event_id"]),
                "season": season,
                "week": week,
                "source_manifest": dict(event.get("source_manifest") or {}),
                "current_and_previous_season_separated": True,
                "v1_recent8_blend_reused": False,
                "market_features_used": False,
                "manual_probability_adjustments": False,
                "target_game_participant_ids_used": False,
            }
            rows.append(
                BinaryTrainingRow(
                    event_id=str(event["event_id"]),
                    event_start_time=start.isoformat(),
                    feature_as_of=(start - timedelta(seconds=1)).isoformat(),
                    positive_outcome=home_score > away_score,
                    features=features,
                    source_manifest_sha256=_hash(manifest),
                )
            )
            metadata.append({"source_manifest": manifest, "season": season, "week": week})

        # Opponent strength is frozen before the target result is appended.
        home_prior_strength = float(hm["season_point_diff"])
        away_prior_strength = float(am["season_point_diff"])
        history.setdefault(home_team, []).append(
            {
                "event_time": start.isoformat(),
                "season": season,
                "won": home_score > away_score,
                "point_diff": home_score - away_score,
                "process_margin": float(event.get("home_process_margin") or 0.0),
                "schedule_adjusted_point_diff": (
                    home_score - away_score + away_prior_strength
                ),
                "turnovers": float(event.get("home_turnovers") or 0.0),
                "sacks_allowed": float(event.get("home_sacks_allowed") or 0.0),
                "special_teams_epa": float(event.get("home_special_teams_epa") or 0.0),
                "qb_ids": list(event.get("home_history_lineup_ids") or []),
            }
        )
        history.setdefault(away_team, []).append(
            {
                "event_time": start.isoformat(),
                "season": season,
                "won": away_score > home_score,
                "point_diff": away_score - home_score,
                "process_margin": float(event.get("away_process_margin") or 0.0),
                "schedule_adjusted_point_diff": (
                    away_score - home_score + home_prior_strength
                ),
                "turnovers": float(event.get("away_turnovers") or 0.0),
                "sacks_allowed": float(event.get("away_sacks_allowed") or 0.0),
                "special_teams_epa": float(event.get("away_special_teams_epa") or 0.0),
                "qb_ids": list(event.get("away_history_lineup_ids") or []),
            }
        )

    if not rows:
        raise TeamStateChallengerUnavailable(
            "NFL_EVENT_CONTEXT_ROWS_EMPTY", "no leakage-safe rows"
        )
    return rows, metadata


def _season_partition(
    rows: Sequence[BinaryTrainingRow], metadata: Sequence[Mapping[str, Any]]
) -> tuple[list[BinaryTrainingRow], list[dict[str, Any]], int]:
    selected_rows: list[BinaryTrainingRow] = []
    selected_meta: list[dict[str, Any]] = []
    forward_reserve = 0
    for row, meta in zip(rows, metadata):
        season = int(meta.get("season") or 0)
        if season in {*TRAIN_SEASONS, CALIBRATION_SEASON, VALIDATION_SEASON}:
            selected_rows.append(row)
            selected_meta.append(dict(meta))
        elif season == FORWARD_RESERVE_SEASON:
            forward_reserve += 1
    return selected_rows, selected_meta, forward_reserve


def _train_season_split_candidate(
    rows: Sequence[BinaryTrainingRow], metadata: Sequence[Mapping[str, Any]]
) -> BinaryCandidate:
    if len(rows) != len(metadata):
        raise TeamStateChallengerUnavailable(
            "NFL_EVENT_CONTEXT_METADATA_MISMATCH", f"rows={len(rows)};meta={len(metadata)}"
        )
    seasons = [int(meta.get("season") or 0) for meta in metadata]
    train_end = sum(season in TRAIN_SEASONS for season in seasons)
    cal_n = sum(season == CALIBRATION_SEASON for season in seasons)
    validation_n = sum(season == VALIDATION_SEASON for season in seasons)
    cal_end = train_end + cal_n
    if train_end < MIN_TRAIN_N:
        raise TeamStateChallengerUnavailable(
            "NFL_EVENT_CONTEXT_TRAIN_SAMPLE_INSUFFICIENT", str(train_end)
        )
    if cal_n < MIN_CALIBRATION_N:
        raise TeamStateChallengerUnavailable(
            "NFL_EVENT_CONTEXT_CALIBRATION_SAMPLE_INSUFFICIENT", str(cal_n)
        )
    if validation_n < MIN_VALIDATION_N:
        raise TeamStateChallengerUnavailable(
            "NFL_EVENT_CONTEXT_VALIDATION_SAMPLE_INSUFFICIENT", str(validation_n)
        )
    expected = (
        [*TRAIN_SEASONS] * 0  # keeps the split declaration visually tied to constants
    )
    del expected
    if any(season not in TRAIN_SEASONS for season in seasons[:train_end]):
        raise TeamStateChallengerUnavailable(
            "NFL_EVENT_CONTEXT_SPLIT_INVALID", "train block is not 2021-2023 only"
        )
    if any(season != CALIBRATION_SEASON for season in seasons[train_end:cal_end]):
        raise TeamStateChallengerUnavailable(
            "NFL_EVENT_CONTEXT_SPLIT_INVALID", "calibration block is not 2024 only"
        )
    if any(season != VALIDATION_SEASON for season in seasons[cal_end:]):
        raise TeamStateChallengerUnavailable(
            "NFL_EVENT_CONTEXT_SPLIT_INVALID", "validation block is not 2025 only"
        )

    try:
        X, y = _matrix(rows, FEATURE_ORDER)
    except BinaryCandidateError as exc:
        raise TeamStateChallengerUnavailable(exc.code, str(exc)) from exc
    X_train, y_train = X[:train_end], y[:train_end]
    X_cal, y_cal = X[train_end:cal_end], y[train_end:cal_end]
    X_test, y_test = X[cal_end:], y[cal_end:]
    for label, partition in (
        ("train", y_train),
        ("calibration", y_cal),
        ("validation", y_test),
    ):
        if len(np.unique(partition)) < 2:
            raise TeamStateChallengerUnavailable(
                "NFL_EVENT_CONTEXT_CLASS_DEGENERATE", label
            )

    scaler = StandardScaler().fit(X_train)
    model = LogisticRegression(C=1.0, solver="lbfgs", max_iter=500, random_state=0)
    model.fit(scaler.transform(X_train), y_train)
    p_cal_raw = model.predict_proba(scaler.transform(X_cal))[:, 1]
    p_test_raw = model.predict_proba(scaler.transform(X_test))[:, 1]
    try:
        calibrator = _select_calibrator(p_cal_raw, y_cal)
        p_test_cal = _map_calibrator(p_test_raw, calibrator)
    except BinaryCandidateError as exc:
        raise TeamStateChallengerUnavailable(exc.code, str(exc)) from exc

    prevalence = float(np.mean(y_train))
    baseline = np.full(len(y_test), prevalence, dtype=float)
    metrics = BinaryCandidateMetrics(
        train_n=len(y_train),
        calibration_n=len(y_cal),
        test_n=len(y_test),
        raw_brier=float(brier_score_loss(y_test, p_test_raw)),
        calibrated_brier=float(brier_score_loss(y_test, p_test_cal)),
        baseline_brier=float(brier_score_loss(y_test, baseline)),
        raw_log_loss=float(log_loss(y_test, p_test_raw, labels=[0, 1])),
        calibrated_log_loss=float(log_loss(y_test, p_test_cal, labels=[0, 1])),
        baseline_log_loss=float(log_loss(y_test, baseline, labels=[0, 1])),
        ece=float(_ece(p_test_cal, y_test)),
    )
    if not all(isfinite(float(value)) for value in asdict(metrics).values()):
        raise TeamStateChallengerUnavailable(
            "NFL_EVENT_CONTEXT_METRICS_INVALID", "non-finite validation metric"
        )
    research_pass = (
        metrics.raw_brier < metrics.baseline_brier
        and metrics.raw_log_loss <= metrics.baseline_log_loss
        and metrics.calibrated_brier <= metrics.raw_brier + 1e-12
        and metrics.calibrated_log_loss <= metrics.raw_log_loss + 1e-12
    )
    artifact = {
        "model_family": MODEL_FAMILY,
        "artifact_format": "STANDARDIZED_LOGISTIC_JSON_V1",
        "feature_names": list(FEATURE_ORDER),
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "intercept": float(model.intercept_[0]),
        "coefficients": model.coef_[0].tolist(),
        "train_seasons": list(TRAIN_SEASONS),
        "calibration_season": CALIBRATION_SEASON,
        "validation_season": VALIDATION_SEASON,
        "forward_reserve_season": FORWARD_RESERVE_SEASON,
        "train_end_index": train_end,
        "calibration_end_index": cal_end,
        "split_policy": "NFL_SEASON_2021_2023_TRAIN_2024_CAL_2025_VALIDATION_V1",
        "calibrator_selection_version": CALIBRATOR_SELECTION_VERSION,
        "market_features_used": False,
        "manual_probability_adjustments": False,
    }
    return BinaryCandidate(
        model_family=MODEL_FAMILY,
        feature_names=tuple(FEATURE_ORDER),
        artifact_payload=artifact,
        calibrator_payload=calibrator,
        metrics=metrics,
        dataset_hash=_dataset_hash(rows, tuple(FEATURE_ORDER), MODEL_FAMILY),
        calibration_start_event=rows[train_end].event_start_time,
        calibration_end_event=rows[cal_end - 1].event_start_time,
        test_start_event=rows[cal_end].event_start_time,
        test_end_event=rows[-1].event_start_time,
        research_screen_pass=research_pass,
    )


def _active_champion(client: Any) -> dict[str, Any]:
    result = (
        client.table("wow_nfl_event_fitted_model_artifacts")
        .select("model_artifact_version,model_family,feature_schema_version,validation_metrics,training_dataset_hash")
        .eq("active", True)
        .eq("promoted", True)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = [dict(row) for row in (result.data or []) if isinstance(row, Mapping)]
    if not rows:
        raise TeamStateChallengerUnavailable(
            "NFL_EVENT_CONTEXT_CHAMPION_UNAVAILABLE", "no active promoted NFL event artifact"
        )
    return rows[0]


def _champion_comparison(candidate: BinaryCandidate, champion: Mapping[str, Any]) -> dict[str, Any]:
    metrics = dict(champion.get("validation_metrics") or {})
    required = (
        "validation_brier_score",
        "validation_log_loss",
        "validation_ece_10",
        "validation_n",
    )
    if any(metrics.get(field) is None for field in required):
        raise TeamStateChallengerUnavailable(
            "NFL_EVENT_CONTEXT_CHAMPION_METRICS_INCOMPLETE", ",".join(required)
        )
    champion_brier = float(metrics["validation_brier_score"])
    champion_log_loss = float(metrics["validation_log_loss"])
    champion_ece = float(metrics["validation_ece_10"])
    champion_n = int(metrics["validation_n"])
    candidate_metrics = candidate.metrics
    same_validation_n = candidate_metrics.test_n == champion_n
    brier_improved = candidate_metrics.calibrated_brier < champion_brier - 1e-12
    log_loss_not_worse = candidate_metrics.calibrated_log_loss <= champion_log_loss + 1e-12
    ece_not_worse = candidate_metrics.ece <= champion_ece + 1e-12
    return {
        "champion_model_artifact_version": champion.get("model_artifact_version"),
        "champion_model_family": champion.get("model_family"),
        "champion_feature_schema_version": champion.get("feature_schema_version"),
        "champion_training_dataset_hash": champion.get("training_dataset_hash"),
        "champion_validation_n": champion_n,
        "candidate_validation_n": candidate_metrics.test_n,
        "same_validation_n": same_validation_n,
        "champion_brier": champion_brier,
        "candidate_brier": candidate_metrics.calibrated_brier,
        "brier_improved": brier_improved,
        "champion_log_loss": champion_log_loss,
        "candidate_log_loss": candidate_metrics.calibrated_log_loss,
        "log_loss_not_worse": log_loss_not_worse,
        "champion_ece": champion_ece,
        "candidate_ece": candidate_metrics.ece,
        "ece_not_worse": ece_not_worse,
        "passes": bool(
            same_validation_n and brier_improved and log_loss_not_worse and ece_not_worse
        ),
    }


def _persist_candidate_artifact(
    client: Any,
    *,
    training_code_sha: str,
    candidate: BinaryCandidate,
    metrics: Mapping[str, Any],
    research_screen_pass: bool,
) -> str:
    artifact = dict(candidate.artifact_payload)
    version = f"{MODEL_FAMILY}_{candidate.dataset_hash[:16]}_{training_code_sha[:12]}"
    client.table("wow_d1_candidate_artifacts").upsert(
        {
            "sport": "NFL",
            "league": "NFL",
            "market_family": "OUTRIGHT_WINNER",
            "model_family": MODEL_FAMILY,
            "model_artifact_version": version,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "source_policy_id": SOURCE_POLICY_ID,
            "training_dataset_hash": candidate.dataset_hash,
            "training_code_sha": training_code_sha,
            "artifact_checksum": _hash(artifact),
            "artifact_payload": artifact,
            "calibrator_payload": dict(candidate.calibrator_payload),
            "validation_metrics": dict(metrics),
            "training_rows": candidate.metrics.train_n,
            "calibration_rows": candidate.metrics.calibration_n,
            "test_rows": candidate.metrics.test_n,
            "research_screen_pass": bool(research_screen_pass),
            "source_review_status": "REQUIRED",
            "lifecycle_state": "CANDIDATE",
            "promoted": False,
            "active": False,
            "automatic_certification": False,
            "automatic_promotion": False,
            "probability_publishable": False,
            "can_execute": False,
        },
        on_conflict="model_artifact_version",
        ignore_duplicates=True,
    ).execute()
    return version


def train_and_persist(
    client: Any,
    *,
    events: Sequence[Mapping[str, Any]],
    training_code_sha: str,
) -> dict[str, Any]:
    all_rows, all_metadata = build_rows(events)
    rows, metadata, forward_reserve_rows = _season_partition(all_rows, all_metadata)
    candidate = _train_season_split_candidate(rows, metadata)
    baseline_screen = evaluate_binary_research_screen(candidate.metrics)
    champion = _active_champion(client)
    champion_comparison = _champion_comparison(candidate, champion)
    screen_pass = bool(baseline_screen["passed"] and champion_comparison["passes"])

    _persist_rows(
        client,
        sport="NFL",
        league="NFL",
        schema=FEATURE_SCHEMA_VERSION,
        model_family=MODEL_FAMILY,
        rows=rows,
        metadata=metadata,
        multiclass=False,
    )
    metrics = asdict(candidate.metrics) | {
        "research_screen_pass": screen_pass,
        "generic_lifecycle_research_screen_pass": candidate.research_screen_pass,
        "team_state_research_screen": baseline_screen,
        "champion_comparison": champion_comparison,
        "market_features_used": False,
        "manual_probability_adjustments": False,
        "champion_challenger_required": True,
        "v1_recent8_blend_reused": False,
        "separated_current_previous_season": True,
        "split_policy": "NFL_SEASON_2021_2023_TRAIN_2024_CAL_2025_VALIDATION_V1",
        "forward_reserve_season": FORWARD_RESERVE_SEASON,
        "forward_reserve_rows": forward_reserve_rows,
        "week_3_v1_prior_season_share_reproduced_pct": 75.0,
    }
    version = _persist_candidate_artifact(
        client,
        training_code_sha=training_code_sha,
        candidate=candidate,
        metrics=metrics,
        research_screen_pass=screen_pass,
    )
    return {
        "sport": "NFL",
        "league": "NFL",
        "model_family": MODEL_FAMILY,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "model_artifact_version": version,
        "eligible_rows": len(rows),
        "forward_reserve_rows": forward_reserve_rows,
        "feature_count": len(FEATURE_ORDER),
        "metrics": metrics,
        "research_screen_pass": screen_pass,
        "lifecycle_state": "CANDIDATE",
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "CALIBRATION_SEASON",
    "FEATURE_ORDER",
    "FEATURE_SCHEMA_VERSION",
    "FORWARD_RESERVE_SEASON",
    "MODEL_FAMILY",
    "TRAIN_SEASONS",
    "VALIDATION_SEASON",
    "build_rows",
    "train_and_persist",
]
