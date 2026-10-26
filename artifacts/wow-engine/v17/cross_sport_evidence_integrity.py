"""V17 cross-sport evidence-integrity preflight (no probability authority).

This is the shared, sport-neutral admission contract for authenticated team/event
model receipts. It validates frozen participant/context evidence, model feature
consumption, module completion, and event-time ordering. Sport-specific fitted
feature construction is intentionally left to the controlling specialist.

The returned decision can HOLD official publication, but must never erase or
alter an already-completed sporting probability. This module cannot authorize
execution, certification, scorer registration, or an odds-derived forecast.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from json import dumps
from typing import Any, Mapping
import re

from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS

CAN_EXECUTE = False
POLICY_VERSION = "V17_CROSS_SPORT_EVIDENCE_INTEGRITY_V1"
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
TEAM_ROSTER_SPORTS = frozenset(
    {"MLB", "NFL", "NBA", "WNBA", "NCAAF", "NCAAB", "NHL", "SOCCER", "CRICKET"}
)
# Each sport must supply its *own* verified critical-status producer. These
# names are evidence-policy identifiers, not a universal sports probability model.
CRITICAL_STATUS_CONTRACT = {
    "MLB": "STARTERS_LINEUPS",
    "NFL": "QB_MATERIAL_AVAILABILITY",
    "NBA": "ROTATION_INJURY",
    "WNBA": "ROTATION_INJURY",
    "NCAAF": "QB_MATERIAL_AVAILABILITY",
    "NCAAB": "STARTERS_INJURY",
    "NHL": "GOALIE_SCRATCHES",
    "SOCCER": "STARTING_XI_AVAILABILITY",
    "TENNIS": "HEALTH_RETIREMENT",
    "PGA": "FIELD_WITHDRAWAL",
    "MMA": "WEIGH_IN_BOUT",
    "BOXING": "WEIGH_IN_BOUT",
    "CRICKET": "PLAYING_XI_AVAILABILITY",
}
HASH_PATTERN = re.compile(r"^[a-fA-F0-9]{64}$")


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _id_list(value: Any) -> list[str] | None:
    if not isinstance(value, (list, tuple)) or any(not _text(x) for x in value):
        return None
    ids = [_text(x) for x in value]
    return ids if len(ids) == len(set(ids)) else None


def audit_cross_sport_evidence_integrity(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """Check one *backend-produced* snapshot/score envelope without inventing proof.

    This routine validates receipt consistency; it cannot verify a producer's
    signature or independently authenticate the upstream source. Only the
    trusted scoring backend may assemble the envelope admitted to publication.
    Missing proof is a HOLD, never synthetic model unavailability.
    """
    blockers: list[str] = []
    sport = _text(envelope.get("sport")).upper()
    event_id = _text(envelope.get("official_event_id"))
    if sport not in EXPECTED_TEAM_EVENT_SPORTS:
        blockers.append("SPORT_CONTRACT_UNRESOLVED")
    if not event_id:
        blockers.append("OFFICIAL_EVENT_ID_UNRESOLVED")
    if not _text(envelope.get("official_event_snapshot_id")):
        blockers.append("OFFICIAL_EVENT_SNAPSHOT_MISSING")
    if not _text(envelope.get("prediction_id")):
        blockers.append("IMMUTABLE_PREDICTION_ID_MISSING")
    if _text(envelope.get("scorer_official_event_id")) != event_id:
        blockers.append("SCORER_EVENT_BINDING_UNPROVEN")

    started_at = _timestamp(envelope.get("event_start_utc"))
    model_at = _timestamp(envelope.get("model_timestamp"))
    if started_at is None or model_at is None:
        blockers.append("PREGAME_TIMESTAMP_PROOF_MISSING")
    elif model_at >= started_at:
        blockers.append("NOT_A_PREGAME_MODEL_SNAPSHOT")
    event_update_at = _timestamp(envelope.get("official_event_updated_at"))
    if event_update_at is None:
        blockers.append("OFFICIAL_EVENT_FRESHNESS_UNVERIFIED")
    elif model_at is not None and event_update_at > model_at:
        blockers.append("OFFICIAL_EVENT_CHANGED_AFTER_MODEL")
    if _text(envelope.get("final_refresh_status")).upper() != "PASS":
        blockers.append("FINAL_REFRESH_NOT_PROVEN")
    if _text(envelope.get("current_event_status")).upper() not in {"PREGAME", "SCHEDULED"}:
        blockers.append("CURRENT_EVENT_NOT_PREGAME")
    refresh_at = _timestamp(envelope.get("final_refresh_at"))
    newest_material_at = _timestamp(envelope.get("current_material_update_at"))
    if refresh_at is None or model_at is None or refresh_at < model_at:
        blockers.append("FINAL_REFRESH_TIME_INVALID")
    if refresh_at is not None and started_at is not None and refresh_at >= started_at:
        blockers.append("FINAL_REFRESH_EVENT_ALREADY_STARTED")
    if newest_material_at is None:
        blockers.append("CURRENT_MATERIAL_UPDATE_WATERMARK_MISSING")
    elif model_at is not None and newest_material_at > model_at:
        blockers.append("FRESH_MATERIAL_UPDATE_REQUIRES_RESCORING")
    if refresh_at is not None and newest_material_at is not None and newest_material_at > refresh_at:
        blockers.append("FINAL_REFRESH_BEFORE_LATEST_UPDATE")

    official_participants = _id_list(envelope.get("official_participant_ids"))
    if official_participants is None or len(official_participants) < 2:
        blockers.append("OFFICIAL_PARTICIPANT_SET_UNRESOLVED")

    participants = envelope.get("participants")
    if not isinstance(participants, list) or len(participants) < 2:
        blockers.append("PARTICIPANT_SNAPSHOTS_INCOMPLETE")
        participants = []
    else:
        ids = [_text(p.get("participant_id")) if isinstance(p, Mapping) else "" for p in participants]
        if any(not item for item in ids) or len(set(ids)) != len(ids):
            blockers.append("PARTICIPANT_IDENTITY_CONFLICT")
        if sport != "PGA" and len(participants) != 2:
            blockers.append("TWO_SIDE_EVENT_IDENTITY_UNRESOLVED")
        if official_participants and set(ids) != set(official_participants):
            blockers.append("OFFICIAL_PARTICIPANT_SET_MISMATCH")

    for index, participant in enumerate(participants):
        if not isinstance(participant, Mapping):
            blockers.append(f"PARTICIPANT_{index}_EVIDENCE_INVALID")
            continue
        if not _text(participant.get("snapshot_id")):
            blockers.append(f"PARTICIPANT_{index}_SNAPSHOT_MISSING")
        if _text(participant.get("verification_status")).upper() != "VERIFIED":
            blockers.append(f"PARTICIPANT_{index}_VERIFICATION_INCOMPLETE")
        if not _text(participant.get("source_id")):
            blockers.append(f"PARTICIPANT_{index}_SOURCE_MISSING")
        if _text(participant.get("critical_status_contract")) != CRITICAL_STATUS_CONTRACT.get(sport):
            blockers.append(f"PARTICIPANT_{index}_CRITICAL_STATUS_CONTRACT_MISMATCH")
        status = _text(participant.get("critical_status")).upper()
        if status == "MODELED_UNCERTAINTY_CERTIFIED":
            if not _text(participant.get("scenario_model_receipt_id")):
                blockers.append(f"PARTICIPANT_{index}_SCENARIO_MODEL_PROOF_MISSING")
        elif status != "PASS":
            blockers.append(f"PARTICIPANT_{index}_CRITICAL_STATUS_UNVERIFIED")
        if not _text(participant.get("critical_status_snapshot_id")):
            blockers.append(f"PARTICIPANT_{index}_CRITICAL_STATUS_SNAPSHOT_MISSING")
        if sport in TEAM_ROSTER_SPORTS:
            if _text(participant.get("roster_reconciliation_status")).upper() != "PASS":
                blockers.append(f"PARTICIPANT_{index}_ROSTER_NOT_RECONCILED")
            if not _text(participant.get("personnel_snapshot_id")):
                blockers.append(f"PARTICIPANT_{index}_PERSONNEL_SNAPSHOT_MISSING")
        for key in ("source_timestamp", "effective_at", "latest_material_update_at"):
            observed = _timestamp(participant.get(key))
            if observed is None:
                blockers.append(f"PARTICIPANT_{index}_{key.upper()}_UNVERIFIED")
            elif model_at is not None and observed > model_at:
                blockers.append(f"PARTICIPANT_{index}_{key.upper()}_AFTER_MODEL")

    frozen_hash = _text(envelope.get("feature_snapshot_hash")).lower()
    consumed_hash = _text(envelope.get("model_consumed_feature_hash")).lower()
    if not HASH_PATTERN.fullmatch(frozen_hash):
        blockers.append("FROZEN_FEATURE_SNAPSHOT_HASH_UNVERIFIED")
    if not HASH_PATTERN.fullmatch(consumed_hash):
        blockers.append("SCORER_CONSUMED_FEATURE_HASH_UNVERIFIED")
    elif HASH_PATTERN.fullmatch(frozen_hash) and consumed_hash != frozen_hash:
        blockers.append("MODEL_FEATURE_SNAPSHOT_MISMATCH")

    if _text(envelope.get("feature_consumption_verification_status")).upper() != "VERIFIED":
        blockers.append("NUMERICAL_FEATURE_CONSUMPTION_UNVERIFIED")
    if envelope.get("critical_feature_consumption_complete") is not True:
        blockers.append("CRITICAL_FEATURE_CONSUMPTION_INCOMPLETE")
    if not _text(envelope.get("model_artifact_version")):
        blockers.append("MODEL_ARTIFACT_IDENTITY_MISSING")
    if envelope.get("model_invoked") is not True:
        blockers.append("FITTED_MODEL_INVOCATION_UNPROVEN")

    required = _id_list(envelope.get("required_modules"))
    completed = _id_list(envelope.get("completed_modules"))
    if not required:
        blockers.append("REQUIRED_MODULE_MANIFEST_MISSING")
    if completed is None or required is None or not set(required).issubset(completed):
        blockers.append("REQUIRED_MODULE_EXECUTION_UNPROVEN")
    if envelope.get("stage_errors") != []:
        blockers.append("STAGE_EXECUTION_ERRORS_PRESENT_OR_UNVERIFIED")

    # Hash identifies audit content, not a signed producer/source attestation.
    blockers = list(dict.fromkeys(blockers))
    core = {
        "policy_version": POLICY_VERSION,
        "sport": sport,
        "official_event_id": event_id or None,
        "evidence_status": "PASS" if not blockers else "HELD",
        "blockers": blockers,
        "feature_snapshot_hash": frozen_hash or None,
        "model_consumed_feature_hash": consumed_hash or None,
        "terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": False,
    }
    core["audit_id"] = sha256(dumps(core, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return core


def evaluate_scored_cross_sport_evidence(result: Mapping[str, Any]) -> dict[str, Any]:
    """Use the same admission decision at scoring and official publication.

    Enrollment and attestation must be set by the trusted bridge rather than
    interpreted from source prose. An unadmitted envelope is not PASS.
    """
    required = result.get("cross_sport_evidence_required") is True
    envelope = result.get("cross_sport_evidence")
    if not required:
        return {
            "evidence_status": "NOT_ENFORCED",
            "blockers": (
                ["UNENROLLED_EVIDENCE_CANNOT_PROVE_PUBLICATION"]
                if envelope is not None else []
            ),
            "can_execute": False,
        }
    if result.get("cross_sport_evidence_attested") is not True:
        return {
            "evidence_status": "HELD",
            "blockers": ["TRUSTED_EVIDENCE_PRODUCER_ATTESTATION_MISSING"],
            "can_execute": False,
        }
    if not isinstance(envelope, Mapping):
        return {
            "evidence_status": "HELD",
            "blockers": ["REQUIRED_CROSS_SPORT_EVIDENCE_ENVELOPE_MISSING"],
            "can_execute": False,
        }
    audit = audit_cross_sport_evidence_integrity(envelope)
    bindings = {
        "sport": "sport",
        "official_event_id": "official_event_id",
        "prediction_id": "prediction_id",
        "feature_consumption_receipt_id": "feature_consumption_receipt_id",
    }
    mismatches = [
        key
        for key, evidence_key in bindings.items()
        if not _text(result.get(key))
        or _text(result.get(key)) != _text(envelope.get(evidence_key))
    ]
    # The scorer's fitted artifact identity cannot silently change between
    # numerical output and the evidence envelope.
    result_artifact = _text(
        result.get("model_artifact_version") or result.get("model_version")
    )
    if not result_artifact or result_artifact != _text(envelope.get("model_artifact_version")):
        mismatches.append("model_artifact_version")
    result_model_at = _timestamp(
        result.get("immutable_model_timestamp") or result.get("model_timestamp")
    )
    if result_model_at is None or result_model_at != _timestamp(envelope.get("model_timestamp")):
        mismatches.append("model_timestamp")
    if mismatches:
        audit = dict(audit)
        audit["evidence_status"] = "HELD"
        audit["blockers"] = list(dict.fromkeys(
            [*audit["blockers"], "SCORED_EVENT_EVIDENCE_IDENTITY_MISMATCH"]
        ))
        audit["binding_mismatches"] = mismatches
    return audit


__all__ = ["CAN_EXECUTE", "CRITICAL_STATUS_CONTRACT", "POLICY_VERSION", "TEAM_ROSTER_SPORTS", "audit_cross_sport_evidence_integrity", "evaluate_scored_cross_sport_evidence"]
