"""Batch persistence for large D1 candidate evidence packages.

Designed for multi-season candidate builds such as NHL where thousands of source
and feature rows must be written without thousands of round-trips. Conflicts are
ignored only on the immutable unique identities; candidate artifact version
collisions remain governed by d1_candidate_registry.persist_candidate.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from v17.d1_candidate_registry import (
    CANDIDATE_TABLE,
    D1RegistryError,
    SOURCE_TABLE,
    TRAINING_TABLE,
    persist_candidate,
)

CAN_EXECUTE = False
DEFAULT_BATCH_SIZE = 250


def _chunks(rows: list[dict[str, Any]], size: int = DEFAULT_BATCH_SIZE) -> Iterable[list[dict[str, Any]]]:
    for offset in range(0, len(rows), size):
        yield rows[offset:offset + size]


def _source_row(raw: Mapping[str, Any]) -> dict[str, Any]:
    if raw.get("can_execute") is not False:
        raise D1RegistryError("D1_SOURCE_CAN_EXECUTE_FORBIDDEN", str(raw.get("official_event_id")))
    payload_hash = str(raw.get("source_payload_sha256") or "").strip().lower()
    event_id = str(raw.get("official_event_id") or "").strip()
    sport = str(raw.get("sport") or "").strip().upper()
    if not event_id or not sport or len(payload_hash) != 64:
        raise D1RegistryError("D1_SOURCE_IDENTITY_INVALID", f"{sport}:{event_id}")
    return {
        "sport": sport,
        "league": str(raw.get("league") or sport).strip().upper(),
        "official_event_id": event_id,
        "season": None if raw.get("season") is None else str(raw.get("season")),
        "event_start_time": raw.get("event_start_time"),
        "home_participant": raw.get("home_team") or raw.get("home_participant"),
        "away_participant": raw.get("away_team") or raw.get("away_participant"),
        "outcome_json": {
            "home_score": raw.get("home_score"),
            "away_score": raw.get("away_score"),
            "positive_outcome": raw.get("positive_outcome"),
        },
        "source_provider": str(raw.get("source_provider") or "").strip(),
        "source_uri": raw.get("source_uri"),
        "source_retrieved_at": raw.get("source_retrieved_at"),
        "source_payload_sha256": payload_hash,
        "historical_reconstruction": bool(raw.get("historical_reconstruction", True)),
        "can_execute": False,
    }


def _training_row(raw: Mapping[str, Any], *, model_family: str) -> dict[str, Any]:
    if raw.get("can_execute") is not False:
        raise D1RegistryError("D1_TRAINING_CAN_EXECUTE_FORBIDDEN", str(raw.get("official_event_id")))
    if raw.get("market_features_used") is not False:
        raise D1RegistryError("D1_TRAINING_MARKET_FEATURES_FORBIDDEN", str(raw.get("official_event_id")))
    manifest_hash = str(raw.get("source_manifest_sha256") or "").strip().lower()
    event_id = str(raw.get("official_event_id") or "").strip()
    sport = str(raw.get("sport") or "").strip().upper()
    schema = str(raw.get("feature_schema_version") or "").strip()
    outcome = raw.get("positive_outcome")
    if not event_id or not sport or not schema or len(manifest_hash) != 64 or not isinstance(outcome, bool):
        raise D1RegistryError("D1_TRAINING_IDENTITY_OR_OUTCOME_INVALID", f"{sport}:{event_id}:{schema}")
    return {
        "sport": sport,
        "league": str(raw.get("league") or sport).strip().upper(),
        "official_event_id": event_id,
        "event_start_time": raw.get("event_start_time"),
        "feature_as_of": raw.get("feature_as_of"),
        "feature_schema_version": schema,
        "model_family": str(model_family),
        "features": dict(raw.get("features") or {}),
        "outcome_json": {"positive_outcome": outcome},
        "source_manifest": dict(raw.get("source_manifest") or {}),
        "source_manifest_sha256": manifest_hash,
        "historical_reconstruction": bool(raw.get("historical_reconstruction", True)),
        "archived_pregame_snapshot": bool(raw.get("archived_pregame_snapshot", False)),
        "market_features_used": False,
        "can_execute": False,
    }


def _upsert_batches(db: Any, table: str, rows: list[dict[str, Any]], *, on_conflict: str) -> int:
    if not rows:
        return 0
    written = 0
    for batch in _chunks(rows):
        result = db.table(table).upsert(
            batch,
            on_conflict=on_conflict,
            ignore_duplicates=True,
        ).execute()
        data = getattr(result, "data", None)
        written += len(data) if isinstance(data, list) else len(batch)
    return written


def persist_candidate_package_bulk(db: Any, package: Mapping[str, Any]) -> dict[str, Any]:
    if package.get("can_execute") is not False or package.get("probability_publishable") is not False:
        raise D1RegistryError("D1_PACKAGE_GOVERNANCE_INVALID", "package must be inert")
    candidate = dict(package.get("candidate") or {})
    model_family = str(candidate.get("model_family") or "").strip()
    if not model_family:
        raise D1RegistryError("D1_PACKAGE_MODEL_FAMILY_MISSING", "candidate model_family required")

    sources = [_source_row(row) for row in list(package.get("games") or [])]
    training = [_training_row(row, model_family=model_family) for row in list(package.get("feature_rows") or [])]
    source_written = _upsert_batches(
        db,
        SOURCE_TABLE,
        sources,
        on_conflict="sport,official_event_id,source_payload_sha256",
    )
    training_written = _upsert_batches(
        db,
        TRAINING_TABLE,
        training,
        on_conflict="sport,official_event_id,feature_schema_version,source_manifest_sha256",
    )
    candidate_result = persist_candidate(db, candidate)
    return {
        "status": "D1_CANDIDATE_PACKAGE_PERSISTED",
        "source_events": {"attempted": len(sources), "written_or_existing": source_written},
        "training_rows": {"attempted": len(training), "written_or_existing": training_written},
        "candidate": candidate_result,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "DEFAULT_BATCH_SIZE",
    "persist_candidate_package_bulk",
]
