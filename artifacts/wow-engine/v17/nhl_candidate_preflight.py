"""Evidence-only preflight for NHL candidate cohorts and current-game inputs."""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import math
from typing import Any, Mapping, Sequence

from nhl_candidate_pipeline import (
    FEATURE_NAMES,
    FEATURE_RECONSTRUCTION_VERSION,
    FEATURE_SCHEMA_VERSION,
    MODEL_FAMILY,
    SOURCE_ID,
)

CAN_EXECUTE = False


class NHLCandidatePreflightError(ValueError):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(detail or code)


def _sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _is_sha256(value: Any) -> bool:
    token = str(value or "").strip().lower()
    return len(token) == 64 and all(ch in "0123456789abcdef" for ch in token)


def _positive_count(value: Any) -> bool:
    return type(value) is int and value > 0


def _event_id(row: Mapping[str, Any]) -> str:
    return str(row.get("official_event_id") or "").strip()


def _parse_time(value: Any, *, event_id: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise NHLCandidatePreflightError(
            "NHL_CANDIDATE_EVENT_TIME_INVALID", event_id
        ) from exc
    if parsed.utcoffset() is None:
        raise NHLCandidatePreflightError(
            "NHL_CANDIDATE_EVENT_TIME_INVALID", event_id
        )
    return parsed


def _training_dataset_hash(
    rows: Sequence[Mapping[str, Any]], *, model_family: str
) -> str:
    payload_rows = []
    for row in rows:
        event_id = _event_id(row)
        features = row.get("features")
        if not isinstance(features, Mapping) or set(features) != set(FEATURE_NAMES):
            raise NHLCandidatePreflightError(
                "NHL_CANDIDATE_FEATURE_SCHEMA_MISMATCH", event_id
            )
        values: dict[str, float] = {}
        for name in FEATURE_NAMES:
            value = features[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise NHLCandidatePreflightError(
                    "NHL_CANDIDATE_FEATURE_INVALID", f"{event_id}:{name}"
                )
            number = float(value)
            if not math.isfinite(number):
                raise NHLCandidatePreflightError(
                    "NHL_CANDIDATE_FEATURE_INVALID", f"{event_id}:{name}"
                )
            values[name] = number
        if type(row.get("positive_outcome")) is not bool:
            raise NHLCandidatePreflightError(
                "NHL_CANDIDATE_OUTCOME_MISSING", event_id
            )
        manifest = row.get("source_manifest")
        manifest_sha = str(row.get("source_manifest_sha256") or "").strip().lower()
        if (
            not isinstance(manifest, Mapping)
            or not _is_sha256(manifest_sha)
            or _sha256(manifest) != manifest_sha
            or manifest.get("source_id") != SOURCE_ID
            or manifest.get("feature_schema_version") != FEATURE_SCHEMA_VERSION
            or manifest.get("reconstruction_version") != FEATURE_RECONSTRUCTION_VERSION
            or manifest.get("historical_reconstruction") is not True
            or manifest.get("reconstruction_basis") != "PRIOR_SETTLED_GAMES_ONLY"
            or manifest.get("archive_pregame_snapshot_claimed") is not False
            or not _is_sha256(manifest.get("game_source_payload_sha256"))
            or manifest.get("market_features_used") is not False
            or manifest.get("can_execute") is not False
        ):
            raise NHLCandidatePreflightError(
                "NHL_CANDIDATE_SOURCE_MANIFEST_INVALID", event_id
            )
        payload_rows.append(
            {
                "event_id": event_id,
                "event_start_time": str(row.get("event_start_time") or ""),
                "feature_as_of": str(row.get("feature_as_of") or ""),
                "positive_outcome": row["positive_outcome"],
                "features": values,
                "source_manifest_sha256": manifest_sha,
            }
        )
    return _sha256(
        {
            "model_family": model_family,
            "feature_names": list(FEATURE_NAMES),
            "rows": payload_rows,
        }
    )


def preflight_nhl_candidate_package(package: Mapping[str, Any]) -> dict[str, Any]:
    """Validate source identity, exact partitions, and dataset hash before persistence."""
    games = package.get("games")
    feature_rows = package.get("feature_rows")
    candidate = package.get("candidate")
    if (
        not isinstance(games, list)
        or not isinstance(feature_rows, list)
        or not isinstance(candidate, Mapping)
        or not games
        or not feature_rows
    ):
        raise NHLCandidatePreflightError(
            "NHL_CANDIDATE_PACKAGE_INCOMPLETE", "games, feature_rows, and candidate are required"
        )
    if (
        candidate.get("sport") != "NHL"
        or candidate.get("league") != "NHL"
        or candidate.get("model_family") != MODEL_FAMILY
        or candidate.get("source_policy_id") != SOURCE_ID
        or candidate.get("feature_schema_version") != FEATURE_SCHEMA_VERSION
    ):
        raise NHLCandidatePreflightError(
            "NHL_CANDIDATE_IDENTITY_MISMATCH", "candidate identity or feature schema differs"
        )
    if (
        candidate.get("lifecycle_state") != "CANDIDATE"
        or candidate.get("promoted") is not False
        or candidate.get("active") is not False
        or (
            candidate.get("automatic_certification") is not None
            and candidate.get("automatic_certification") is not False
        )
        or (
            candidate.get("automatic_promotion") is not None
            and candidate.get("automatic_promotion") is not False
        )
        or candidate.get("probability_publishable") is not False
        or candidate.get("can_execute") is not False
        or package.get("automatic_certification") is not False
        or package.get("automatic_promotion") is not False
        or package.get("probability_publishable") is not False
        or package.get("can_execute") is not False
    ):
        raise NHLCandidatePreflightError(
            "NHL_CANDIDATE_GOVERNANCE_FLAGS_INVALID", "candidate must remain research-only"
        )
    artifact = candidate.get("artifact_payload")
    if (
        not isinstance(artifact, Mapping)
        or tuple(artifact.get("feature_names") or ()) != FEATURE_NAMES
    ):
        raise NHLCandidatePreflightError(
            "NHL_CANDIDATE_FEATURE_SCHEMA_MISMATCH", "artifact feature names differ"
        )
    artifact_checksum = str(candidate.get("artifact_checksum") or "").strip().lower()
    if not _is_sha256(artifact_checksum) or artifact_checksum != _sha256(artifact):
        raise NHLCandidatePreflightError(
            "NHL_CANDIDATE_ARTIFACT_CHECKSUM_MISMATCH", "artifact checksum does not match payload"
        )
    metrics = candidate.get("validation_metrics")
    if not isinstance(metrics, Mapping):
        raise NHLCandidatePreflightError(
            "NHL_CANDIDATE_PARTITIONS_INVALID", "validation metrics are required"
        )
    if metrics.get("market_features_used") is not False:
        raise NHLCandidatePreflightError(
            "NHL_CANDIDATE_MARKET_FEATURES_FORBIDDEN", "market-derived inputs are not model features"
        )
    stored_counts = (
        candidate.get("training_rows"),
        candidate.get("calibration_rows"),
        candidate.get("test_rows"),
    )
    fitted_counts = (
        metrics.get("train_n"),
        metrics.get("calibration_n"),
        metrics.get("test_n"),
    )
    if (
        not all(_positive_count(value) for value in stored_counts)
        or not all(_positive_count(value) for value in fitted_counts)
        or stored_counts != fitted_counts
        or sum(stored_counts) != len(feature_rows)
        or sum(fitted_counts) != len(feature_rows)
    ):
        raise NHLCandidatePreflightError(
            "NHL_CANDIDATE_PARTITION_MISMATCH",
            f"stored={stored_counts}; fitted={fitted_counts}; source_rows={len(feature_rows)}",
        )
    if artifact.get("model_family") != MODEL_FAMILY:
        raise NHLCandidatePreflightError(
            "NHL_CANDIDATE_ARTIFACT_IDENTITY_MISMATCH", "embedded model family differs"
        )
    if (
        artifact.get("split_policy") != "CHRONOLOGICAL_60_20_20"
        or type(artifact.get("train_end_index")) is not int
        or artifact.get("train_end_index") != fitted_counts[0]
        or type(artifact.get("calibration_end_index")) is not int
        or artifact.get("calibration_end_index") != fitted_counts[0] + fitted_counts[1]
    ):
        raise NHLCandidatePreflightError(
            "NHL_CANDIDATE_ARTIFACT_SPLIT_MISMATCH",
            "embedded split policy or boundaries differ from fitted counts",
        )

    game_by_id: dict[str, Mapping[str, Any]] = {}
    game_identities: set[tuple[str, str, str, str]] = set()
    for game in games:
        if not isinstance(game, Mapping):
            raise NHLCandidatePreflightError(
                "NHL_CANDIDATE_SOURCE_EVENT_INVALID", "source event must be an object"
            )
        event_id = _event_id(game)
        if not event_id or event_id in game_by_id:
            raise NHLCandidatePreflightError(
                "NHL_CANDIDATE_DUPLICATE_IDENTITY", event_id or "empty official event id"
            )
        source_sha = game.get("source_payload_sha256")
        if (
            game.get("source_provider") != SOURCE_ID
            or not _is_sha256(source_sha)
            or not str(game.get("source_uri") or "").startswith(
                "https://api-web.nhle.com/v1/club-schedule-season/"
            )
            or not str(game.get("source_retrieved_at") or "").strip()
            or game.get("historical_reconstruction") is not True
            or game.get("can_execute") is not False
        ):
            raise NHLCandidatePreflightError(
                "NHL_CANDIDATE_SOURCE_PIN_MISSING", event_id
            )
        identity = (
            str(game.get("season") or ""),
            str(game.get("event_start_time") or ""),
            str(game.get("home_team") or "").upper(),
            str(game.get("away_team") or "").upper(),
        )
        if identity in game_identities:
            raise NHLCandidatePreflightError(
                "NHL_CANDIDATE_DUPLICATE_IDENTITY", event_id
            )
        game_identities.add(identity)
        game_by_id[event_id] = game

    seen_feature_ids: set[str] = set()
    previous_start: datetime | None = None
    for row in feature_rows:
        if not isinstance(row, Mapping):
            raise NHLCandidatePreflightError(
                "NHL_CANDIDATE_FEATURE_ROW_INVALID", "feature row must be an object"
            )
        event_id = _event_id(row)
        source = game_by_id.get(event_id)
        if not event_id or event_id in seen_feature_ids or source is None:
            raise NHLCandidatePreflightError(
                "NHL_CANDIDATE_DUPLICATE_IDENTITY", event_id or "unmatched official event id"
            )
        seen_feature_ids.add(event_id)
        if (
            row.get("sport") != "NHL"
            or row.get("league") != "NHL"
            or row.get("feature_schema_version") != FEATURE_SCHEMA_VERSION
            or row.get("historical_reconstruction") is not True
            or row.get("archived_pregame_snapshot") is not False
            or row.get("market_features_used") is not False
            or row.get("can_execute") is not False
            or row.get("event_start_time") != source.get("event_start_time")
            or row.get("positive_outcome") is not source.get("positive_outcome")
        ):
            raise NHLCandidatePreflightError(
                "NHL_CANDIDATE_SOURCE_IDENTITY_MISMATCH", event_id
            )
        manifest = row.get("source_manifest")
        if (
            not isinstance(manifest, Mapping)
            or manifest.get("game_source_payload_sha256")
            != source.get("source_payload_sha256")
        ):
            raise NHLCandidatePreflightError(
                "NHL_CANDIDATE_SOURCE_IDENTITY_MISMATCH", event_id
            )
        event_start = _parse_time(row.get("event_start_time"), event_id=event_id)
        feature_as_of = _parse_time(row.get("feature_as_of"), event_id=event_id)
        if feature_as_of >= event_start:
            raise NHLCandidatePreflightError(
                "NHL_CANDIDATE_TEMPORAL_LEAKAGE", event_id
            )
        if previous_start is not None and event_start < previous_start:
            raise NHLCandidatePreflightError(
                "NHL_CANDIDATE_ROWS_NOT_CHRONOLOGICAL", event_id
            )
        previous_start = event_start

    expected_hash = _training_dataset_hash(
        feature_rows, model_family=MODEL_FAMILY
    )
    actual_hash = str(candidate.get("training_dataset_hash") or "").strip().lower()
    if not _is_sha256(actual_hash) or actual_hash != expected_hash:
        raise NHLCandidatePreflightError(
            "NHL_CANDIDATE_DATASET_HASH_MISMATCH",
            f"expected={expected_hash}; actual={actual_hash or 'missing'}",
        )
    return {
        "status": "NHL_CANDIDATE_PREFLIGHT_READY",
        "source_event_n": len(games),
        "training_feature_n": len(feature_rows),
        "training_dataset_hash": actual_hash,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def validate_nhl_goalie_status_handoff(
    frozen: Mapping[str, Any], current: Mapping[str, Any], *, checked_at: str
) -> dict[str, Any]:
    """Verify confirmed goalies and source freshness; never qualify or invoke a scorer."""
    frozen_event = str(frozen.get("official_event_id") or "").strip()
    current_event = str(current.get("official_event_id") or "").strip()
    if not frozen_event or frozen_event != current_event:
        raise NHLCandidatePreflightError(
            "NHL_GOALIE_EVENT_IDENTITY_MISMATCH", current_event or "event id missing"
        )
    for snapshot in (frozen, current):
        if (
            snapshot.get("source_id") != SOURCE_ID
            or not str(snapshot.get("source_snapshot_id") or "").strip()
            or not _is_sha256(snapshot.get("source_payload_sha256"))
        ):
            raise NHLCandidatePreflightError(
                "NHL_GOALIE_SOURCE_PIN_MISSING", "official source and payload hash required"
            )
    try:
        inspected_at = _parse_time(checked_at, event_id=frozen_event)
        kickoff = _parse_time(frozen.get("event_start_time"), event_id=frozen_event)
        current_kickoff = _parse_time(current.get("event_start_time"), event_id=frozen_event)
        frozen_source_at = _parse_time(frozen.get("source_evidence_timestamp"), event_id=frozen_event)
        current_source_at = _parse_time(current.get("source_evidence_timestamp"), event_id=frozen_event)
    except NHLCandidatePreflightError as exc:
        raise NHLCandidatePreflightError("NHL_GOALIE_SOURCE_TIME_INVALID") from exc
    if kickoff != current_kickoff:
        raise NHLCandidatePreflightError("NHL_GOALIE_EVENT_TIME_MISMATCH")
    if inspected_at >= kickoff:
        raise NHLCandidatePreflightError("NHL_GOALIE_EVENT_ALREADY_STARTED")
    if frozen_source_at > inspected_at or current_source_at > inspected_at or current_source_at >= kickoff:
        raise NHLCandidatePreflightError("NHL_GOALIE_SOURCE_FROM_FUTURE")
    if current_source_at < frozen_source_at:
        raise NHLCandidatePreflightError("NHL_GOALIE_SOURCE_TIME_REGRESSION")
    # Conservative advisory-only freshness requirement; not a model certification.
    if inspected_at - current_source_at > timedelta(hours=2):
        raise NHLCandidatePreflightError("NHL_GOALIE_SOURCE_STALE")
    for side in ("home", "away"):
        frozen_goalie = frozen.get(side)
        current_goalie = current.get(side)
        if not isinstance(frozen_goalie, Mapping) or not isinstance(current_goalie, Mapping):
            raise NHLCandidatePreflightError("NHL_GOALIE_MISSING", side)
        frozen_id = str(frozen_goalie.get("goalie_id") or "").strip()
        current_id = str(current_goalie.get("goalie_id") or "").strip()
        frozen_status = str(frozen_goalie.get("starting_status") or "").strip().upper()
        current_status = str(current_goalie.get("starting_status") or "").strip().upper()
        if not frozen_id or not current_id:
            raise NHLCandidatePreflightError("NHL_GOALIE_MISSING", side)
        if frozen_id != current_id:
            raise NHLCandidatePreflightError(
                "NHL_STARTING_GOALIE_CHANGED", side
            )
        if not frozen_status or not current_status:
            raise NHLCandidatePreflightError(
                "NHL_GOALIE_STATUS_MISSING", side
            )
        if frozen_status != current_status:
            raise NHLCandidatePreflightError(
                "NHL_STARTING_STATUS_CHANGED", side
            )
        if current_status not in {"CONFIRMED", "OFFICIAL", "STARTING"}:
            raise NHLCandidatePreflightError("NHL_GOALIE_NOT_CONFIRMED", side)
    if str(frozen["home"]["goalie_id"]).strip() == str(frozen["away"]["goalie_id"]).strip():
        raise NHLCandidatePreflightError(
            "NHL_GOALIE_IDENTITY_INVALID", "home and away goalie identities must differ"
        )
    return {
        "status": "NHL_GOALIE_STATUS_UNCHANGED",
        "official_event_id": frozen_event,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "NHLCandidatePreflightError",
    "preflight_nhl_candidate_package",
    "validate_nhl_goalie_status_handoff",
]
