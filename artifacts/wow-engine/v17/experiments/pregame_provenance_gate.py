"""WOW V17 research-only provenance audit for historical training and pregame sources.

A retrospective feature_as_of value or self-declared archived flag never proves
an actual pregame capture. This module inventories that missing evidence.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isfinite
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
AUTOMATIC_PROMOTION_ALLOWED = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"


class ProvenanceError(ValueError):
    pass


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ProvenanceError("REQUIRED_TIMESTAMP_MISSING")
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProvenanceError("TIMESTAMP_INVALID") from exc
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ProvenanceError("TIMESTAMP_NOT_TIMEZONE_AWARE")
    return ts.astimezone(timezone.utc)


def _event_id(value: Any, sport: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProvenanceError("OFFICIAL_EVENT_ID_MISSING")
    prefix = sport + ":"
    return value[len(prefix):] if value.startswith(prefix) and value[len(prefix):] else value


def _feature_digest(features: Any) -> str:
    if not isinstance(features, dict) or not features:
        raise ProvenanceError("FEATURES_MISSING")
    for key, value in features.items():
        if not isinstance(key, str) or not key or isinstance(value, bool):
            raise ProvenanceError("FEATURE_SCHEMA_INVALID")
        if not isinstance(value, (int, float)) or not isfinite(value):
            raise ProvenanceError("FEATURE_NON_FINITE_OR_NONNUMERIC")
    return sha256(json.dumps(features, sort_keys=True, allow_nan=False,
                             separators=(",", ":")).encode()).hexdigest()


def audit_d1_provenance(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Audit D1 export with one event-family identity and exact source chronology.

    A historical reconstruction, even with features_as_of=kickoff-1s, is not
    an archived pregame feature capture. No record self-certifies provenance.
    """
    if not isinstance(records, list):
        raise ProvenanceError("SOURCE_ROWS_NOT_LIST")
    reasons: Counter[str] = Counter()
    groups: dict[tuple[str, str, str, str, str], list[tuple[str, bool]]] = defaultdict(list)
    identities: dict[tuple[str, str], str] = {}
    seen_ids: set[str] = set()
    for row in records:
        if not isinstance(row, dict):
            reasons["ROW_NOT_OBJECT"] += 1
            continue
        try:
            sport = row.get("sport")
            if sport not in {"MLB", "NFL", "NBA", "NHL", "WNBA", "NCAAF", "NCAAB"}:
                raise ProvenanceError("SPORT_UNSUPPORTED")
            family, schema = row.get("model_family"), row.get("feature_schema_version")
            if not isinstance(family, str) or not family or not isinstance(schema, str) or not schema:
                raise ProvenanceError("MODEL_SCHEMA_IDENTITY_MISSING")
            event_id = _event_id(row.get("official_event_id"), sport)
            start = _timestamp(row.get("event_start_time"))
            asof = _timestamp(row.get("feature_as_of"))
            if not asof < start:
                raise ProvenanceError("FEATURE_ASOF_NOT_PREGAME")
            row_id = row.get("training_row_id")
            if not isinstance(row_id, str) or not row_id:
                raise ProvenanceError("TRAINING_ROW_ID_MISSING")
            if row_id in seen_ids:
                raise ProvenanceError("DUPLICATE_TRAINING_ROW_ID")
            seen_ids.add(row_id)
            label = row.get("outcome_json")
            if not isinstance(label, dict) or not isinstance(label.get("home_win"), bool):
                raise ProvenanceError("BINARY_OUTCOME_MISSING")
            digest = _feature_digest(row.get("features"))
            lineage = row.get("source_manifest_sha256")
            if (not isinstance(lineage, str) or len(lineage) != 64 or
                any(c not in "0123456789abcdef" for c in lineage.lower())):
                raise ProvenanceError("SOURCE_MANIFEST_HASH_MISSING_OR_INVALID")
            event_key = (sport, event_id)
            game_start = start.isoformat()
            if event_key in identities and identities[event_key] != game_start:
                raise ProvenanceError("CONFLICTING_EVENT_START")
            identities[event_key] = game_start
            key = (sport, family, schema, event_id, game_start)
            archived = (row.get("archived_pregame_snapshot") is True and
                        row.get("historical_reconstruction") is False)
            groups[key].append((digest + ":" + str(label["home_win"]), archived))
            if row.get("historical_reconstruction") is True:
                reasons["HISTORICAL_RECONSTRUCTION_NOT_ARCHIVED"] += 1
            elif row.get("archived_pregame_snapshot") is not True:
                reasons["ARCHIVED_PREGAME_SNAPSHOT_ABSENT"] += 1
            else:
                reasons["FEATURE_CAPTURE_PROVENANCE_NOT_INDEPENDENTLY_CERTIFIED"] += 1
        except ProvenanceError as exc:
            reasons[str(exc)] += 1
    ambiguous = sum(1 for group in groups.values() if len({x[0] for x in group}) > 1)
    repeats = sum(len(group) - 1 for group in groups.values())
    archived_groups = sum(1 for group in groups.values() if any(x[1] for x in group))
    validated = sum(map(len, groups.values()))
    return {
        "status": "RESEARCH_INVENTORY_ONLY_NO_CERTIFIED_FEATURES",
        "source_rows": len(records), "validated_rows": validated,
        "rejected_rows": len(records) - validated,
        "unique_model_event_groups": len(groups),
        "repeated_model_event_rows": repeats,
        "ambiguous_feature_or_outcome_groups": ambiguous,
        "self_declared_archived_groups": archived_groups,
        "independently_certified_feature_groups": 0,
        "reasons": dict(sorted(reasons.items())),
        "can_execute": CAN_EXECUTE, "automatic_promotion_allowed": AUTOMATIC_PROMOTION_ALLOWED,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


def audit_snapshot_alignment(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Check model versus source snapshot times; never infer matching features."""
    if not isinstance(records, list):
        raise ProvenanceError("SOURCE_ROWS_NOT_LIST")
    reasons: Counter[str] = Counter()
    events: set[tuple[str, str]] = set()
    for row in records:
        try:
            if not isinstance(row, dict):
                raise ProvenanceError("ROW_NOT_OBJECT")
            event_id = _event_id(row.get("official_event_id"), "MLB")
            start = _timestamp(row.get("event_start_time"))
            model = _timestamp(row.get("model_timestamp"))
            capture = _timestamp(row.get("source_captured_at"))
            if model >= start:
                raise ProvenanceError("MODEL_NOT_PREGAME")
            if capture >= start:
                reason = "SOURCE_CAPTURE_NOT_PREGAME"
            elif capture > model:
                reason = "SOURCE_CAPTURE_AFTER_MODEL"
            else:
                reason = "SOURCE_CAPTURE_BEFORE_MODEL_UNVERIFIED_FEATURE_MATCH"
            reasons[reason] += 1
            events.add((event_id, start.isoformat()))
        except ProvenanceError as exc:
            reasons[str(exc)] += 1
    return {
        "status": "SOURCE_ALIGNMENT_RESEARCH_ONLY", "source_rows": len(records),
        "unique_events": len(events), "reason_counts": dict(sorted(reasons.items())),
        "certified_feature_snapshots": 0,
        "can_execute": CAN_EXECUTE, "automatic_promotion_allowed": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }
