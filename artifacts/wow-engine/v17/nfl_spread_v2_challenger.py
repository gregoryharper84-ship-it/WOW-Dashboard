"""Research-only NFL spread V2 challenger.

V1's exact-line holdout is fully bound but calibrated worse than binary no-skill
references.  This challenger changes only the *sporting* margin model: it reuses
NFL_EVENT_CONTEXT_FEATURES_V2, which separates current-season from prior-season
state and includes strictly-prior QB continuity/rest/process context.  Market
spread is never a fitted feature and is applied only after the sporting margin
distribution is frozen.

Training discipline is fixed: 2021-2023 fit, 2024 residual calibration, untouched
2025 exact-line validation, 2026 forward reserve.  Nothing here certifies,
promotes, publishes, ranks, or executes a wager.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Mapping, Sequence

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from nfl_event_data_p1 import download_asset, schedules_asset
from v17.nfl_event_context_challenger import (
    CALIBRATION_SEASON,
    FEATURE_SCHEMA_VERSION as EVENT_FEATURE_SCHEMA_VERSION,
    FORWARD_RESERVE_SEASON,
    MIN_CALIBRATION_N,
    MIN_TRAIN_N,
    MIN_VALIDATION_N,
    TRAIN_SEASONS,
    VALIDATION_SEASON,
    build_rows as build_event_context_rows,
)
from v17.spread_certification_replay import _read_csv, _source_manifest
from v17.spread_historical_close_proxy import (
    NFLVERSE_EVIDENCE_CLASS,
    evaluate_historical_close_proxy,
    nflverse_close_proxy,
)
from v17.spread_margin_challenger import (
    MarginTrainingRow,
    SpreadChallengerUnavailable,
    predict_margin_center,
    score_home_spread,
)

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
RANK_ELIGIBLE = False
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
GLOBAL_TERMINAL_REDUCER = "V17_TERMINAL_REDUCER"
MODEL_FAMILY = "NFL_SPREAD_EVENT_CONTEXT_RIDGE_EMPIRICAL_V2"
FEATURE_SCHEMA_VERSION = "NFL_SPREAD_EVENT_CONTEXT_FEATURES_V2"
SOURCE_POLICY_ID = "NFL_SPREAD_SEASON_SEPARATED_PRIOR_ONLY_V2"
RIDGE_ALPHA = 4.0
NO_SKILL_BRIER = 0.25
NO_SKILL_LOG_LOSS = math.log(2.0)


@dataclass(frozen=True)
class NFLSpreadV2Artifact:
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
    train_seasons: tuple[int, ...]
    calibration_season: int
    validation_season: int
    forward_reserve_season: int
    forward_reserve_rows: int

    def payload(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "source_policy_id": SOURCE_POLICY_ID,
            "market_features_used": False,
            "spread_line_used_as_feature": False,
            "market_probability_substitution_used": False,
            "moneyline_probability_used": False,
            "automatic_certification": False,
            "automatic_promotion": False,
            "probability_publishable": False,
            "rank_eligible": False,
            "global_terminal_reducer": GLOBAL_TERMINAL_REDUCER,
            "can_execute": False,
        }


def _hash(payload: Any) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _matrix(rows: Sequence[MarginTrainingRow], names: Sequence[str]) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray([[float(row.features[name]) for name in names] for row in rows], dtype=float)
    y = np.asarray([float(row.margin) for row in rows], dtype=float)
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise SpreadChallengerUnavailable("NFL_SPREAD_V2_NONFINITE_INPUT", "non-finite sporting input")
    return x, y


def _dataset_hash(rows: Sequence[MarginTrainingRow], seasons: Sequence[int]) -> str:
    return _hash([
        {
            "event_id": row.event_id,
            "event_start_time": row.event_start_time,
            "feature_as_of": row.feature_as_of,
            "margin": row.margin,
            "features": dict(sorted((str(k), float(v)) for k, v in row.features.items())),
            "source_manifest_sha256": row.source_manifest_sha256,
            "season": int(season),
        }
        for row, season in zip(rows, seasons)
    ])


def build_margin_rows(events: Sequence[Mapping[str, Any]]) -> tuple[list[MarginTrainingRow], list[int]]:
    """Reuse the audited NFL event-context feature builder with a margin target."""
    binary_rows, metadata = build_event_context_rows(events)
    event_by_id = {str(event.get("event_id") or ""): event for event in events}
    rows: list[MarginTrainingRow] = []
    seasons: list[int] = []
    for binary, meta in zip(binary_rows, metadata):
        source = event_by_id.get(binary.event_id)
        if not source or source.get("home_score") is None or source.get("away_score") is None:
            raise SpreadChallengerUnavailable(
                "NFL_SPREAD_V2_SETTLEMENT_IDENTITY_MISSING",
                f"margin settlement missing for {binary.event_id}",
            )
        season = int(meta.get("season") or source.get("season") or 0)
        rows.append(MarginTrainingRow(
            event_id=binary.event_id,
            event_start_time=binary.event_start_time,
            feature_as_of=binary.feature_as_of,
            margin=int(round(float(source["home_score"]) - float(source["away_score"]))),
            features={name: float(value) for name, value in binary.features.items()},
            source_manifest_sha256=binary.source_manifest_sha256,
        ))
        seasons.append(season)
    if not rows:
        raise SpreadChallengerUnavailable("NFL_SPREAD_V2_ROWS_EMPTY", "no event-context margin rows")
    return rows, seasons


def _partition(
    rows: Sequence[MarginTrainingRow], seasons: Sequence[int]
) -> tuple[list[MarginTrainingRow], list[MarginTrainingRow], list[MarginTrainingRow], int]:
    train, calibration, validation = [], [], []
    forward_reserve = 0
    for row, season in zip(rows, seasons):
        if season in TRAIN_SEASONS:
            train.append(row)
        elif season == CALIBRATION_SEASON:
            calibration.append(row)
        elif season == VALIDATION_SEASON:
            validation.append(row)
        elif season == FORWARD_RESERVE_SEASON:
            forward_reserve += 1
    if len(train) < MIN_TRAIN_N:
        raise SpreadChallengerUnavailable("NFL_SPREAD_V2_TRAIN_ROWS_INSUFFICIENT", f"need {MIN_TRAIN_N}; got {len(train)}")
    if len(calibration) < MIN_CALIBRATION_N:
        raise SpreadChallengerUnavailable("NFL_SPREAD_V2_CALIBRATION_ROWS_INSUFFICIENT", f"need {MIN_CALIBRATION_N}; got {len(calibration)}")
    if len(validation) < MIN_VALIDATION_N:
        raise SpreadChallengerUnavailable("NFL_SPREAD_V2_VALIDATION_ROWS_INSUFFICIENT", f"need {MIN_VALIDATION_N}; got {len(validation)}")
    return train, calibration, validation, forward_reserve


def train_candidate(events: Sequence[Mapping[str, Any]], *, ridge_alpha: float = RIDGE_ALPHA) -> tuple[NFLSpreadV2Artifact, list[MarginTrainingRow]]:
    rows, seasons = build_margin_rows(events)
    train, calibration, validation, forward_reserve = _partition(rows, seasons)
    names = tuple(sorted(train[0].features))
    if tuple(sorted(names)) != tuple(sorted(binary_name for binary_name in names)):
        raise SpreadChallengerUnavailable("NFL_SPREAD_V2_FEATURE_SCHEMA_INVALID", "feature schema invalid")
    for row in rows:
        if tuple(sorted(row.features)) != names:
            raise SpreadChallengerUnavailable("NFL_SPREAD_V2_FEATURE_SCHEMA_MISMATCH", "feature keys differ")
        if row.feature_as_of >= row.event_start_time:
            raise SpreadChallengerUnavailable("NFL_SPREAD_V2_FEATURE_LEAKAGE", row.event_id)

    x_train, y_train = _matrix(train, names)
    x_cal, y_cal = _matrix(calibration, names)
    scaler = StandardScaler().fit(x_train)
    model = Ridge(alpha=float(ridge_alpha)).fit(scaler.transform(x_train), y_train)
    cal_pred = model.predict(scaler.transform(x_cal))
    residuals = tuple(float(actual - pred) for actual, pred in zip(y_cal, cal_pred))
    if len(residuals) < 2 or float(np.std(np.asarray(residuals, dtype=float))) <= 1e-9:
        raise SpreadChallengerUnavailable("NFL_SPREAD_V2_RESIDUALS_DEGENERATE", "2024 residual distribution degenerate")

    selected_rows = [row for row, season in zip(rows, seasons) if season in {*TRAIN_SEASONS, CALIBRATION_SEASON, VALIDATION_SEASON}]
    selected_seasons = [season for season in seasons if season in {*TRAIN_SEASONS, CALIBRATION_SEASON, VALIDATION_SEASON}]
    artifact = NFLSpreadV2Artifact(
        sport="NFL",
        model_family=MODEL_FAMILY,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        feature_names=names,
        scaler_mean=tuple(float(v) for v in scaler.mean_),
        scaler_scale=tuple(float(v if abs(v) > 1e-12 else 1.0) for v in scaler.scale_),
        coefficients=tuple(float(v) for v in model.coef_),
        intercept=float(model.intercept_),
        calibration_residuals=residuals,
        train_rows=len(train),
        calibration_rows=len(calibration),
        test_rows=len(validation),
        training_dataset_hash=_dataset_hash(selected_rows, selected_seasons),
        ridge_alpha=float(ridge_alpha),
        train_seasons=tuple(int(v) for v in TRAIN_SEASONS),
        calibration_season=int(CALIBRATION_SEASON),
        validation_season=int(VALIDATION_SEASON),
        forward_reserve_season=int(FORWARD_RESERVE_SEASON),
        forward_reserve_rows=int(forward_reserve),
    )
    return artifact, validation


def _bind_validation_proxies(validation: Sequence[MarginTrainingRow], source_rows: Sequence[Mapping[str, Any]]):
    by_id: dict[str, list[Mapping[str, Any]]] = {}
    for raw in source_rows:
        gid = str(raw.get("game_id") or "").strip()
        if gid:
            by_id.setdefault(gid, []).append(raw)
    evidence = {}
    blockers: dict[str, str] = {}
    for row in validation:
        raw_id = row.event_id.removeprefix("NFL:")
        matches = by_id.get(raw_id) or []
        if len(matches) != 1:
            blockers[row.event_id] = "NFL_SPREAD_V2_EXACT_LINE_MISSING" if not matches else "NFL_SPREAD_V2_EXACT_LINE_AMBIGUOUS"
            continue
        try:
            proxy = nflverse_close_proxy(matches[0])
            evidence[row.event_id] = replace(proxy, event_id=row.event_id)
        except SpreadChallengerUnavailable as exc:
            blockers[row.event_id] = exc.code
    return evidence, blockers


def run_experiment(
    *,
    events: Sequence[Mapping[str, Any]],
    ridge_alpha: float = RIDGE_ALPHA,
    download_fn: Any = download_asset,
) -> dict[str, Any]:
    artifact, validation = train_candidate(events, ridge_alpha=ridge_alpha)
    with TemporaryDirectory(prefix="wow-nfl-spread-v2-") as directory:
        captured = download_fn(schedules_asset(), directory)
        source_rows = _read_csv(captured.local_path)
        evidence, blockers = _bind_validation_proxies(validation, source_rows)
        metrics = evaluate_historical_close_proxy(
            artifact=artifact,  # duck-compatible with governed spread scorer
            test_rows=validation,
            evidence_by_event=evidence,
            evidence_class=NFLVERSE_EVIDENCE_CLASS,
        )
        source_manifest = _source_manifest(captured)

    exact_n = int(metrics.get("evidence_row_n") or 0)
    coverage = float(metrics.get("exact_line_coverage") or 0.0)
    brier = metrics.get("cover_brier")
    logloss = metrics.get("cover_log_loss")
    beats_no_skill = bool(
        exact_n == len(validation)
        and coverage == 1.0
        and isinstance(brier, (int, float))
        and isinstance(logloss, (int, float))
        and float(brier) < NO_SKILL_BRIER
        and float(logloss) < NO_SKILL_LOG_LOSS
    )
    return {
        "status": "EXPERIMENT_CREATED",
        "code": "NFL_SPREAD_V2_EXACT_LINE_EXPERIMENT_COMPLETE",
        "sport": "NFL",
        "model_family": MODEL_FAMILY,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "event_feature_schema_version": EVENT_FEATURE_SCHEMA_VERSION,
        "source_policy_id": SOURCE_POLICY_ID,
        "artifact": artifact.payload(),
        "artifact_checksum": _hash(artifact.payload()),
        "exact_line_metrics": metrics,
        "binding_audit": {
            "validation_event_n": len(validation),
            "bound_event_n": len(evidence),
            "coverage": coverage,
            "blocker_counts": {code: list(blockers.values()).count(code) for code in sorted(set(blockers.values()))},
        },
        "source_manifest": source_manifest,
        "binary_no_skill_reference": {"cover_brier": NO_SKILL_BRIER, "cover_log_loss": NO_SKILL_LOG_LOSS},
        "beats_binary_no_skill_reference": beats_no_skill,
        "market_features_used": False,
        "spread_line_used_as_feature": False,
        "market_probability_substitution_used": False,
        "moneyline_probability_used": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "global_terminal_reducer": GLOBAL_TERMINAL_REDUCER,
        "can_execute": False,
    }


__all__ = [
    "AUTOMATIC_CERTIFICATION",
    "AUTOMATIC_PROMOTION",
    "CAN_EXECUTE",
    "FEATURE_SCHEMA_VERSION",
    "GLOBAL_TERMINAL_REDUCER",
    "MODEL_FAMILY",
    "NFLSpreadV2Artifact",
    "PROBABILITY_PUBLISHABLE",
    "build_margin_rows",
    "run_experiment",
    "train_candidate",
]
