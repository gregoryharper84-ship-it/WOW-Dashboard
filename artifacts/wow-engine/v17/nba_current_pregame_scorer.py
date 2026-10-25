"""Unregistered fail-closed NBA current-pregame scorer integration seam.

Receipts from this module are engineering holds, not terminal-reducer inputs.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
import math
from typing import Any, Callable, Mapping

from basketball_team_event_specialist import (
    FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
    MODEL_FAMILY,
    LogisticArtifact,
)
from v17.fitted_team_event_certification import (
    TeamEventCertificationError,
    resolve_certification,
)
from v17.multisport_team_event_calibration import CalibrationArtifactInvalid
from v17.team_event_capability_manifest import CERTIFIED_TEAM_EVENT_SPORTS
from v17.nba_event_identity import (
    NBAEventIdentityError,
    resolve_nba_current_event_identity,
)

CAN_EXECUTE = False
CONFIRMED_ROTATION_STATES = frozenset({"CONFIRMED", "OFFICIAL", "COMPLETE", "PASS"})


def _timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _hold(
    code: str,
    blocker: str | tuple[str, ...],
    *,
    identity: Mapping[str, Any] | None = None,
    feature_package: Mapping[str, Any] | None = None,
    source_snapshot_id: str | None = None,
    typed_failure: str | None = None,
    certification: Any = None,
    champion_artifact: Mapping[str, Any] | None = None,
    calibration_artifact: Mapping[str, Any] | None = None,
    research_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    identity = identity if isinstance(identity, Mapping) else {}
    feature_package = feature_package if isinstance(feature_package, Mapping) else {}
    feature_audit = feature_package.get("feature_audit")
    feature_audit = feature_audit if isinstance(feature_audit, Mapping) else {}
    champion_artifact = champion_artifact if isinstance(champion_artifact, Mapping) else {}
    calibration_artifact = calibration_artifact if isinstance(calibration_artifact, Mapping) else {}
    return {
        "status": "HOLD",
        "code": code,
        "typed_failure": typed_failure,
        "blockers": [blocker] if isinstance(blocker, str) else list(blocker),
        "sport": "NBA",
        "official_event_id": identity.get("event_id") or feature_package.get("official_event_id"),
        "home_team_id": identity.get("home_team_id") or feature_package.get("home_team_id"),
        "away_team_id": identity.get("away_team_id") or feature_package.get("away_team_id"),
        "identity_provider": identity.get("identity_provider"),
        "identity_resolution": identity.get("identity_resolution"),
        "source_snapshot_id": source_snapshot_id,
        "source_snapshot_timestamp": feature_package.get("source_snapshot_timestamp"),
        "feature_timestamp": feature_audit.get("feature_timestamp"),
        "feature_as_of": feature_audit.get("feature_as_of"),
        "dataset_id": feature_audit.get("dataset_id"),
        "certification_id": getattr(certification, "certification_id", None),
        "model_artifact_id": getattr(certification, "artifact_id", None),
        "model_artifact_version": champion_artifact.get("model_artifact_version")
        or getattr(certification, "model_version", None),
        "model_artifact_sha256": getattr(certification, "artifact_sha256", None),
        "calibration_artifact_id": getattr(certification, "calibration_artifact_id", None),
        "calibration_version": calibration_artifact.get("calibration_version"),
        "calibration_artifact_sha256": getattr(certification, "calibration_sha256", None),
        "raw_probability": None,
        "calibrated_probability": None,
        "calibrated_lower_bound": None,
        "calibrated_upper_bound": None,
        "research_receipt": dict(research_receipt) if isinstance(research_receipt, Mapping) else None,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def _feature_blocker(
    feature_package: Mapping[str, Any] | None,
    identity: Mapping[str, Any],
    *,
    source_snapshot_id: str | None,
    event_start_time: str,
) -> str | None:
    if not isinstance(feature_package, Mapping):
        return "NBA_CURRENT_PREGAME_FEATURE_HYDRATION_UNAVAILABLE"
    if feature_package.get("status") != "PASS":
        return "NBA_CURRENT_PREGAME_FEATURES_NOT_READY"
    if str(feature_package.get("sport") or "").upper() != "NBA":
        return "NBA_FEATURE_SPORT_MISMATCH"
    if str(feature_package.get("official_event_id") or "") != str(identity.get("event_id") or ""):
        return "NBA_FEATURE_EVENT_IDENTITY_MISMATCH"
    if (
        str(feature_package.get("home_team_id") or "") != str(identity.get("home_team_id") or "")
        or str(feature_package.get("away_team_id") or "") != str(identity.get("away_team_id") or "")
    ):
        return "NBA_FEATURE_TEAM_IDENTITY_MISMATCH"
    if _timestamp(feature_package.get("event_start_time_utc")) != _timestamp(event_start_time):
        return "NBA_FEATURE_EVENT_TIME_MISMATCH"
    if str(feature_package.get("feature_schema_version") or "") != FEATURE_SCHEMA_VERSION:
        return "NBA_FEATURE_SCHEMA_MISMATCH"
    features = feature_package.get("features")
    if not isinstance(features, Mapping) or set(features) != set(FEATURE_NAMES):
        return "NBA_CURRENT_FEATURES_INCOMPLETE"
    for name in FEATURE_NAMES:
        value = features.get(name)
        if isinstance(value, bool):
            return "NBA_CURRENT_FEATURES_INVALID"
        try:
            if not math.isfinite(float(value)):
                return "NBA_CURRENT_FEATURES_INVALID"
        except (TypeError, ValueError):
            return "NBA_CURRENT_FEATURES_INVALID"

    audit = feature_package.get("feature_audit")
    if not isinstance(audit, Mapping):
        return "NBA_FEATURE_LINEAGE_MISSING"
    # A source captured with a runner clock and HTTP Date is useful research
    # but is explicitly NOT independently attested. The current NBA scorer
    # must refuse that input before any certified model inference, even if a
    # future release enables the controlling specialist and bounds policy.
    if audit.get("source_replay_status") != "RAW_CAPTURE_HASH_AND_EVENT_REPLAY_PASS":
        return "NBA_SOURCE_EVENT_REPLAY_NOT_VERIFIED"
    if audit.get("timestamp_attestation") != "VERIFIED":
        return "NBA_SOURCE_TIMESTAMP_ATTESTATION_NOT_VERIFIED"
    if not source_snapshot_id or feature_package.get("source_snapshot_id") != source_snapshot_id:
        return "NBA_SOURCE_SNAPSHOT_ID_MISMATCH"
    source_at = _timestamp(feature_package.get("source_snapshot_timestamp"))
    if source_at is None:
        return "NBA_SOURCE_SNAPSHOT_PROVENANCE_MISSING"
    if not audit.get("dataset_id"):
        return "NBA_FEATURE_DATASET_ID_MISSING"
    feature_at = _timestamp(audit.get("feature_timestamp"))
    event_at = _timestamp(event_start_time)
    if feature_at is None:
        return "NBA_FEATURE_TIMESTAMP_MISSING"
    if event_at is None or feature_at >= event_at:
        return "NBA_FEATURE_TIMESTAMP_INVALID_FOR_PREGAME"
    if source_at >= event_at:
        return "NBA_SOURCE_SNAPSHOT_AFTER_KICKOFF"
    if source_at > feature_at:
        return "NBA_SOURCE_SNAPSHOT_AFTER_FEATURE"
    feature_as_of = audit.get("feature_as_of")
    try:
        as_of_value = str(feature_as_of)
        as_of_datetime = datetime.fromisoformat(as_of_value.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return "NBA_FEATURE_AS_OF_INVALID"
    if as_of_datetime.utcoffset() is None:
        if len(as_of_value) != 10:
            return "NBA_FEATURE_AS_OF_INVALID"
        if (
            as_of_datetime.date() > source_at.date()
            or as_of_datetime.date() > feature_at.date()
            or as_of_datetime.date() > event_at.date()
        ):
            return "NBA_FEATURE_AS_OF_AFTER_CUTOFF"
    elif as_of_datetime.astimezone(timezone.utc) > min(source_at, feature_at, event_at):
        return "NBA_FEATURE_AS_OF_AFTER_CUTOFF"
    return None


def _rotation_blocker(
    lineup_evidence: Mapping[str, Any] | None,
    feature_package: Mapping[str, Any],
) -> str | None:
    if not isinstance(lineup_evidence, Mapping):
        return "NBA_LINEUP_EVIDENCE_MISSING"
    injury_report = lineup_evidence.get("injury_report")
    if not isinstance(injury_report, Mapping) or str(injury_report.get("status") or "").upper() not in {
        "CURRENT",
        "CONFIRMED",
        "OFFICIAL",
    }:
        return "NBA_INJURY_REPORT_NOT_CURRENT"

    feature_audit = feature_package.get("feature_audit")
    feature_audit = feature_audit if isinstance(feature_audit, Mapping) else {}
    feature_at = _timestamp(feature_audit.get("feature_timestamp"))
    latest_material_at = _timestamp(lineup_evidence.get("latest_material_update_timestamp"))
    if latest_material_at is None:
        return "NBA_LINEUP_UPDATE_TIMESTAMP_MISSING"
    if feature_at is None:
        return "NBA_FEATURE_TIMESTAMP_MISSING"
    if latest_material_at > feature_at:
        return "NBA_STARTER_CHANGED_AFTER_FEATURE_HYDRATION"
    rotation = str(lineup_evidence.get("expected_starters_rotation") or "").upper()
    if rotation == "CHANGED":
        return "NBA_CHANGED_STARTERS_RECEIPT_UNVERIFIED"
    if rotation not in CONFIRMED_ROTATION_STATES:
        return "NBA_STARTING_ROTATION_NOT_CONFIRMED"
    return None


def _research_receipt(
    artifact: Mapping[str, Any],
    certification: Any,
    feature_package: Mapping[str, Any],
) -> dict[str, Any]:
    payload = artifact.get("artifact_payload")
    if not isinstance(payload, Mapping):
        raise ValueError("NBA_CERTIFIED_MODEL_ARTIFACT_PAYLOAD_INVALID")
    fields = (
        "sport",
        "model_family",
        "model_artifact_version",
        "feature_schema_version",
        "feature_names",
        "intercept",
        "coefficients",
        "means",
        "scales",
    )
    if any(field not in payload for field in fields):
        raise ValueError("NBA_CERTIFIED_MODEL_ARTIFACT_PAYLOAD_INVALID")
    checksum_payload = {field: payload[field] for field in fields}
    try:
        checksum = hashlib.sha256(
            json.dumps(checksum_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    except (TypeError, ValueError) as exc:
        raise ValueError("NBA_CERTIFIED_MODEL_ARTIFACT_PAYLOAD_INVALID") from exc
    if (
        str(payload.get("sport") or "").upper() != "NBA"
        or payload.get("model_family") != MODEL_FAMILY
        or payload.get("model_artifact_version") != certification.model_version
        or payload.get("feature_schema_version") != FEATURE_SCHEMA_VERSION
        or tuple(payload.get("feature_names") or ()) != tuple(FEATURE_NAMES)
        or checksum != certification.artifact_sha256
    ):
        raise ValueError("NBA_CERTIFIED_MODEL_ARTIFACT_PAYLOAD_MISMATCH")
    try:
        fitted = LogisticArtifact(
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
            artifact_checksum=checksum,
        )
        values = tuple(float(feature_package["features"][name]) for name in FEATURE_NAMES)
        raw_probability = fitted.raw_probability(values)
    except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError) as exc:
        raise ValueError("NBA_CERTIFIED_MODEL_ARTIFACT_PAYLOAD_INVALID") from exc
    if (
        len(fitted.coefficients) != len(FEATURE_NAMES)
        or len(fitted.means) != len(FEATURE_NAMES)
        or len(fitted.scales) != len(FEATURE_NAMES)
        or any(not math.isfinite(value) for value in (*fitted.coefficients, *fitted.means, *fitted.scales))
        or any(value <= 0 for value in fitted.scales)
        or not math.isfinite(fitted.intercept)
        or not math.isfinite(raw_probability)
        or not 0 < raw_probability < 1
    ):
        raise ValueError("NBA_CERTIFIED_MODEL_OUTPUT_INVALID")
    audit = feature_package["feature_audit"]
    return {
        "status": "RESEARCH_ONLY_NOT_FOR_PUBLICATION",
        "raw_probability": raw_probability,
        "certification_id": certification.certification_id,
        "model_artifact_id": certification.artifact_id,
        "model_artifact_version": certification.model_version,
        "model_artifact_sha256": certification.artifact_sha256,
        "calibration_artifact_id": certification.calibration_artifact_id,
        "calibration_artifact_sha256": certification.calibration_sha256,
        "official_event_id": feature_package["official_event_id"],
        "source_snapshot_id": feature_package["source_snapshot_id"],
        "source_snapshot_timestamp": feature_package["source_snapshot_timestamp"],
        "feature_timestamp": audit["feature_timestamp"],
        "feature_as_of": audit["feature_as_of"],
        "dataset_id": audit["dataset_id"],
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def resolve_nba_champion_artifact(client: Any, certification: Any) -> Mapping[str, Any]:
    """Load the exact certified basketball champion using the current-main RPC."""
    try:
        response = client.rpc(
            "wow_basketball_certified_model_artifact",
            {"p_sport": "NBA"},
        ).execute()
    except Exception as exc:  # noqa: BLE001 - preserve this as a scorer infrastructure failure
        raise RuntimeError("NBA_CERTIFIED_MODEL_ARTIFACT_LOOKUP_FAILED") from exc

    data = getattr(response, "data", None)
    row = data[0] if isinstance(data, list) and data else data
    if not isinstance(row, Mapping):
        raise LookupError("NBA_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND")
    if (
        str(row.get("sport") or "").upper() != "NBA"
        or str(row.get("model_family") or "") != MODEL_FAMILY
        or str(row.get("model_artifact_version") or "") != certification.model_version
        or str(row.get("feature_schema_version") or "") != FEATURE_SCHEMA_VERSION
        or str(row.get("artifact_checksum") or "").lower() != certification.artifact_sha256
        or not isinstance(row.get("artifact_payload"), Mapping)
    ):
        raise ValueError("NBA_CERTIFIED_MODEL_ARTIFACT_CERTIFICATION_MISMATCH")
    return row


def prepare_nba_current_pregame_scorer_receipt(
    *,
    event_start_time: str,
    home_team_alias: str,
    away_team_alias: str,
    current_feature_package: Mapping[str, Any] | None,
    lineup_evidence: Mapping[str, Any] | None,
    source_snapshot_id: str | None,
    client: Any,
    identity_resolver: Callable[..., Mapping[str, Any]] = resolve_nba_current_event_identity,
    certification_resolver: Callable[..., Any] = resolve_certification,
    champion_artifact_resolver: Callable[[Any, Any], Mapping[str, Any]] = resolve_nba_champion_artifact,
    calibration_artifact_resolver: Callable[[Any, Any], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve current NBA identity and evidence, stopping at unapproved bounds."""
    try:
        identity = dict(
            identity_resolver(
                event_start_time=event_start_time,
                home_team_alias=home_team_alias,
                away_team_alias=away_team_alias,
            )
        )
    except NBAEventIdentityError as exc:
        return _hold(
            "MODEL_INPUTS_INSUFFICIENT",
            exc.code,
            source_snapshot_id=source_snapshot_id,
            typed_failure="MODEL_INPUTS_INSUFFICIENT",
        )
    except Exception:
        return _hold(
            "MODEL_SCORER_FAILED",
            "NBA_CANONICAL_IDENTITY_RESOLUTION_FAILED",
            source_snapshot_id=source_snapshot_id,
            typed_failure="MODEL_SCORER_FAILED",
        )
    if (
        not identity.get("event_id")
        or not identity.get("home_team_id")
        or not identity.get("away_team_id")
        or identity.get("home_team_id") == identity.get("away_team_id")
    ):
        return _hold(
            "MODEL_INPUTS_INSUFFICIENT",
            "NBA_CANONICAL_IDENTITY_INCOMPLETE",
            identity=identity,
            source_snapshot_id=source_snapshot_id,
            typed_failure="MODEL_INPUTS_INSUFFICIENT",
        )

    feature_blocker = _feature_blocker(
        current_feature_package,
        identity,
        source_snapshot_id=source_snapshot_id,
        event_start_time=event_start_time,
    )
    if feature_blocker:
        return _hold(
            "MODEL_INPUTS_INSUFFICIENT",
            feature_blocker,
            identity=identity,
            feature_package=current_feature_package,
            source_snapshot_id=source_snapshot_id,
            typed_failure="MODEL_INPUTS_INSUFFICIENT",
        )

    rotation_blocker = _rotation_blocker(lineup_evidence, current_feature_package)
    if rotation_blocker:
        return _hold(
            "MODEL_INPUTS_INSUFFICIENT",
            rotation_blocker,
            identity=identity,
            feature_package=current_feature_package,
            source_snapshot_id=source_snapshot_id,
            typed_failure="MODEL_INPUTS_INSUFFICIENT",
        )

    controlling_specialist = CERTIFIED_TEAM_EVENT_SPORTS.get("NBA")
    if not controlling_specialist:
        return _hold(
            "MODEL_UNAVAILABLE",
            "NBA_CONTROLLING_SPECIALIST_NOT_CERTIFIED",
            identity=identity,
            feature_package=current_feature_package,
            source_snapshot_id=source_snapshot_id,
            typed_failure="MODEL_UNAVAILABLE",
        )

    try:
        certification = certification_resolver(client, sport="NBA", league_scope="NBA")
    except TeamEventCertificationError as exc:
        if exc.code in {
            "TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED",
            "TEAM_EVENT_SPECIALIST_CERTIFICATION_REVOKED",
        }:
            typed = "MODEL_UNAVAILABLE"
        elif exc.code == "TEAM_EVENT_CERTIFICATION_REGISTRY_UNAVAILABLE":
            typed = "MODEL_SCORER_FAILED"
        elif exc.code == "TEAM_EVENT_CERTIFICATION_REGISTRY_INVALID_RESPONSE":
            typed = "MODEL_OUTPUT_INVALID"
        else:
            typed = "MODEL_OUTPUT_INVALID"
        return _hold(
            typed,
            exc.code,
            identity=identity,
            feature_package=current_feature_package,
            source_snapshot_id=source_snapshot_id,
            typed_failure=typed,
        )
    except Exception:
        return _hold(
            "MODEL_SCORER_FAILED",
            "NBA_CERTIFICATION_RESOLUTION_FAILED",
            identity=identity,
            feature_package=current_feature_package,
            source_snapshot_id=source_snapshot_id,
            typed_failure="MODEL_SCORER_FAILED",
        )

    if (
        certification.sport != "NBA"
        or certification.league_scope != "NBA"
        or certification.controlling_specialist != controlling_specialist
        or certification.model_family != MODEL_FAMILY
        or certification.feature_schema_version != FEATURE_SCHEMA_VERSION
    ):
        return _hold(
            "MODEL_UNAVAILABLE",
            "NBA_CERTIFICATION_SPECIALIST_SCOPE_OR_MODEL_MISMATCH",
            identity=identity,
            feature_package=current_feature_package,
            source_snapshot_id=source_snapshot_id,
            typed_failure="MODEL_UNAVAILABLE",
            certification=certification,
        )
    try:
        champion_artifact = champion_artifact_resolver(client, certification)
    except LookupError as exc:
        return _hold(
            "MODEL_UNAVAILABLE",
            str(exc),
            identity=identity,
            feature_package=current_feature_package,
            source_snapshot_id=source_snapshot_id,
            typed_failure="MODEL_UNAVAILABLE",
            certification=certification,
        )
    except ValueError as exc:
        return _hold(
            "MODEL_OUTPUT_INVALID",
            str(exc),
            identity=identity,
            feature_package=current_feature_package,
            source_snapshot_id=source_snapshot_id,
            typed_failure="MODEL_OUTPUT_INVALID",
            certification=certification,
        )
    except Exception:
        return _hold(
            "MODEL_SCORER_FAILED",
            "NBA_CHAMPION_ARTIFACT_RESOLUTION_FAILED",
            identity=identity,
            feature_package=current_feature_package,
            source_snapshot_id=source_snapshot_id,
            typed_failure="MODEL_SCORER_FAILED",
            certification=certification,
        )

    try:
        research_receipt = _research_receipt(
            champion_artifact,
            certification,
            current_feature_package,
        )
    except ValueError as exc:
        return _hold(
            "MODEL_OUTPUT_INVALID",
            str(exc),
            identity=identity,
            feature_package=current_feature_package,
            source_snapshot_id=source_snapshot_id,
            typed_failure="MODEL_OUTPUT_INVALID",
            certification=certification,
            champion_artifact=champion_artifact,
        )
    except Exception:
        return _hold(
            "MODEL_SCORER_FAILED",
            "NBA_FITTED_RESEARCH_SCORER_FAILED",
            identity=identity,
            feature_package=current_feature_package,
            source_snapshot_id=source_snapshot_id,
            typed_failure="MODEL_SCORER_FAILED",
            certification=certification,
            champion_artifact=champion_artifact,
        )

    if calibration_artifact_resolver is None:
        return _hold(
            "MODEL_INPUTS_INSUFFICIENT",
            "NBA_CALIBRATOR_PAYLOAD_RESOLVER_UNAVAILABLE",
            identity=identity,
            feature_package=current_feature_package,
            source_snapshot_id=source_snapshot_id,
            typed_failure="MODEL_INPUTS_INSUFFICIENT",
            certification=certification,
            champion_artifact=champion_artifact,
            research_receipt=research_receipt,
        )
    try:
        calibration_artifact = calibration_artifact_resolver(client, certification)
    except CalibrationArtifactInvalid as exc:
        return _hold(
            "MODEL_INPUTS_INSUFFICIENT",
            exc.blockers,
            identity=identity,
            feature_package=current_feature_package,
            source_snapshot_id=source_snapshot_id,
            typed_failure="MODEL_INPUTS_INSUFFICIENT",
            certification=certification,
            champion_artifact=champion_artifact,
            research_receipt=research_receipt,
        )
    except LookupError:
        return _hold(
            "MODEL_INPUTS_INSUFFICIENT",
            "NBA_CERTIFIED_CALIBRATOR_UNAVAILABLE",
            identity=identity,
            feature_package=current_feature_package,
            source_snapshot_id=source_snapshot_id,
            typed_failure="MODEL_INPUTS_INSUFFICIENT",
            certification=certification,
            champion_artifact=champion_artifact,
            research_receipt=research_receipt,
        )
    except ValueError:
        return _hold(
            "MODEL_OUTPUT_INVALID",
            "NBA_CERTIFIED_CALIBRATOR_INVALID",
            identity=identity,
            feature_package=current_feature_package,
            source_snapshot_id=source_snapshot_id,
            typed_failure="MODEL_OUTPUT_INVALID",
            certification=certification,
            champion_artifact=champion_artifact,
            research_receipt=research_receipt,
        )
    except Exception:
        return _hold(
            "MODEL_SCORER_FAILED",
            "NBA_CALIBRATOR_RESOLUTION_FAILED",
            identity=identity,
            feature_package=current_feature_package,
            source_snapshot_id=source_snapshot_id,
            typed_failure="MODEL_SCORER_FAILED",
            certification=certification,
            champion_artifact=champion_artifact,
            research_receipt=research_receipt,
        )

    return _hold(
        "MODEL_INPUTS_INSUFFICIENT",
        "NBA_DYNAMIC_BOUNDS_POLICY_NOT_APPROVED",
        identity=identity,
        feature_package=current_feature_package,
        source_snapshot_id=source_snapshot_id,
        typed_failure="MODEL_INPUTS_INSUFFICIENT",
        certification=certification,
        champion_artifact=champion_artifact,
        calibration_artifact=calibration_artifact,
        research_receipt=research_receipt,
    )


__all__ = [
    "CAN_EXECUTE",
    "prepare_nba_current_pregame_scorer_receipt",
    "resolve_nba_champion_artifact",
]
