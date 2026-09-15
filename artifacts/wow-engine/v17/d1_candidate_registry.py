"""Persistence boundary for D1 research candidate artifacts.

The registry is intentionally inert: it stores immutable source events,
chronological reconstructed feature rows, and CANDIDATE artifacts. It cannot
certify, promote, activate, publish, or execute a sporting probability.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
SOURCE_TABLE = "wow_d1_source_events"
TRAINING_TABLE = "wow_d1_training_rows"
CANDIDATE_TABLE = "wow_d1_candidate_artifacts"


class D1RegistryError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _one(result: Any) -> dict[str, Any] | None:
    data = getattr(result, "data", None) or []
    return dict(data[0]) if data else None


def _validate_governance(row: Mapping[str, Any], *, prefix: str) -> None:
    if row.get("can_execute") is not False:
        raise D1RegistryError(f"{prefix}_CAN_EXECUTE_FORBIDDEN", "can_execute must be false")
    if row.get("probability_publishable") not in (None, False):
        raise D1RegistryError(f"{prefix}_PROBABILITY_PUBLICATION_FORBIDDEN", "candidate registry is non-publishable")


def persist_source_events(db: Any, rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    inserted_n = 0
    existing_n = 0
    for raw in rows:
        _validate_governance(raw, prefix="D1_SOURCE")
        sport = str(raw.get("sport") or "").strip().upper()
        event_id = str(raw.get("official_event_id") or "").strip()
        payload_hash = str(raw.get("source_payload_sha256") or "").strip().lower()
        if not sport or not event_id or len(payload_hash) != 64:
            raise D1RegistryError("D1_SOURCE_IDENTITY_INVALID", f"{sport}:{event_id}")
        existing = _one(
            db.table(SOURCE_TABLE)
            .select("source_event_id")
            .eq("sport", sport)
            .eq("official_event_id", event_id)
            .eq("source_payload_sha256", payload_hash)
            .limit(1)
            .execute()
        )
        if existing:
            existing_n += 1
            continue
        outcome = {
            "home_score": raw.get("home_score"),
            "away_score": raw.get("away_score"),
            "positive_outcome": raw.get("positive_outcome"),
        }
        row = {
            "sport": sport,
            "league": str(raw.get("league") or sport).strip().upper(),
            "official_event_id": event_id,
            "season": None if raw.get("season") is None else str(raw.get("season")),
            "event_start_time": raw.get("event_start_time"),
            "home_participant": raw.get("home_team") or raw.get("home_participant"),
            "away_participant": raw.get("away_team") or raw.get("away_participant"),
            "outcome_json": outcome,
            "source_provider": str(raw.get("source_provider") or "").strip(),
            "source_uri": raw.get("source_uri"),
            "source_retrieved_at": raw.get("source_retrieved_at"),
            "source_payload_sha256": payload_hash,
            "historical_reconstruction": bool(raw.get("historical_reconstruction", True)),
            "can_execute": False,
        }
        db.table(SOURCE_TABLE).insert(row).execute()
        inserted_n += 1
    return {"inserted": inserted_n, "existing": existing_n}


def persist_training_rows(
    db: Any,
    rows: Sequence[Mapping[str, Any]],
    *,
    model_family: str,
) -> dict[str, int]:
    inserted_n = 0
    existing_n = 0
    for raw in rows:
        _validate_governance(raw, prefix="D1_TRAINING")
        if raw.get("market_features_used") is not False:
            raise D1RegistryError("D1_TRAINING_MARKET_FEATURES_FORBIDDEN", str(raw.get("official_event_id")))
        sport = str(raw.get("sport") or "").strip().upper()
        event_id = str(raw.get("official_event_id") or "").strip()
        schema = str(raw.get("feature_schema_version") or "").strip()
        manifest_hash = str(raw.get("source_manifest_sha256") or "").strip().lower()
        if not sport or not event_id or not schema or len(manifest_hash) != 64:
            raise D1RegistryError("D1_TRAINING_IDENTITY_INVALID", f"{sport}:{event_id}:{schema}")
        existing = _one(
            db.table(TRAINING_TABLE)
            .select("training_row_id")
            .eq("sport", sport)
            .eq("official_event_id", event_id)
            .eq("feature_schema_version", schema)
            .eq("source_manifest_sha256", manifest_hash)
            .limit(1)
            .execute()
        )
        if existing:
            existing_n += 1
            continue
        outcome = raw.get("outcome_json")
        if not isinstance(outcome, Mapping):
            outcome = {"positive_outcome": raw.get("positive_outcome")}
        row = {
            "sport": sport,
            "league": str(raw.get("league") or sport).strip().upper(),
            "official_event_id": event_id,
            "event_start_time": raw.get("event_start_time"),
            "feature_as_of": raw.get("feature_as_of"),
            "feature_schema_version": schema,
            "model_family": str(model_family),
            "features": dict(raw.get("features") or {}),
            "outcome_json": dict(outcome),
            "source_manifest": dict(raw.get("source_manifest") or {}),
            "source_manifest_sha256": manifest_hash,
            "historical_reconstruction": bool(raw.get("historical_reconstruction", True)),
            "archived_pregame_snapshot": bool(raw.get("archived_pregame_snapshot", False)),
            "market_features_used": False,
            "can_execute": False,
        }
        db.table(TRAINING_TABLE).insert(row).execute()
        inserted_n += 1
    return {"inserted": inserted_n, "existing": existing_n}


def persist_candidate(db: Any, raw: Mapping[str, Any]) -> dict[str, Any]:
    _validate_governance(raw, prefix="D1_CANDIDATE")
    if raw.get("lifecycle_state") != "CANDIDATE":
        raise D1RegistryError("D1_CANDIDATE_LIFECYCLE_INVALID", str(raw.get("lifecycle_state")))
    if any(bool(raw.get(name)) for name in ("promoted", "active", "automatic_certification", "automatic_promotion")):
        raise D1RegistryError("D1_CANDIDATE_GOVERNANCE_FLAGS_INVALID", "candidate must remain inert")
    version = str(raw.get("model_artifact_version") or "").strip()
    checksum = str(raw.get("artifact_checksum") or "").strip().lower()
    if not version or len(checksum) != 64:
        raise D1RegistryError("D1_CANDIDATE_IDENTITY_INVALID", version)
    existing = _one(
        db.table(CANDIDATE_TABLE)
        .select("candidate_id,artifact_checksum,lifecycle_state")
        .eq("model_artifact_version", version)
        .limit(1)
        .execute()
    )
    if existing:
        if str(existing.get("artifact_checksum") or "") != checksum:
            raise D1RegistryError("D1_CANDIDATE_VERSION_COLLISION", version)
        return {
            "status": "ALREADY_REGISTERED_IDENTICAL",
            "candidate_id": existing.get("candidate_id"),
            "model_artifact_version": version,
            "lifecycle_state": existing.get("lifecycle_state"),
        }
    row = {
        "sport": str(raw.get("sport") or "").strip().upper(),
        "league": str(raw.get("league") or raw.get("sport") or "").strip().upper(),
        "market_family": str(raw.get("market_family") or "OUTRIGHT_WINNER").strip().upper(),
        "model_family": str(raw.get("model_family") or "").strip(),
        "model_artifact_version": version,
        "feature_schema_version": str(raw.get("feature_schema_version") or "").strip(),
        "source_policy_id": str(raw.get("source_policy_id") or "").strip(),
        "training_dataset_hash": str(raw.get("training_dataset_hash") or "").strip().lower(),
        "training_code_sha": str(raw.get("training_code_sha") or "").strip().lower(),
        "artifact_checksum": checksum,
        "artifact_payload": dict(raw.get("artifact_payload") or {}),
        "calibrator_payload": dict(raw.get("calibrator_payload") or {}),
        "validation_metrics": dict(raw.get("validation_metrics") or {}),
        "training_rows": int(raw.get("training_rows") or 0),
        "calibration_rows": int(raw.get("calibration_rows") or 0),
        "test_rows": int(raw.get("test_rows") or 0),
        "research_screen_pass": bool(raw.get("research_screen_pass", False)),
        "source_review_status": str(raw.get("source_review_status") or "REQUIRED").strip().upper(),
        "lifecycle_state": "CANDIDATE",
        "promoted": False,
        "active": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }
    inserted = db.table(CANDIDATE_TABLE).insert(row).execute()
    persisted = _one(inserted) or {}
    return {
        "status": "CANDIDATE_REGISTERED",
        "candidate_id": persisted.get("candidate_id"),
        "model_artifact_version": version,
        "lifecycle_state": "CANDIDATE",
    }


def persist_candidate_package(db: Any, package: Mapping[str, Any]) -> dict[str, Any]:
    if package.get("can_execute") is not False or package.get("probability_publishable") is not False:
        raise D1RegistryError("D1_PACKAGE_GOVERNANCE_INVALID", "package must be inert")
    candidate = dict(package.get("candidate") or {})
    model_family = str(candidate.get("model_family") or "").strip()
    if not model_family:
        raise D1RegistryError("D1_PACKAGE_MODEL_FAMILY_MISSING", "candidate model_family required")
    source_result = persist_source_events(db, list(package.get("games") or []))
    training_result = persist_training_rows(db, list(package.get("feature_rows") or []), model_family=model_family)
    candidate_result = persist_candidate(db, candidate)
    return {
        "status": "D1_CANDIDATE_PACKAGE_PERSISTED",
        "source_events": source_result,
        "training_rows": training_result,
        "candidate": candidate_result,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "CANDIDATE_TABLE",
    "D1RegistryError",
    "SOURCE_TABLE",
    "TRAINING_TABLE",
    "persist_candidate",
    "persist_candidate_package",
    "persist_source_events",
    "persist_training_rows",
]
