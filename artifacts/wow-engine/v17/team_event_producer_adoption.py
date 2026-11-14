"""Read-only production-producer adoption diagnostics for V17 team/event models.

This module reports genuine evidence components already carried by the controlling
scorer. It cannot create authenticated source snapshots, missing participant data,
a fitted numerical-vector digest, an attestation, or a publication decision.
Never source component evidence from the caller's request or research prose.
"""
from __future__ import annotations

from hashlib import sha256
from json import dumps
from typing import Any, Mapping
import re

from v17.cross_sport_evidence_integrity import (
    CRITICAL_STATUS_CONTRACT,
    TEAM_ROSTER_SPORTS,
    evaluate_scored_cross_sport_evidence,
)

TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
CAN_EXECUTE = False
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def inspect_team_event_producer_adoption(
    *, sport: str, result: Mapping[str, Any], consumption_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Record the first producer proof gap; never self-certify from source claims.

    These are diagnostics only. Only a registered backend attestation verifier,
    the cross-sport scorer gate, and the V17 terminal reducer can authorize a
    publication result. Presence of a field does not establish source authenticity.
    """
    normalized = _text(sport).upper()
    if normalized not in CRITICAL_STATUS_CONTRACT:
        raise ValueError("SPORT_CONTRACT_UNRESOLVED")

    canonical = result.get("canonical_acquisition")
    canonical = canonical if isinstance(canonical, Mapping) else {}
    governance = result.get("llp_governance")
    governance = governance if isinstance(governance, Mapping) else {}
    refresh = governance.get("final_refresh")
    refresh = refresh if isinstance(refresh, Mapping) else {}
    stage = result.get("nfl_stage_receipt")
    stage = stage if isinstance(stage, Mapping) else {}

    # Scorer result fields and old sport-specific ledgers are useful
    # observations, never a substitute for an admitted producer envelope.
    receipt_id = _text(consumption_receipt.get("feature_consumption_receipt_id"))
    numerical_receipt_complete = (
        consumption_receipt.get("receipt_complete") is True
        and consumption_receipt.get("numerical_consumption_proven") is True
        and consumption_receipt.get("critical_consumption_complete") is True
        and consumption_receipt.get("consumption_verification_status") == "VERIFIED"
    )

    evidence = result.get("cross_sport_evidence_audit")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    envelope = result.get("cross_sport_evidence")
    envelope = envelope if isinstance(envelope, Mapping) else {}
    # Recalculate the governed admission contract rather than trusting stale
    # serialized PASS text or a modified envelope after the bridge scored it.
    # This remains diagnostic-only and cannot authenticate upstream sources.
    current_audit = evaluate_scored_cross_sport_evidence(result)
    audit_identity_consistent = (
        current_audit.get("evidence_status") == "PASS"
        and bool(_text(evidence.get("audit_id")))
        and _text(evidence.get("audit_id")) == _text(current_audit.get("audit_id"))
    )

    # These fields are written by the trusted registered bridge after its
    # verifier and deterministic cross-sport audit. The read-only diagnostic
    # cannot grant model certification or terminal publication on their behalf.
    # Check immutable binding a second time to avoid producing a misleading
    # positive if a downstream serializer drops/replaces a receipt.
    binding_fields = ("official_event_id", "prediction_id")
    identity_bound = all(
        _text(result.get(key))
        and _text(result.get(key)) == _text(envelope.get(key))
        for key in binding_fields
    )
    scorer_artifact = _text(
        result.get("model_artifact_version") or result.get("model_version")
    )
    authenticated_enrollment = (
        result.get("cross_sport_evidence_required") is True
        and result.get("cross_sport_evidence_attested") is True
        and evidence.get("evidence_status") == "PASS"
        and audit_identity_consistent
        and _text(evidence.get("sport")) == normalized
        and _text(envelope.get("sport")).upper() == normalized
        and identity_bound
        and bool(receipt_id)
        and receipt_id == _text(envelope.get("feature_consumption_receipt_id"))
        and scorer_artifact == _text(envelope.get("model_artifact_version"))
        and bool(scorer_artifact)
        and numerical_receipt_complete
    )

    source_id = _text(canonical.get("source_snapshot_id")) if normalized == "NFL" else ""
    if normalized == "MLB":
        source_id = _text(result.get("source_snapshot_id"))
    if authenticated_enrollment:
        # The authenticated envelope is the cross-sport source of truth. Raw
        # caller-supplied source names and unauthenticated envelopes are ignored.
        source_id = _text(envelope.get("official_event_snapshot_id")) or source_id
    source_reference_present = bool(source_id)

    scorer_feature_hash = _text(result.get("feature_row_hash"))
    fitted_hash_seen = bool(_SHA256.fullmatch(scorer_feature_hash))
    frozen_vector_hash = _text(result.get("feature_snapshot_hash"))
    scorer_consumed_hash = _text(result.get("model_consumed_feature_hash"))
    nfl_scorer_vector_proven = (
        normalized == "NFL"
        and bool(_SHA256.fullmatch(frozen_vector_hash))
        and frozen_vector_hash.lower() == scorer_consumed_hash.lower()
        and _text(result.get("feature_vector_hash_policy"))
        == "NFL_CANONICAL_NUMERIC_VECTOR_SCHEMA_ORDER_V1"
    )
    envelope_frozen_hash = _text(envelope.get("feature_snapshot_hash"))
    envelope_consumed_hash = _text(envelope.get("model_consumed_feature_hash"))
    authenticated_vector_proven = (
        authenticated_enrollment
        and bool(_SHA256.fullmatch(envelope_frozen_hash))
        and envelope_frozen_hash.lower() == envelope_consumed_hash.lower()
    )
    scorer_vector_proven = nfl_scorer_vector_proven or authenticated_vector_proven

    nfl_schedule_refresh_present = (
        normalized == "NFL"
        and refresh.get("final_refresh_status") == "PASS"
        and bool(_text(refresh.get("checked_at")))
        and bool(_text(refresh.get("refresh_source_snapshot_id")))
    )
    admitted_final_refresh = (
        authenticated_enrollment
        and envelope.get("final_refresh_status") == "PASS"
        and bool(_text(envelope.get("final_refresh_at")))
    )
    nfl_stage_pass = normalized == "NFL" and stage.get("status") == "PASS"
    model_at_present = bool(_text(
        result.get("immutable_model_timestamp") or result.get("model_timestamp")
    ))

    components = {
        "backend_score_source_reference": (
            "ATTESTED_SOURCE_SNAPSHOT_REFERENCE"
            if authenticated_enrollment and source_reference_present
            else "PRESENT_NOT_INDEPENDENTLY_AUTHENTICATED"
            if source_reference_present else "MISSING"
        ),
        "fitted_model_timestamp": (
            "PRESENT_BOUND_TO_ATTESTED_ENVELOPE"
            if authenticated_enrollment and model_at_present
            else "PRESENT_NOT_BOUND" if model_at_present else "MISSING"
        ),
        "fitted_numerical_feature_consumption": (
            "RECEIPT_COMPLETE" if numerical_receipt_complete else "NOT_PROVEN"
        ),
        "fitted_scorer_row_hash": (
            "HASH_PRESENT_NOT_VECTOR_EQUIVALENCE" if fitted_hash_seen
            else "NOT_PROVEN" if normalized == "NFL" else "NOT_APPLICABLE"
        ),
        "scorer_numeric_vector_equivalence": (
            "ATTESTED_FROZEN_VECTOR_MATCH" if authenticated_vector_proven
            else "SCORER_VECTOR_MATCH_NOT_SOURCE_AUTHENTICATION"
            if nfl_scorer_vector_proven else "NOT_PROVEN"
        ),
        "nfl_schedule_final_refresh": (
            "SCHEDULE_ONLY_PASS" if nfl_schedule_refresh_present
            else "ATTESTED_FINAL_REFRESH" if normalized == "NFL" and admitted_final_refresh
            else "NOT_PROVEN" if normalized == "NFL" else "NOT_APPLICABLE"
        ),
        "nfl_durable_stage_receipt": (
            "PASS" if nfl_stage_pass else
            "NOT_PROVEN" if normalized == "NFL" else "NOT_APPLICABLE"
        ),
        "sport_specific_critical_status": CRITICAL_STATUS_CONTRACT[normalized],
    }

    blockers = []
    if not source_reference_present:
        blockers.append("BACKEND_SOURCE_REFERENCE_NOT_RETURNED")
    if not model_at_present:
        blockers.append("FITTED_INFERENCE_TIME_NOT_RETURNED")
    if not numerical_receipt_complete:
        blockers.append("NUMERICAL_CONSUMPTION_RECEIPT_NOT_COMPLETE")
    # The old row-input digest is NFL-specific. It is not an additional
    # requirement for an authenticated MLB, NBA, NHL, soccer, or other sport
    # producer whose governed envelope already proves vector equivalence.
    if normalized == "NFL" and not fitted_hash_seen and not authenticated_enrollment:
        blockers.append("FITTED_FEATURE_ROW_DIGEST_NOT_RETURNED")
    if not scorer_vector_proven:
        blockers.append("SCORER_FROZEN_VECTOR_EQUIVALENCE_NOT_PROVEN")
    if normalized == "NFL" and not (nfl_schedule_refresh_present or admitted_final_refresh):
        blockers.append("NFL_FINAL_SCHEDULE_REFRESH_NOT_PROVEN")
    if normalized == "NFL" and not nfl_stage_pass:
        blockers.append("NFL_DURABLE_STAGE_RECEIPT_NOT_PROVEN")

    # These are required *regardless of the number of references above*, until
    # the terminal admissible package is verified by the actual score bridge.
    if not authenticated_enrollment:
        blockers.extend([
            "AUTHENTICATED_SOURCE_AND_PARTICIPANT_SNAPSHOTS_NOT_PROVEN",
            "PRODUCER_ATTESTATION_NOT_ENROLLED_OR_NOT_PASS",
        ])
        if normalized in TEAM_ROSTER_SPORTS:
            blockers.append("EVENT_EFFECTIVE_PERSONNEL_STATUS_NOT_PROVEN")
        else:
            blockers.append("EVENT_EFFECTIVE_PARTICIPANT_STATUS_NOT_PROVEN")

    blockers = list(dict.fromkeys(blockers))
    output: dict[str, Any] = {
        "sport": normalized,
        "status": "PRODUCER_ADMISSION_PASS" if authenticated_enrollment and not blockers else "PRODUCER_PROOF_INCOMPLETE",
        "evidence_integrity_status": evidence.get("evidence_status") or "NOT_ENFORCED",
        "components": components,
        "source_snapshot_id": source_id or None,
        "fitted_scorer_feature_row_hash": scorer_feature_hash if fitted_hash_seen else None,
        "scorer_numeric_vector_hash": (
            envelope_consumed_hash if authenticated_vector_proven
            else scorer_consumed_hash if scorer_vector_proven else None
        ),
        "consumption_receipt_id": _text(consumption_receipt.get("feature_consumption_receipt_id")) or None,
        "blockers": blockers,
        "publication_authority": False,
        "terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": False,
    }
    output["diagnostic_receipt_id"] = sha256(
        dumps(output, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return output


__all__ = ["CAN_EXECUTE", "inspect_team_event_producer_adoption"]
