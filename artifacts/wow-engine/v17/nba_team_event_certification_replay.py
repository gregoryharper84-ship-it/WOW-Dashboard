"""Read-only NBA team/event artifact, calibrator and pregame-corpus replay.

This lane replays the persisted fitted artifact against its untouched chronological
holdout. It separately audits the persisted calibrator and source/feature corpus.
A successful numerical replay is evidence for independent review only: it never
certifies, promotes, activates, registers, publishes, ranks or executes a model.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, time, timezone
from types import SimpleNamespace
from typing import Any, Mapping

from basketball_specialist_pipeline import _walk_forward_raw, evaluate_untouched_calibrated_holdout
from calibration import phase_b_platt
from basketball_team_event_specialist import (
    FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
    MODEL_FAMILY,
    FeatureRow,
    LogisticArtifact,
    MIN_PRIOR_GAMES,
    TrainingGame,
    build_pregame_features,
)

CAN_EXECUTE = False
SPORT = "NBA"
PAGE_SIZE = 1000
MAX_CORPUS_ROWS = 50000

ARTIFACT_TABLE = "wow_basketball_team_event_model_artifacts"
CALIBRATOR_TABLE = "wow_basketball_team_event_calibrators"
CERTIFICATION_TABLE = "wow_basketball_team_event_certification"
IMMUTABLE_CERTIFICATION_TABLE = "wow_team_event_specialist_certifications"
TRAINING_TABLE = "wow_nba_training_games"
FEATURE_TABLE = "wow_nba_pregame_feature_rows"

ARTIFACT_FIELDS = (
    "artifact_id,sport,model_family,model_artifact_version,feature_schema_version,"
    "artifact_format,artifact_payload,artifact_checksum,training_row_count,train_start_date,"
    "train_end_date,holdout_row_count,holdout_brier,holdout_log_loss,holdout_accuracy,"
    "promoted,active,certified_at,created_at"
)
CALIBRATOR_FIELDS = (
    "calibrator_id,sport,calibrator_version,model_artifact_version,calibration_method,"
    "coefficients,calibration_row_count,brier,log_loss,ece,calibration_bias,promoted,active,created_at"
)
CERTIFICATION_FIELDS = (
    "sport,capability_status,reason_code,model_artifact_version,calibrator_version,"
    "corpus_rows,settled_rows,certification_notes,updated_at"
)
IMMUTABLE_CERTIFICATION_FIELDS = (
    "certification_id,model_version,artifact_sha256,calibration_sha256,"
    "independent_verification_status,certification_status,probability_publishable,can_execute"
)
TRAINING_FIELDS = (
    "game_id,season,game_date,status,home_team_id,away_team_id,home_score,away_score,home_win,"
    "source_provider,source_endpoint,source_retrieved_at,source_payload_sha256,settled"
)
FEATURE_FIELDS = (
    "game_id,as_of,feature_schema_version,home_games_prior,away_games_prior,"
    "feature_payload,feature_payload_sha256"
)


def _is_sha256(value: Any) -> bool:
    token = str(value or "").strip().lower()
    return len(token) == 64 and all(char in "0123456789abcdef" for char in token)


def _aware_timestamp(value: Any) -> datetime | None:
    token = str(value or "").strip()
    if not token:
        return None
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _sha256_json(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_one(
    db: Any,
    table: str,
    fields: str,
    *,
    filters: Mapping[str, Any],
    order_by: str | None = "created_at",
) -> dict[str, Any] | None:
    query = db.table(table).select(fields)
    for field, value in filters.items():
        query = query.eq(field, value)
    if order_by:
        query = query.order(order_by, desc=True)
    result = query.limit(1).execute()
    rows = getattr(result, "data", None)
    if not isinstance(rows, list):
        raise ValueError("NBA_REPLAY_REGISTRY_RESPONSE_INVALID:" + table)
    if not rows:
        return None
    if not isinstance(rows[0], Mapping):
        raise ValueError("NBA_REPLAY_REGISTRY_ROW_INVALID:" + table)
    return dict(rows[0])


def _read_all(
    db: Any,
    table: str,
    fields: str,
    *,
    settled_only: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        query = db.table(table).select(fields)
        if settled_only:
            query = query.eq("settled", True)
        result = (
            query.order("game_id")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )
        batch = getattr(result, "data", None)
        if not isinstance(batch, list):
            raise ValueError("NBA_REPLAY_CORPUS_RESPONSE_INVALID:" + table)
        if any(not isinstance(row, Mapping) for row in batch):
            raise ValueError("NBA_REPLAY_CORPUS_ROW_INVALID:" + table)
        rows.extend(dict(row) for row in batch)
        if len(rows) > MAX_CORPUS_ROWS:
            raise ValueError("NBA_REPLAY_CORPUS_BOUND_EXCEEDED:" + table)
        if len(batch) < PAGE_SIZE:
            return rows
        offset += PAGE_SIZE


def _artifact_from_row(row: Mapping[str, Any]) -> tuple[LogisticArtifact | None, list[str], bool, bool]:
    blockers: list[str] = []
    payload = row.get("artifact_payload")
    if not isinstance(payload, Mapping):
        return None, ["CANDIDATE_ARTIFACT_IDENTITY_INCOMPLETE"], False, False

    checksum_fields = (
        "sport", "model_family", "model_artifact_version", "feature_schema_version",
        "feature_names", "intercept", "coefficients", "means", "scales",
    )
    if any(field not in payload for field in checksum_fields):
        return None, ["CANDIDATE_ARTIFACT_IDENTITY_INCOMPLETE"], False, False

    try:
        calculated_checksum = _sha256_json({field: payload[field] for field in checksum_fields})
        row_checksum = str(row.get("artifact_checksum") or "").strip().lower()
        payload_checksum = str(payload.get("artifact_checksum") or "").strip().lower()
        if (
            calculated_checksum != row_checksum
            or calculated_checksum != payload_checksum
            or str(row.get("sport") or "").upper() != SPORT
            or str(payload.get("sport") or "").upper() != SPORT
            or str(row.get("model_family") or "") != MODEL_FAMILY
            or str(payload.get("model_family") or "") != MODEL_FAMILY
            or str(row.get("model_artifact_version") or "") != str(payload.get("model_artifact_version") or "")
            or str(row.get("feature_schema_version") or "") != FEATURE_SCHEMA_VERSION
            or str(payload.get("feature_schema_version") or "") != FEATURE_SCHEMA_VERSION
            or tuple(payload.get("feature_names") or ()) != tuple(FEATURE_NAMES)
            or not str(row.get("artifact_id") or "").strip()
            or str(row.get("artifact_format") or "") != "WOW_BASKETBALL_LOGISTIC_JSON_V1"
        ):
            blockers.append("CANDIDATE_ARTIFACT_IDENTITY_INCOMPLETE")

        artifact = LogisticArtifact(
            sport=str(payload["sport"]),
            model_family=str(payload["model_family"]),
            model_artifact_version=str(payload["model_artifact_version"]),
            feature_schema_version=str(payload["feature_schema_version"]),
            feature_names=tuple(payload["feature_names"]),
            intercept=float(payload["intercept"]),
            coefficients=tuple(float(value) for value in payload["coefficients"]),
            means=tuple(float(value) for value in payload["means"]),
            scales=tuple(float(value) for value in payload["scales"]),
            training_row_count=int(payload["training_row_count"]),
            holdout_row_count=int(payload["holdout_row_count"]),
            train_start_date=str(payload["train_start_date"]),
            train_end_date=str(payload["train_end_date"]),
            holdout_brier=float(payload["holdout_brier"]),
            holdout_log_loss=float(payload["holdout_log_loss"]),
            holdout_accuracy=float(payload["holdout_accuracy"]),
            artifact_checksum=calculated_checksum,
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return None, ["CANDIDATE_ARTIFACT_IDENTITY_INCOMPLETE"], False, False

    dimensions_valid = (
        type(payload.get("training_row_count")) is int
        and type(payload.get("holdout_row_count")) is int
        and type(row.get("training_row_count")) is int
        and type(row.get("holdout_row_count")) is int
        and row.get("training_row_count") == artifact.training_row_count
        and row.get("holdout_row_count") == artifact.holdout_row_count
        and str(row.get("train_start_date") or "") == artifact.train_start_date
        and str(row.get("train_end_date") or "") == artifact.train_end_date
        and _matches(row.get("holdout_brier"), artifact.holdout_brier)
        and _matches(row.get("holdout_log_loss"), artifact.holdout_log_loss)
        and _matches(row.get("holdout_accuracy"), artifact.holdout_accuracy)
        and len(artifact.coefficients) == len(FEATURE_NAMES)
        and len(artifact.means) == len(FEATURE_NAMES)
        and len(artifact.scales) == len(FEATURE_NAMES)
        and all(math.isfinite(value) for value in (
            artifact.intercept, *artifact.coefficients, *artifact.means, *artifact.scales
        ))
        and all(value > 0 for value in artifact.scales)
        and artifact.training_row_count > 0
        and artifact.holdout_row_count > 0
    )
    if not dimensions_valid:
        blockers.append("CANDIDATE_ARTIFACT_IDENTITY_INCOMPLETE")

    integrity_valid = not blockers
    # The current basketball artifact table/payload has no corpus hash or
    # training-code SHA. The artifact checksum above proves model payload
    # integrity, not which exact source corpus and training code produced it.
    provenance_complete = bool(
        _is_sha256(row.get("training_dataset_hash") or payload.get("training_dataset_hash"))
        and str(row.get("training_code_sha") or payload.get("training_code_sha") or "").strip()
    )
    if not provenance_complete:
        blockers.append("CANDIDATE_ARTIFACT_IDENTITY_INCOMPLETE")

    artifact_ready = integrity_valid and provenance_complete
    return artifact, list(dict.fromkeys(blockers)), integrity_valid, artifact_ready

def _training_game(row: Mapping[str, Any]) -> TrainingGame:
    game_id = str(row.get("game_id") or "").strip()
    game_date = date.fromisoformat(str(row.get("game_date") or "")[:10])
    season = row.get("season")
    home_score = row.get("home_score")
    away_score = row.get("away_score")
    if (
        not game_id
        or type(season) is not int
        or isinstance(home_score, bool) or not isinstance(home_score, int)
        or isinstance(away_score, bool) or not isinstance(away_score, int)
        or home_score == away_score
        or row.get("settled") is not True
        or row.get("home_win") is not (home_score > away_score)
        or not str(row.get("home_team_id") or "").strip()
        or not str(row.get("away_team_id") or "").strip()
        or row.get("home_team_id") == row.get("away_team_id")
    ):
        raise ValueError("NBA_REPLAY_SETTLED_GAME_IDENTITY_INVALID")
    return TrainingGame(
        game_id=game_id,
        sport=SPORT,
        season=season,
        game_date=game_date,
        home_team_id=str(row["home_team_id"]),
        away_team_id=str(row["away_team_id"]),
        home_score=home_score,
        away_score=away_score,
    )


def _corpus_replay(
    games_raw: list[dict[str, Any]],
    features_raw: list[dict[str, Any]],
    artifact: LogisticArtifact,
) -> tuple[list[FeatureRow], dict[str, Any], bool]:
    blockers: list[str] = []
    games_by_id: dict[str, dict[str, Any]] = {}
    training_games: list[TrainingGame] = []
    providers: set[str] = set()
    endpoints: set[str] = set()
    source_provenance_fields_valid = True

    for raw in games_raw:
        game_id = str(raw.get("game_id") or "").strip()
        if not game_id or game_id in games_by_id:
            blockers.append("CANDIDATE_PARTITIONS_INVALID")
            continue
        games_by_id[game_id] = raw
        try:
            training_games.append(_training_game(raw))
        except (TypeError, ValueError, OverflowError):
            source_provenance_fields_valid = False
            blockers.append("SOURCE_PROVENANCE_NOT_CERTIFIED")
            continue

        provider = str(raw.get("source_provider") or "").strip()
        endpoint = str(raw.get("source_endpoint") or "").strip()
        retrieved_at = _aware_timestamp(raw.get("source_retrieved_at"))
        if provider:
            providers.add(provider)
        if endpoint:
            endpoints.add(endpoint)
        if not provider or not endpoint or retrieved_at is None or not _is_sha256(raw.get("source_payload_sha256")):
            source_provenance_fields_valid = False
            blockers.append("SOURCE_PROVENANCE_NOT_CERTIFIED")

    generated = build_pregame_features(training_games, SPORT)
    generated_by_id = {row.game_id: row for row in generated}
    persisted_by_id: dict[str, dict[str, Any]] = {}
    date_bound_rows = 0
    feature_hashes_valid = True

    for raw in features_raw:
        game_id = str(raw.get("game_id") or "").strip()
        if not game_id or game_id in persisted_by_id:
            blockers.append("CANDIDATE_PARTITIONS_INVALID")
            feature_hashes_valid = False
            continue
        persisted_by_id[game_id] = raw
        game = games_by_id.get(game_id)
        payload = raw.get("feature_payload")
        if game is None or not isinstance(payload, Mapping):
            blockers.append("SOURCE_PROVENANCE_NOT_CERTIFIED")
            feature_hashes_valid = False
            continue

        try:
            game_date = date.fromisoformat(str(game.get("game_date") or "")[:10])
            as_of = _aware_timestamp(raw.get("as_of"))
            feature_date = date.fromisoformat(str(payload.get("game_date") or "")[:10])
        except (TypeError, ValueError, OverflowError):
            as_of = None
            game_date = None
            feature_date = None

        values = payload.get("features")
        valid_payload = (
            str(payload.get("sport") or "").upper() == SPORT
            and str(payload.get("game_id") or "") == game_id
            and feature_date is not None
            and feature_date == game_date
            and str(payload.get("feature_schema_version") or "") == FEATURE_SCHEMA_VERSION
            and str(raw.get("feature_schema_version") or "") == FEATURE_SCHEMA_VERSION
            and payload.get("home_games_prior") == raw.get("home_games_prior")
            and payload.get("away_games_prior") == raw.get("away_games_prior")
            and isinstance(values, Mapping)
            and set(values) == set(FEATURE_NAMES)
            and type(raw.get("home_games_prior")) is int
            and type(raw.get("away_games_prior")) is int
            and raw.get("home_games_prior", 0) >= MIN_PRIOR_GAMES
            and raw.get("away_games_prior", 0) >= MIN_PRIOR_GAMES
            and as_of is not None
            and as_of.date() == game_date
        )
        if not valid_payload:
            blockers.append("SOURCE_PROVENANCE_NOT_CERTIFIED")
            feature_hashes_valid = False
            continue

        numeric_values: list[float] = []
        for name in FEATURE_NAMES:
            value = values.get(name)
            if isinstance(value, bool):
                numeric_values = []
                break
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                numeric_values = []
                break
            if not math.isfinite(number):
                numeric_values = []
                break
            numeric_values.append(number)
        if len(numeric_values) != len(FEATURE_NAMES):
            blockers.append("SOURCE_PROVENANCE_NOT_CERTIFIED")
            feature_hashes_valid = False
            continue

        try:
            computed_feature_hash = _sha256_json(payload)
        except (TypeError, ValueError):
            computed_feature_hash = ""
        if not _is_sha256(raw.get("feature_payload_sha256")) or computed_feature_hash != str(raw.get("feature_payload_sha256") or "").lower():
            blockers.append("SOURCE_PROVENANCE_NOT_CERTIFIED")
            feature_hashes_valid = False

        date_bound_rows += 1

    generated_ids = set(generated_by_id)
    persisted_ids = set(persisted_by_id)
    if generated_ids != persisted_ids:
        blockers.append("SOURCE_PROVENANCE_NOT_CERTIFIED")
        feature_hashes_valid = False

    replay_rows: list[FeatureRow] = []
    for game_id, expected in generated_by_id.items():
        raw = persisted_by_id.get(game_id)
        if raw is None:
            continue
        payload = raw.get("feature_payload")
        values = payload.get("features") if isinstance(payload, Mapping) else None
        if not isinstance(values, Mapping):
            continue
        try:
            actual_values = tuple(float(values[name]) for name in FEATURE_NAMES)
            game = games_by_id[game_id]
            actual_date = date.fromisoformat(str(game["game_date"])[:10])
            matches = (
                actual_date == expected.game_date
                and raw.get("home_games_prior") == expected.home_games_prior
                and raw.get("away_games_prior") == expected.away_games_prior
                and len(actual_values) == len(expected.values)
                and all(math.isclose(a, b, rel_tol=0.0, abs_tol=1e-12) for a, b in zip(actual_values, expected.values))
                and int(bool(game.get("home_win"))) == expected.outcome
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            matches = False
            actual_values = ()
        if not matches:
            blockers.append("SOURCE_PROVENANCE_NOT_CERTIFIED")
            feature_hashes_valid = False
            continue
        replay_rows.append(expected)

    digest = hashlib.sha256()
    for game_id in sorted(games_by_id):
        raw = games_by_id[game_id]
        digest.update((
            "G|" + "|".join((
                game_id,
                str(raw.get("season") or ""),
                str(raw.get("game_date") or ""),
                str(raw.get("home_team_id") or ""),
                str(raw.get("away_team_id") or ""),
                str(raw.get("home_score") or ""),
                str(raw.get("away_score") or ""),
                str(raw.get("source_provider") or ""),
                str(raw.get("source_endpoint") or ""),
                str(raw.get("source_retrieved_at") or ""),
                str(raw.get("source_payload_sha256") or ""),
                str(raw.get("home_win")),
            )) + "\n"
        ).encode("utf-8"))
    for game_id in sorted(persisted_by_id):
        raw = persisted_by_id[game_id]
        digest.update((
            "F|" + "|".join((
                game_id,
                str(raw.get("as_of") or ""),
                str(raw.get("feature_schema_version") or ""),
                str(raw.get("home_games_prior") or ""),
                str(raw.get("away_games_prior") or ""),
                str(raw.get("feature_payload_sha256") or ""),
            )) + "\n"
        ).encode("utf-8"))

    # The corpus is reproducible and date-bounded, but the persisted training
    # schema has no event-start timestamp or official settlement receipt. A
    # midnight UTC date boundary cannot prove observed_at < actual tipoff.
    event_start_attested = False
    official_settlement_attested = False
    corpus_integrity_valid = (
        bool(games_raw)
        and bool(features_raw)
        and len(games_by_id) == len(games_raw)
        and len(persisted_by_id) == len(features_raw)
        and generated_ids == persisted_ids
        and len(replay_rows) == len(generated)
        and source_provenance_fields_valid
        and feature_hashes_valid
    )
    summary = {
        "settled_game_rows": len(games_raw),
        "distinct_settled_game_ids": len(games_by_id),
        "persisted_feature_rows": len(features_raw),
        "regenerated_feature_rows": len(generated),
        "date_bound_feature_rows": date_bound_rows,
        "settled_games_without_features": max(0, len(games_raw) - len(features_raw)),
        "feature_rows_without_settled_games": len(persisted_ids - set(games_by_id)),
        "source_providers": sorted(providers),
        "source_endpoints": sorted(endpoints),
        "source_provenance_fields_valid": source_provenance_fields_valid,
        "source_payload_raw_bytes_persisted": False,
        "source_payload_hash_independently_recomputable": False,
        "feature_payload_hashes_valid": feature_hashes_valid,
        "feature_reconstruction_status": "PASS" if corpus_integrity_valid else "BLOCKED",
        "observed_corpus_manifest_sha256": digest.hexdigest(),
        "event_start_timestamp_persisted": event_start_attested,
        "official_settlement_attestation_persisted": official_settlement_attested,
        "pregame_corpus_ready": False,
        "can_execute": False,
    }
    if not event_start_attested or not official_settlement_attested:
        blockers.append("SOURCE_PROVENANCE_NOT_CERTIFIED")
    return replay_rows, summary, corpus_integrity_valid


def _metrics(probabilities: list[float], outcomes: list[int]) -> dict[str, float]:
    if not probabilities or len(probabilities) != len(outcomes):
        raise ValueError("NBA_REPLAY_HOLDOUT_METRICS_INVALID")
    safe = [min(max(float(value), 1e-9), 1.0 - 1e-9) for value in probabilities]
    brier = sum((probability - outcome) ** 2 for probability, outcome in zip(safe, outcomes)) / len(safe)
    log_loss = -sum(
        outcome * math.log(probability) + (1 - outcome) * math.log(1 - probability)
        for probability, outcome in zip(safe, outcomes)
    ) / len(safe)
    accuracy = sum((probability >= 0.5) == bool(outcome) for probability, outcome in zip(safe, outcomes)) / len(safe)
    return {"brier": brier, "log_loss": log_loss, "accuracy": accuracy}


def _matches(actual: Any, expected: Any, *, tolerance: float = 1e-9) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return actual is expected
    try:
        return math.isclose(float(actual), float(expected), rel_tol=tolerance, abs_tol=tolerance)
    except (TypeError, ValueError, OverflowError):
        return False


def _calibrator_digest(row: Mapping[str, Any]) -> str:
    payload = {
        key: row.get(key)
        for key in (
            "calibrator_id", "sport", "calibrator_version", "model_artifact_version",
            "calibration_method", "coefficients", "calibration_row_count",
            "brier", "log_loss", "ece", "calibration_bias",
        )
    }
    return _sha256_json(payload)



def _replay_calibrator_fit(
    training_rows: list[FeatureRow],
    calibrator_row: Mapping[str, Any],
) -> dict[str, Any]:
    """Recreate the walk-forward Platt fit from the exact pre-holdout corpus."""
    try:
        raw, outcomes, folds, timestamps = _walk_forward_raw(training_rows)
        fit = phase_b_platt(raw, outcomes, folds, timestamps)
        coefficients = fit.coefficients
        metrics = fit.metrics
        row_coefficients = calibrator_row.get("coefficients")
        if not isinstance(row_coefficients, Mapping):
            raise ValueError("NBA_REPLAY_CALIBRATOR_COEFFICIENTS_INVALID")
        replay_metrics = {
            "brier": float(metrics.brier),
            "log_loss": float(metrics.log_loss),
            "ece": float(metrics.ece),
            "calibration_bias": float(metrics.calibration_bias),
        }
        replay_coefficients = {"a": float(coefficients.a), "b": float(coefficients.b)}
        matches = (
            fit.result is not None
            and fit.result.calibration_status == calibrator_row.get("calibration_method")
            and len(raw) == calibrator_row.get("calibration_row_count")
            and _matches(replay_coefficients["a"], row_coefficients.get("a"))
            and _matches(replay_coefficients["b"], row_coefficients.get("b"))
            and all(_matches(replay_metrics[key], calibrator_row.get(key)) for key in replay_metrics)
        )
        digest = hashlib.sha256()
        for raw_probability, outcome, fold_id, timestamp in zip(raw, outcomes, folds, timestamps):
            digest.update(
                f"{timestamp}|{int(fold_id)}|{float(raw_probability):.17g}|{int(outcome)}\n".encode("utf-8")
            )
        digest.update(_sha256_json({
            "calibration_method": fit.result.calibration_status if fit.result else None,
            "coefficients": replay_coefficients,
            "metrics": replay_metrics,
            "calibration_row_count": len(raw),
        }).encode("ascii"))
        return {
            "status": "PASS" if matches else "MISMATCH",
            "matches_persisted_calibrator": bool(matches),
            "calibration_row_count": len(raw),
            "fold_count": len(set(int(value) for value in folds)),
            "evaluated_fold_count": len(fit.fold_train_audit),
            "coefficients": replay_coefficients,
            "metrics": replay_metrics,
            "sha256": digest.hexdigest(),
        }
    except Exception as exc:  # noqa: BLE001 - preserve calibration replay failure, never fall back
        return {
            "status": "FAILED",
            "matches_persisted_calibrator": False,
            "error_type": type(exc).__name__,
        }

def _read_failure(stage: str, exc: Exception) -> dict[str, Any]:
    return {
        "sport": SPORT,
        "status": "READ_FAILED",
        "read_stage": stage,
        "error_type": type(exc).__name__,
        "artifact_ready": False,
        "calibrator_ready": False,
        "pregame_corpus_ready": False,
        "exact_bundle_match": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def run_nba_team_event_certification_replay(db: Any) -> dict[str, Any]:
    """Recompute the NBA persisted artifact's chronological holdout, read-only."""
    try:
        artifact_row = _read_one(
            db, ARTIFACT_TABLE, ARTIFACT_FIELDS,
            filters={"sport": SPORT}, order_by="created_at",
        )
    except Exception as exc:  # noqa: BLE001 - preserve registry read failure as a read failure
        return _read_failure("ARTIFACT_REGISTRY", exc)

    if artifact_row is None:
        return {
            "sport": SPORT,
            "status": "CANDIDATE_ARTIFACT_MISSING",
            "blockers": ["CANDIDATE_ARTIFACT_MISSING"],
            "artifact_ready": False,
            "calibrator_ready": False,
            "pregame_corpus_ready": False,
            "exact_bundle_match": False,
            "automatic_certification": False,
            "automatic_promotion": False,
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        }

    artifact, artifact_blockers, artifact_integrity_valid, artifact_ready = _artifact_from_row(artifact_row)
    if artifact is None:
        return {
            "sport": SPORT,
            "status": "ARTIFACT_IDENTITY_INVALID",
            "blockers": artifact_blockers,
            "model_artifact_version": artifact_row.get("model_artifact_version"),
            "artifact_checksum": artifact_row.get("artifact_checksum"),
            "artifact_ready": False,
            "calibrator_ready": False,
            "pregame_corpus_ready": False,
            "exact_bundle_match": False,
            "automatic_certification": False,
            "automatic_promotion": False,
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        }

    version = artifact.model_artifact_version
    try:
        calibrator_row = _read_one(
            db, CALIBRATOR_TABLE, CALIBRATOR_FIELDS,
            filters={"sport": SPORT, "model_artifact_version": version},
            order_by="created_at",
        )
    except Exception as exc:  # noqa: BLE001
        return _read_failure("CALIBRATOR_REGISTRY", exc)

    if calibrator_row is None:
        return {
            "sport": SPORT,
            "status": "CALIBRATOR_MISSING",
            "blockers": list(dict.fromkeys(artifact_blockers + ["CALIBRATOR_NOT_READY"])),
            "model_artifact_version": version,
            "artifact_ready": artifact_ready,
            "calibrator_ready": False,
            "pregame_corpus_ready": False,
            "exact_bundle_match": False,
            "automatic_certification": False,
            "automatic_promotion": False,
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        }

    coefficients = calibrator_row.get("coefficients")
    calibrator_identity_valid = (
        str(calibrator_row.get("sport") or "").upper() == SPORT
        and str(calibrator_row.get("model_artifact_version") or "") == version
        and str(calibrator_row.get("calibration_method") or "") == "PLATT_TIME_SPLIT_V1"
        and isinstance(coefficients, Mapping)
        and all(
            not isinstance(coefficients.get(key), bool)
            and isinstance(coefficients.get(key), (int, float))
            and math.isfinite(float(coefficients[key]))
            for key in ("a", "b")
        )
        and type(calibrator_row.get("calibration_row_count")) is int
        and calibrator_row.get("calibration_row_count", 0) > 0
        and all(
            isinstance(calibrator_row.get(key), (int, float))
            and not isinstance(calibrator_row.get(key), bool)
            and math.isfinite(float(calibrator_row[key]))
            for key in ("brier", "log_loss", "ece", "calibration_bias")
        )
        and 0.0 <= float(calibrator_row["brier"]) <= 1.0
        and float(calibrator_row["log_loss"]) >= 0.0
        and 0.0 <= float(calibrator_row["ece"]) <= 1.0
        and -1.0 <= float(calibrator_row["calibration_bias"]) <= 1.0
    )
    calibrator_checksum = _calibrator_digest(calibrator_row)
    # The current table has no persisted calibrator checksum or fit-corpus hash.
    calibrator_checksum_persisted = bool(calibrator_row.get("calibrator_checksum"))

    try:
        certification_row = _read_one(
            db, CERTIFICATION_TABLE, CERTIFICATION_FIELDS,
            filters={"sport": SPORT}, order_by=None,
        )
        immutable_certification = _read_one(
            db, IMMUTABLE_CERTIFICATION_TABLE, IMMUTABLE_CERTIFICATION_FIELDS,
            filters={"sport": SPORT}, order_by="certified_at",
        )
    except Exception as exc:  # noqa: BLE001
        return _read_failure("CERTIFICATION_REGISTRY", exc)

    certification_notes = (
        certification_row.get("certification_notes")
        if isinstance(certification_row, Mapping) else {}
    )
    certification_notes = certification_notes if isinstance(certification_notes, Mapping) else {}
    stored_holdout_evidence = certification_notes.get("untouched_holdout_evidence")
    stored_holdout_evidence = stored_holdout_evidence if isinstance(stored_holdout_evidence, Mapping) else {}
    calibration_training_rows = certification_notes.get("calibration_training_rows")
    calibration_holdout_overlap_rows = certification_notes.get("calibration_holdout_overlap_rows")
    calibrator_fit_cohort_metadata_valid = bool(
        type(calibration_training_rows) is int
        and calibration_training_rows == artifact.training_row_count
        and str(certification_notes.get("calibration_training_cutoff") or "") == artifact.train_end_date
        and type(calibration_holdout_overlap_rows) is int
        and calibration_holdout_overlap_rows == 0
        and type(calibrator_row.get("calibration_row_count")) is int
        and calibrator_row.get("calibration_row_count", 0) <= calibration_training_rows
        and stored_holdout_evidence.get("holdout_rows") == artifact.holdout_row_count
        and stored_holdout_evidence.get("publication_authorized") is False
        and stored_holdout_evidence.get("can_execute") is False
    )
    calibrator_fit_replay_status = "NOT_RUN"
    calibrator_fit_replay_sha256 = None
    calibrator_fit_replay_metrics: dict[str, Any] | None = None
    calibrator_fit_evidence_ready = False
    calibrator_ready = False

    try:
        games = _read_all(db, TRAINING_TABLE, TRAINING_FIELDS, settled_only=True)
        features = _read_all(db, FEATURE_TABLE, FEATURE_FIELDS)
    except Exception as exc:  # noqa: BLE001
        return _read_failure("PREGAME_CORPUS", exc)

    try:
        replay_rows, corpus_summary, corpus_integrity_valid = _corpus_replay(games, features, artifact)
    except Exception as exc:  # noqa: BLE001 - isolate NBA replay failure from the D1 replay route
        return {
            "sport": SPORT,
            "status": "REPLAY_FAILED",
            "replay_stage": "PREGAME_CORPUS_RECONSTRUCTION",
            "error_type": type(exc).__name__,
            "error_message": str(exc)[:240],
            "model_artifact_version": version,
            "calibrator_version": calibrator_row.get("calibrator_version"),
            "artifact_ready": artifact_ready,
            "calibrator_ready": False,
            "pregame_corpus_ready": False,
            "exact_bundle_match": False,
            "automatic_certification": False,
            "automatic_promotion": False,
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        }
    blockers = list(artifact_blockers)
    if not calibrator_identity_valid or not calibrator_checksum_persisted:
        blockers.append("CALIBRATOR_NOT_READY")
    if not corpus_integrity_valid or not corpus_summary.get("pregame_corpus_ready"):
        blockers.append("SOURCE_PROVENANCE_NOT_CERTIFIED")

    split_valid = False
    raw_metrics: dict[str, float] | None = None
    calibrated_metrics: dict[str, Any] | None = None
    numerical_replay_status = "NOT_RUN"
    replay_metrics_match = False
    if replay_rows:
        cutoff = date.fromisoformat(artifact.train_end_date)
        training_rows = [row for row in replay_rows if row.game_date <= cutoff]
        holdout_rows = [row for row in replay_rows if row.game_date > cutoff]
        split_valid = (
            len(training_rows) == artifact.training_row_count
            and len(holdout_rows) == artifact.holdout_row_count
            and bool(training_rows)
            and bool(holdout_rows)
            and min(row.game_date for row in training_rows).isoformat() == artifact.train_start_date
            and max(row.game_date for row in training_rows).isoformat() == artifact.train_end_date
            and min(row.game_date for row in holdout_rows) > cutoff
            and len({row.game_id for row in replay_rows}) == len(replay_rows)
        )
        if not split_valid:
            blockers.append("CANDIDATE_PARTITION_METRICS_MISMATCH")
        elif calibrator_identity_valid:
            calibrator_fit_replay = _replay_calibrator_fit(training_rows, calibrator_row)
            calibrator_fit_replay_status = str(calibrator_fit_replay.get("status") or "FAILED")
            calibrator_fit_replay_sha256 = calibrator_fit_replay.get("sha256")
            calibrator_fit_replay_metrics = calibrator_fit_replay.get("metrics")
            calibrator_fit_evidence_ready = calibrator_fit_replay_status == "PASS"
            calibrator_ready = bool(
                calibrator_identity_valid
                and calibrator_checksum_persisted
                and calibrator_fit_cohort_metadata_valid
                and calibrator_fit_evidence_ready
            )
            try:
                outcomes = [row.outcome for row in holdout_rows]
                raw_probabilities = [artifact.raw_probability(row.values) for row in holdout_rows]
                raw_metrics = _metrics(raw_probabilities, outcomes)
                calibrated_metrics = evaluate_untouched_calibrated_holdout(
                    artifact,
                    holdout_rows,
                    SimpleNamespace(a=float(coefficients["a"]), b=float(coefficients["b"])),
                )
                notes = certification_row.get("certification_notes") if isinstance(certification_row, Mapping) else {}
                notes = notes if isinstance(notes, Mapping) else {}
                stored_calibrated = notes.get("untouched_holdout_evidence")
                stored_calibrated = stored_calibrated if isinstance(stored_calibrated, Mapping) else {}
                replay_metrics_match = (
                    _matches(raw_metrics["brier"], artifact_row.get("holdout_brier"))
                    and _matches(raw_metrics["log_loss"], artifact_row.get("holdout_log_loss"))
                    and _matches(raw_metrics["accuracy"], artifact_row.get("holdout_accuracy"))
                    and _matches(calibrated_metrics.get("calibrated_brier"), stored_calibrated.get("calibrated_brier"))
                    and _matches(calibrated_metrics.get("calibrated_log_loss"), stored_calibrated.get("calibrated_log_loss"))
                    and _matches(calibrated_metrics.get("calibration_bias"), stored_calibrated.get("calibration_bias"))
                    and calibrated_metrics.get("holdout_rows") == stored_calibrated.get("holdout_rows")
                    and calibrated_metrics.get("holdout_start_date") == stored_calibrated.get("holdout_start_date")
                    and calibrated_metrics.get("holdout_end_date") == stored_calibrated.get("holdout_end_date")
                )
                numerical_replay_status = "PASS" if replay_metrics_match else "METRICS_MISMATCH"
            except Exception:  # noqa: BLE001 - no numeric fallback on replay failure
                numerical_replay_status = "FAILED"
        else:
            numerical_replay_status = "CALIBRATOR_IDENTITY_INVALID"
            calibrator_fit_replay_status = "CALIBRATOR_IDENTITY_INVALID"

    if numerical_replay_status != "PASS":
        blockers.append("CANDIDATE_PARTITION_METRICS_MISMATCH")
    if calibrator_fit_replay_status != "PASS":
        blockers.append("CALIBRATOR_NOT_READY")

    certification_binding_valid = bool(
        certification_row
        and certification_row.get("model_artifact_version") == version
        and certification_row.get("calibrator_version") == calibrator_row.get("calibrator_version")
        and str(certification_row.get("capability_status") or "").upper() == "SHADOW"
        and str(certification_row.get("reason_code") or "") == "CERTIFICATION_REPLAY_REQUIRED"
    )
    immutable_certification_present = bool(immutable_certification)
    if not certification_binding_valid:
        blockers.append("CANDIDATE_ARTIFACT_IDENTITY_INCOMPLETE")

    dataset_hash = artifact_row.get("training_dataset_hash") or (
        artifact_row.get("artifact_payload") or {}
    ).get("training_dataset_hash")
    training_code_sha = artifact_row.get("training_code_sha") or (
        artifact_row.get("artifact_payload") or {}
    ).get("training_code_sha")
    corpus_summary.update({
        "artifact_corpus_hash_bound": _is_sha256(dataset_hash) and str(dataset_hash).lower() == corpus_summary["observed_corpus_manifest_sha256"],
        "artifact_training_code_sha_present": bool(str(training_code_sha or "").strip()),
        "calibrator_checksum_persisted": calibrator_checksum_persisted,
        "calibrator_fit_corpus_hash_persisted": False,
        "calibrator_fit_cohort_metadata_valid": calibrator_fit_cohort_metadata_valid,
        "calibrator_fit_replay_status": calibrator_fit_replay_status,
        "calibrator_fit_replay_sha256": calibrator_fit_replay_sha256,
        "official_settlement_attestation_persisted": False,
        "event_start_timestamp_persisted": False,
    })
    pregame_corpus_ready = bool(
        corpus_integrity_valid
        and corpus_summary.get("artifact_corpus_hash_bound")
        and corpus_summary.get("source_payload_hash_independently_recomputable")
        and corpus_summary.get("event_start_timestamp_persisted")
        and corpus_summary.get("official_settlement_attestation_persisted")
    )
    corpus_summary["pregame_corpus_ready"] = pregame_corpus_ready
    exact_bundle_match = bool(artifact_ready and calibrator_ready and pregame_corpus_ready and certification_binding_valid)

    if not corpus_summary["artifact_corpus_hash_bound"] or not corpus_summary["artifact_training_code_sha_present"]:
        if "CANDIDATE_ARTIFACT_IDENTITY_INCOMPLETE" not in blockers:
            blockers.append("CANDIDATE_ARTIFACT_IDENTITY_INCOMPLETE")
    if not corpus_summary["event_start_timestamp_persisted"] or not corpus_summary["official_settlement_attestation_persisted"]:
        if "SOURCE_PROVENANCE_NOT_CERTIFIED" not in blockers:
            blockers.append("SOURCE_PROVENANCE_NOT_CERTIFIED")

    status = (
        "REPLAY_EVIDENCE_READY_FOR_INDEPENDENT_REVIEW"
        if exact_bundle_match and numerical_replay_status == "PASS"
        else "REPLAY_EVIDENCE_REPRODUCED_BUT_BUNDLE_INCOMPLETE"
        if numerical_replay_status == "PASS"
        else "NUMERICAL_REPLAY_BLOCKED"
    )
    evidence_identity = {
        "sport": SPORT,
        "artifact_id": artifact_row.get("artifact_id"),
        "model_artifact_version": version,
        "artifact_checksum": artifact_row.get("artifact_checksum"),
        "calibrator_id": calibrator_row.get("calibrator_id"),
        "calibrator_version": calibrator_row.get("calibrator_version"),
        "calibrator_fit_replay_status": calibrator_fit_replay_status,
        "calibrator_fit_replay_sha256": calibrator_fit_replay_sha256,
        "observed_corpus_manifest_sha256": corpus_summary.get("observed_corpus_manifest_sha256"),
        "feature_reconstruction_status": corpus_summary.get("feature_reconstruction_status"),
        "artifact_ready": artifact_ready,
        "calibrator_ready": calibrator_ready,
        "pregame_corpus_ready": pregame_corpus_ready,
        "exact_bundle_match": exact_bundle_match,
        "certification_binding_valid": certification_binding_valid,
        "immutable_certification_record_present": immutable_certification_present,
        "numerical_replay_status": numerical_replay_status,
        "raw_metrics": raw_metrics,
        "calibrated_metrics": calibrated_metrics,
        "blockers": sorted(set(blockers)),
    }
    return {
        "sport": SPORT,
        "status": status,
        "blockers": sorted(set(blockers)),
        "model_artifact_id": artifact_row.get("artifact_id"),
        "model_artifact_version": version,
        "model_family": artifact.model_family,
        "artifact_checksum": artifact_row.get("artifact_checksum"),
        "artifact_integrity_valid": artifact_integrity_valid,
        "artifact_ready": artifact_ready,
        "training_dataset_hash": dataset_hash,
        "training_code_sha": training_code_sha,
        "calibrator_id": calibrator_row.get("calibrator_id"),
        "calibrator_version": calibrator_row.get("calibrator_version"),
        "calibrator_sha256_observed": calibrator_checksum,
        "calibrator_model_artifact_version_match": calibrator_identity_valid,
        "calibrator_checksum_persisted": calibrator_checksum_persisted,
        "calibrator_fit_cohort_metadata_valid": calibrator_fit_cohort_metadata_valid,
        "calibrator_fit_replay_status": calibrator_fit_replay_status,
        "calibrator_fit_replay_sha256": calibrator_fit_replay_sha256,
        "calibrator_fit_replay_metrics": calibrator_fit_replay_metrics,
        "calibrator_fit_evidence_ready": calibrator_fit_evidence_ready,
        "calibrator_ready": calibrator_ready,
        "calibration_row_count": calibrator_row.get("calibration_row_count"),
        "calibration_metrics": {
            "brier": calibrator_row.get("brier"),
            "log_loss": calibrator_row.get("log_loss"),
            "ece": calibrator_row.get("ece"),
            "calibration_bias": calibrator_row.get("calibration_bias"),
        },
        "pregame_corpus": corpus_summary,
        "pregame_corpus_ready": pregame_corpus_ready,
        "exact_bundle_match": exact_bundle_match,
        "certification_summary_status": certification_row.get("capability_status") if certification_row else None,
        "certification_summary_reason": certification_row.get("reason_code") if certification_row else None,
        "certification_binding_valid": certification_binding_valid,
        "immutable_certification_record_present": immutable_certification_present,
        "numerical_replay_status": numerical_replay_status,
        "numerical_replay_metrics_match": replay_metrics_match,
        "raw_holdout_metrics": raw_metrics,
        "calibrated_holdout_metrics": calibrated_metrics,
        "replay_evidence_sha256": _sha256_json(evidence_identity),
        "immutable_replay_receipt_persisted": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
        "next_gate": (
            "PERSIST_ARTIFACT_CORPUS_AND_TRAINING_CODE_BINDING; "
            "PERSIST_CALIBRATOR_CHECKSUM_AND_WALK_FORWARD_FIT_EVIDENCE; "
            "ADD_EVENT_START_AND_OFFICIAL_SETTLEMENT_ATTESTATION; "
            "THEN_INDEPENDENT_SIRT_REVIEW"
        ),
    }


__all__ = ["CAN_EXECUTE", "run_nba_team_event_certification_replay"]
