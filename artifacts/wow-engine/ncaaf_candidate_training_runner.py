"""Load complete governed NCAAF training rows and persist research-only candidates.

This runner may create only CANDIDATE/non-active/nonpublishable model and calibrator
artifacts. Promotion, calibration-health PASS, prospective certification, probability
publication, and execution are intentionally outside this boundary.
"""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
from typing import Any, Mapping

from ncaaf_feature_transform import model_features_from_snapshot
from ncaaf_three_way_trainer import train_calibrate_test, candidate_clears_research_screen
from ncaaf_trainer import TrainingRow, NCAAFTrainingError

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
PROVIDER_IDENTITY = "WOW_NCAAF_FITTED_MODEL_V1"
MODEL_FAMILY = "NCAAF_LOGISTIC_V1"
ARTIFACT_FORMAT = "STANDARDIZED_LOGISTIC_JSON_V1"
FEATURE_SCHEMA_VERSION = "NCAAF_FEATURES_V1"
FEATURE_TRANSFORM_VERSION = "NCAAF_FEATURE_TRANSFORM_V1"
SPECIALIST_VERSION = "wow-llp-ncaaf-trust-layer"


class NCAAFTrainingRunnerUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _paged(client: Any, table: str, columns: str, *, page_size: int = 1000) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    start = 0
    while True:
        try:
            result = client.table(table).select(columns).range(start, start + page_size - 1).execute()
        except Exception as exc:
            raise NCAAFTrainingRunnerUnavailable("NCAAF_TRAINING_DATA_READ_FAILED", table) from exc
        rows = getattr(result, "data", None)
        if not isinstance(rows, list):
            raise NCAAFTrainingRunnerUnavailable("NCAAF_TRAINING_DATA_INVALID", table)
        out.extend(dict(row) for row in rows if isinstance(row, Mapping))
        if len(rows) < page_size:
            return out
        start += page_size


def load_training_rows(client: Any) -> tuple[list[TrainingRow], list[dict[str, Any]]]:
    games = _paged(
        client,
        "wow_ncaaf_training_games",
        "training_game_id,official_event_id,season,event_start_time,neutral_site,home_team,away_team,home_won",
    )
    features = _paged(client, "wow_ncaaf_training_features", "*")
    games_by_id = {str(g.get("training_game_id")): g for g in games if g.get("training_game_id")}
    if not games_by_id or not features:
        raise NCAAFTrainingRunnerUnavailable("NCAAF_TRAINING_DATA_EMPTY", "training games/features are empty")

    rows: list[TrainingRow] = []
    metadata: list[dict[str, Any]] = []
    for feature in features:
        if str(feature.get("feature_schema_version") or "") != FEATURE_SCHEMA_VERSION:
            continue
        game = games_by_id.get(str(feature.get("training_game_id") or ""))
        if not game or game.get("home_won") is None:
            continue
        merged = {**feature, **{
            "official_event_id": game.get("official_event_id"),
            "event_start_time": game.get("event_start_time"),
            "home_team": game.get("home_team"),
            "away_team": game.get("away_team"),
            "neutral_site": game.get("neutral_site"),
        }}
        try:
            transformed = model_features_from_snapshot(merged)
        except Exception:
            continue
        event_start = str(game.get("event_start_time") or "")
        feature_as_of = str(feature.get("feature_as_of") or "")
        if not event_start or not feature_as_of:
            continue
        rows.append(TrainingRow(
            event_start_time=event_start,
            feature_as_of=feature_as_of,
            home_won=bool(game.get("home_won")),
            features=transformed,
        ))
        metadata.append({
            "official_event_id": str(game.get("official_event_id")),
            "season": int(game.get("season")),
            "event_start_time": event_start,
        })
    order = sorted(range(len(rows)), key=lambda i: rows[i].event_start_time)
    rows = [rows[i] for i in order]
    metadata = [metadata[i] for i in order]
    if len(rows) < 300:
        raise NCAAFTrainingRunnerUnavailable("NCAAF_COMPLETE_TRAINING_ROWS_INSUFFICIENT", f"complete_rows={len(rows)}; minimum=300")
    return rows, metadata


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def train_and_persist_candidate(client: Any, *, training_code_sha: str) -> dict[str, Any]:
    if not isinstance(training_code_sha, str) or len(training_code_sha.strip()) < 7:
        raise NCAAFTrainingRunnerUnavailable("NCAAF_TRAINING_CODE_SHA_REQUIRED", "auditable training code SHA is required")
    rows, metadata = load_training_rows(client)
    try:
        candidate = train_calibrate_test(rows)
    except NCAAFTrainingError as exc:
        raise NCAAFTrainingRunnerUnavailable(exc.code, str(exc)) from exc

    artifact_payload = dict(candidate.artifact_payload)
    artifact_checksum = _canonical_hash(artifact_payload)
    model_version = f"NCAAF_LOGISTIC_V1-{candidate.dataset_hash[:16]}-{training_code_sha[:12]}"
    calibrator_version = f"NCAAF_CAL-{candidate.dataset_hash[:16]}-{training_code_sha[:12]}"
    seasons = sorted({m["season"] for m in metadata})
    validation_start = datetime.fromisoformat(candidate.test_start_event.replace("Z", "+00:00")).date().isoformat()
    validation_end = datetime.fromisoformat(candidate.test_end_event.replace("Z", "+00:00")).date().isoformat()
    calibration_start = datetime.fromisoformat(candidate.calibration_start_event.replace("Z", "+00:00")).date().isoformat()
    calibration_end = datetime.fromisoformat(candidate.calibration_end_event.replace("Z", "+00:00")).date().isoformat()
    metrics = {
        "train_n": candidate.metrics.train_n,
        "calibration_n": candidate.metrics.calibration_n,
        "test_n": candidate.metrics.test_n,
        "raw_test_brier": candidate.metrics.raw_test_brier,
        "calibrated_test_brier": candidate.metrics.calibrated_test_brier,
        "baseline_test_brier": candidate.metrics.baseline_test_brier,
        "raw_test_log_loss": candidate.metrics.raw_test_log_loss,
        "calibrated_test_log_loss": candidate.metrics.calibrated_test_log_loss,
        "baseline_test_log_loss": candidate.metrics.baseline_test_log_loss,
        "research_screen_pass": candidate_clears_research_screen(candidate),
        "split_policy": "CHRONOLOGICAL_60_20_20",
        "untouched_test": True,
    }
    model_row = {
        "provider_identity": PROVIDER_IDENTITY,
        "model_family": MODEL_FAMILY,
        "model_artifact_version": model_version,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_transform_version": FEATURE_TRANSFORM_VERSION,
        "specialist_version": SPECIALIST_VERSION,
        "certification_id": "UNSET_CANDIDATE",
        "lifecycle_state": "CANDIDATE",
        "training_dataset_hash": candidate.dataset_hash,
        "training_code_sha": training_code_sha,
        "artifact_checksum": artifact_checksum,
        "artifact_format": ARTIFACT_FORMAT,
        "artifact_payload": artifact_payload,
        "training_rows": len(rows),
        "training_seasons": seasons,
        "validation_start_date": validation_start,
        "validation_end_date": validation_end,
        "validation_metrics": metrics,
        "calibration_method": "EMPIRICAL_WILSON_BINS_V1",
        "calibrator_version": calibrator_version,
        "calibration_training_n": candidate.metrics.calibration_n,
        "promoted": False,
        "active": False,
        "probability_publishable": False,
        "can_execute": False,
    }
    calibrator_row = {
        "calibrator_version": calibrator_version,
        "model_artifact_version": model_version,
        "calibration_method": "EMPIRICAL_WILSON_BINS_V1",
        "training_n": candidate.metrics.calibration_n,
        "calibration_start_date": calibration_start,
        "calibration_end_date": calibration_end,
        "payload": dict(candidate.calibrator_payload),
        "metrics": metrics,
        "calibration_health_status": "BLOCKED",
        "active": False,
        "probability_publishable": False,
        "can_execute": False,
    }
    try:
        client.table("wow_ncaaf_fitted_model_artifacts").upsert(
            model_row, on_conflict="provider_identity,model_artifact_version"
        ).execute()
        client.table("wow_ncaaf_calibrator_artifacts").upsert(
            calibrator_row, on_conflict="calibrator_version"
        ).execute()
    except Exception as exc:
        raise NCAAFTrainingRunnerUnavailable("NCAAF_CANDIDATE_ARTIFACT_PERSIST_FAILED", type(exc).__name__) from exc
    return {
        "ok": True,
        "code": "NCAAF_CANDIDATE_ARTIFACTS_PERSISTED",
        "model_artifact_version": model_version,
        "calibrator_version": calibrator_version,
        "training_rows": len(rows),
        "metrics": metrics,
        "lifecycle_state": "CANDIDATE",
        "calibration_health_status": "BLOCKED",
        "probability_publishable": False,
        "can_execute": False,
    }
