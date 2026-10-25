"""Versioned producer → full-board stage-proof contract.

The contract records only proof emitted by the authority that performed a
stage. A terminal PASS, calibration-health PASS, official-publication guard
PASS, or scheduled/pregame event state is not stage proof. Missing proof is
``EVIDENCE_NOT_EMITTED``. An explicit failed receipt is ``EVIDENCE_FAILED``.
Those two outcomes stay distinct, and neither one is upgraded to PASS.

NFL dynamic calibration and final refresh are not executed by the NFL team-event
producer. This module does not borrow MLB or Flask refresh receipts to fill
that gap.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

CONTRACT_VERSION = "wow.v17.stage_proof.v1"
NFL_PRODUCER = "v17.nfl_team_event_publication"
PACKAGE_AUTHORITY = "v17.team_event_request_runtime._build_governed_probability_package"
GOVERNANCE_AUTHORITY = "public.wow_v17_nfl_team_event_governance_bridge"
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"

EVIDENCE_EMITTED = "EVIDENCE_EMITTED"
EVIDENCE_NOT_EMITTED = "EVIDENCE_NOT_EMITTED"
EVIDENCE_FAILED = "EVIDENCE_FAILED"
EVIDENCE_STALE = "EVIDENCE_STALE"
EVIDENCE_MALFORMED = "EVIDENCE_MALFORMED"
EVIDENCE_CONFLICT = "EVIDENCE_CONFLICT"

STAGE_PACKAGE = "probability_package"
STAGE_DYNAMIC_CALIBRATION = "dynamic_calibration"
STAGE_PROBABILITY_AUDIT = "probability_audit"
STAGE_EVENT_GOVERNOR = "event_governor"
STAGE_FINAL_REFRESH = "final_refresh"

_CONTRACT_STAGES = (
    STAGE_PACKAGE,
    STAGE_DYNAMIC_CALIBRATION,
    STAGE_PROBABILITY_AUDIT,
    STAGE_EVENT_GOVERNOR,
    STAGE_FINAL_REFRESH,
)
_AUDIT_STAGE_BY_CONTRACT = {
    STAGE_PACKAGE: "probability_package_valid",
    STAGE_DYNAMIC_CALIBRATION: "dynamic_calibration_complete",
    STAGE_PROBABILITY_AUDIT: "probability_audit_passed",
    STAGE_EVENT_GOVERNOR: "event_governor_complete",
    STAGE_FINAL_REFRESH: "final_refresh_passed",
}
_ABSENT_BLOCKER = {
    "probability_package_valid": "PROBABILITY_PACKAGE_EVIDENCE_NOT_EMITTED",
    "dynamic_calibration_complete": "DYNAMIC_CALIBRATION_EVIDENCE_NOT_EMITTED",
    "probability_audit_passed": "PROBABILITY_AUDIT_EVIDENCE_NOT_EMITTED",
    "event_governor_complete": "EVENT_GOVERNOR_EVIDENCE_NOT_EMITTED",
    "numerical_bounds_present": "CALIBRATED_PROBABILITY_OR_LOWER_BOUND_MISSING",
    "final_refresh_passed": "FINAL_REFRESH_EVIDENCE_NOT_EMITTED",
    "terminal_authority_preserved": "TERMINAL_AUTHORITY_OR_EXECUTION_INVARIANT_NOT_PROVEN",
}
_FAILED_BLOCKER = {
    "probability_package_valid": "PROBABILITY_PACKAGE_NOT_VALID",
    "dynamic_calibration_complete": "DYNAMIC_CALIBRATION_NOT_COMPLETE",
    "probability_audit_passed": "PROBABILITY_AUDIT_NOT_COMPLETE",
    "event_governor_complete": "EVENT_GOVERNOR_NOT_COMPLETE",
    "numerical_bounds_present": "CALIBRATED_PROBABILITY_OR_LOWER_BOUND_MISSING",
    "final_refresh_passed": "FINAL_REFRESH_NOT_COMPLETE",
    "terminal_authority_preserved": "TERMINAL_AUTHORITY_OR_EXECUTION_INVARIANT_NOT_PROVEN",
}
_PASS_DISPOSITIONS = {"PASS", "COMPLETE"}
_PASS_AUDIT_RESULTS = {"PASS", "PASS_PROBABILITY_AUDIT"}
_PUBLICATION_CODES = {"GOVERNED_PROBABILITY_PUBLISHED", "FINAL_APPROVED"}
_IDENTITY_FIELDS = (
    "research_run_id",
    "event_key",
    "official_event_id",
    "score_snapshot_id",
)


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _parse_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _identity(source: Mapping[str, Any]) -> dict[str, Any]:
    return {field: source.get(field) for field in _IDENTITY_FIELDS}


def _receipt(
    *,
    stage: str,
    evidence_type: str,
    disposition: str,
    authority: str | None,
    reason: str | None,
    identity: Mapping[str, Any],
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    receipt = {
        "stage": stage,
        "evidence_type": evidence_type,
        "disposition": disposition,
        "authority": authority,
        "reason": reason,
        "bound_research_run_id": identity.get("research_run_id"),
        "bound_event_key": identity.get("event_key"),
        "bound_official_event_id": identity.get("official_event_id"),
        "bound_score_snapshot_id": identity.get("score_snapshot_id"),
        "can_execute": False,
    }
    if extra:
        receipt.update(dict(extra))
        receipt["can_execute"] = False
    return receipt


NFL_FINAL_REFRESH_AUTHORITY = "v17.nfl_team_event_publication._nfl_final_refresh"


def _nfl_producer_final_refresh_receipt(
    identity: Mapping[str, Any], source_snapshot_id: str | None,
    final_refresh: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Attest only the second, canonical NFL schedule read actually performed.

    A modeled event being PREGAME, SQL final-gates PASS, or calibration health
    PASS never supplies this proof. The refresh provider must have re-queried the
    exact modeled source and checked the unchanged schedule content.
    """
    if final_refresh is None:
        return _receipt(
            stage=STAGE_FINAL_REFRESH,
            evidence_type=EVIDENCE_NOT_EMITTED, disposition="UNPROVEN",
            authority=None, reason="NFL_FINAL_REFRESH_OPERATION_NOT_EXECUTED",
            identity=identity,
            extra={"scheduled_event_status_accepted_as_proof": False,
                   "borrowed_mlb_or_flask_receipt": False},
        )
    if not isinstance(final_refresh, Mapping):
        return _receipt(
            stage=STAGE_FINAL_REFRESH,
            evidence_type=EVIDENCE_MALFORMED, disposition="FAIL",
            authority=None, reason="NFL_FINAL_REFRESH_PRODUCER_RECEIPT_MALFORMED",
            identity=identity,
        )
    status = _upper(final_refresh.get("final_refresh_status"))
    reasons = final_refresh.get("reasons")
    if (final_refresh.get("source") != "NFL_CANONICAL_SCHEDULE_REQUERY"
            or not isinstance(reasons, list)
            or final_refresh.get("can_execute") is not False):
        return _receipt(
            stage=STAGE_FINAL_REFRESH,
            evidence_type=EVIDENCE_MALFORMED, disposition="FAIL",
            authority=None, reason="NFL_FINAL_REFRESH_PRODUCER_RECEIPT_MALFORMED",
            identity=identity,
        )
    if (status != "PASS" or reasons
            or final_refresh.get("probability_invalidated") is not False
            or final_refresh.get("rerun_required") is not False):
        return _receipt(
            stage=STAGE_FINAL_REFRESH,
            evidence_type=EVIDENCE_FAILED, disposition="FAIL",
            authority=NFL_FINAL_REFRESH_AUTHORITY,
            reason="NFL_FINAL_REFRESH_PRODUCER_REJECTED", identity=identity,
            extra={"producer_reasons": list(reasons)},
        )
    scored_id = str(final_refresh.get("scored_source_snapshot_id") or "")
    refreshed_id = str(final_refresh.get("refresh_source_snapshot_id") or "")
    if not source_snapshot_id or scored_id != str(source_snapshot_id) or not refreshed_id:
        return _receipt(
            stage=STAGE_FINAL_REFRESH,
            evidence_type=EVIDENCE_CONFLICT, disposition="FAIL",
            authority=NFL_FINAL_REFRESH_AUTHORITY,
            reason="NFL_FINAL_REFRESH_SOURCE_SNAPSHOT_ID_MISMATCH", identity=identity,
        )
    checked_at = _parse_timestamp(final_refresh.get("checked_at"))
    model_at = _parse_timestamp(final_refresh.get("model_timestamp"))
    refreshed_at = _parse_timestamp(final_refresh.get("refresh_source_timestamp"))
    if checked_at is None or model_at is None or refreshed_at is None:
        return _receipt(
            stage=STAGE_FINAL_REFRESH,
            evidence_type=EVIDENCE_MALFORMED, disposition="FAIL",
            authority=None, reason="NFL_FINAL_REFRESH_TIMESTAMP_INVALID", identity=identity,
        )
    if (checked_at < model_at or checked_at < refreshed_at
            or checked_at - refreshed_at > timedelta(hours=24)):
        return _receipt(
            stage=STAGE_FINAL_REFRESH,
            evidence_type=EVIDENCE_STALE, disposition="FAIL",
            authority=NFL_FINAL_REFRESH_AUTHORITY,
            reason="NFL_FINAL_REFRESH_TIMESTAMP_STALE_OR_FUTURE", identity=identity,
        )
    return _receipt(
        stage=STAGE_FINAL_REFRESH,
        evidence_type=EVIDENCE_EMITTED, disposition="PASS",
        authority=NFL_FINAL_REFRESH_AUTHORITY,
        reason=None, identity=identity,
        extra={"refreshed_at": checked_at.isoformat(),
               "material_update_timestamp": refreshed_at.isoformat(),
               "scored_source_snapshot_id": scored_id,
               "refresh_source_snapshot_id": refreshed_id,
               "freshness_status": "FRESH",
               "scheduled_event_status_accepted_as_proof": False,
               "borrowed_mlb_or_flask_receipt": False},
    )


def build_nfl_stage_proof_contract(
    *,
    research_run_id: str,
    event_key: str,
    official_event_id: str,
    score_snapshot_id: str | None,
    source_snapshot_id: str | None,
    model_artifact_id: str | None,
    model_version: str | None,
    package_validated: bool,
    governance_from_bridge: bool,
    probability_audit_result: Any,
    event_mutex_status: Any,
    calibration_health_status: Any,
    final_refresh: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the NFL producer's own stage receipts.

    Package validation is recorded only when ``_build_governed_probability_package``
    has already returned a package for this call. Dynamic calibration and final
    refresh stays ``EVIDENCE_NOT_EMITTED`` unless a real second schedule re-query
    from the certified NFL producer is passed and independently checked here.
    ``calibration_health_status`` is retained as an observation
    and is not accepted as dynamic-calibration proof.
    """
    identity = {
        "research_run_id": research_run_id,
        "event_key": event_key,
        "official_event_id": official_event_id,
        "score_snapshot_id": score_snapshot_id,
    }
    if package_validated:
        package = _receipt(
            stage=STAGE_PACKAGE,
            evidence_type=EVIDENCE_EMITTED,
            disposition="PASS",
            authority=PACKAGE_AUTHORITY,
            reason=None,
            identity=identity,
        )
    else:
        package = _receipt(
            stage=STAGE_PACKAGE,
            evidence_type=EVIDENCE_NOT_EMITTED,
            disposition="UNPROVEN",
            authority=None,
            reason="NFL_PACKAGE_VALIDATOR_DID_NOT_EMIT_PROOF",
            identity=identity,
        )

    dynamic = _receipt(
        stage=STAGE_DYNAMIC_CALIBRATION,
        evidence_type=EVIDENCE_NOT_EMITTED,
        disposition="UNPROVEN",
        authority=None,
        reason="NFL_DYNAMIC_CALIBRATION_STAGE_NOT_EXECUTED",
        identity=identity,
        extra={
            "observed_calibration_health_status": calibration_health_status,
            "calibration_health_accepted_as_proof": False,
        },
    )
    if governance_from_bridge:
        audit_pass = _upper(probability_audit_result) in _PASS_AUDIT_RESULTS
        audit = _receipt(
            stage=STAGE_PROBABILITY_AUDIT,
            evidence_type=EVIDENCE_EMITTED if audit_pass else EVIDENCE_FAILED,
            disposition="PASS" if audit_pass else "FAIL",
            authority=GOVERNANCE_AUTHORITY,
            reason=None if audit_pass else "NFL_PROBABILITY_AUDIT_NOT_PASS",
            identity=identity,
            extra={"probability_audit_result": probability_audit_result},
        )
        governor_pass = _upper(event_mutex_status) == "PASS"
        governor = _receipt(
            stage=STAGE_EVENT_GOVERNOR,
            evidence_type=EVIDENCE_EMITTED if governor_pass else EVIDENCE_FAILED,
            disposition="PASS" if governor_pass else "FAIL",
            authority=GOVERNANCE_AUTHORITY,
            reason=None if governor_pass else "NFL_EVENT_GOVERNOR_NOT_PASS",
            identity=identity,
            extra={"event_mutex_status": event_mutex_status},
        )
    else:
        audit = _receipt(
            stage=STAGE_PROBABILITY_AUDIT,
            evidence_type=EVIDENCE_NOT_EMITTED,
            disposition="UNPROVEN",
            authority=None,
            reason="NFL_GOVERNANCE_BRIDGE_DID_NOT_EXECUTE",
            identity=identity,
        )
        governor = _receipt(
            stage=STAGE_EVENT_GOVERNOR,
            evidence_type=EVIDENCE_NOT_EMITTED,
            disposition="UNPROVEN",
            authority=None,
            reason="NFL_GOVERNANCE_BRIDGE_DID_NOT_EXECUTE",
            identity=identity,
        )
    refresh = _nfl_producer_final_refresh_receipt(identity, source_snapshot_id, final_refresh)
    return {
        "contract_version": CONTRACT_VERSION,
        "producer": NFL_PRODUCER,
        "sport": "NFL",
        "research_run_id": research_run_id,
        "event_key": event_key,
        "official_event_id": official_event_id,
        "score_snapshot_id": score_snapshot_id,
        "source_snapshot_id": source_snapshot_id,
        "model_artifact_id": model_artifact_id,
        "model_version": model_version,
        "stages": {
            STAGE_PACKAGE: package,
            STAGE_DYNAMIC_CALIBRATION: dynamic,
            STAGE_PROBABILITY_AUDIT: audit,
            STAGE_EVENT_GOVERNOR: governor,
            STAGE_FINAL_REFRESH: refresh,
        },
        "can_execute": False,
    }


def _blocker_for(stage: str, evidence_type: str, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    if evidence_type == EVIDENCE_NOT_EMITTED:
        return _ABSENT_BLOCKER[stage]
    if evidence_type == EVIDENCE_STALE and stage == "final_refresh_passed":
        return "FINAL_REFRESH_EVIDENCE_STALE"
    if evidence_type == EVIDENCE_MALFORMED and stage == "final_refresh_passed":
        return "FINAL_REFRESH_EVIDENCE_MALFORMED"
    if evidence_type == EVIDENCE_CONFLICT and stage == "final_refresh_passed":
        return "FINAL_REFRESH_EVIDENCE_CONFLICT"
    if evidence_type == EVIDENCE_MALFORMED and stage == "probability_package_valid":
        return "PROBABILITY_PACKAGE_MALFORMED"
    return _FAILED_BLOCKER[stage]


def _legacy_bool(
    row: Mapping[str, Any],
    key: str,
    status_key: str | None = None,
    pass_statuses: set[str] | None = None,
) -> tuple[str, bool]:
    if key in row:
        ok = row.get(key) is True
        return (EVIDENCE_EMITTED if ok else EVIDENCE_FAILED), ok
    if status_key and status_key in row:
        ok = _upper(row.get(status_key)) in (pass_statuses or _PASS_DISPOSITIONS)
        return (EVIDENCE_EMITTED if ok else EVIDENCE_FAILED), ok
    return EVIDENCE_NOT_EMITTED, False


def _refresh_is_stale(refresh: Mapping[str, Any]) -> bool:
    if _upper(refresh.get("freshness_status")) == "STALE":
        return True
    refreshed = _parse_timestamp(refresh.get("refreshed_at") or refresh.get("final_refresh_timestamp"))
    material = _parse_timestamp(
        refresh.get("material_update_timestamp") or refresh.get("latest_material_update_timestamp")
    )
    return refreshed is not None and material is not None and refreshed < material


def _legacy_final_refresh_values(row: Mapping[str, Any]) -> tuple[str, bool, str | None]:
    present = ("final_refresh_passed" in row) or ("final_refresh_status" in row)
    if not present:
        return EVIDENCE_NOT_EMITTED, False, _ABSENT_BLOCKER["final_refresh_passed"]
    values: list[bool] = []
    if "final_refresh_passed" in row:
        values.append(row.get("final_refresh_passed") is True)
    if "final_refresh_status" in row:
        values.append(_upper(row.get("final_refresh_status")) in _PASS_DISPOSITIONS)
    if len(set(values)) > 1:
        return EVIDENCE_CONFLICT, False, "FINAL_REFRESH_EVIDENCE_CONFLICT"
    ok = values[0]
    if ok:
        return EVIDENCE_EMITTED, True, None
    return EVIDENCE_FAILED, False, "FINAL_REFRESH_NOT_COMPLETE"


def _final_refresh_evidence(row: Mapping[str, Any]) -> tuple[str, bool, str | None]:
    """Read an actual refresh receipt. Event state is never a receipt."""
    governance_present = "llp_governance" in row
    governance = row.get("llp_governance")
    if governance_present and not isinstance(governance, Mapping):
        return EVIDENCE_MALFORMED, False, "LLP_GOVERNANCE_MALFORMED"
    if isinstance(governance, Mapping) and "final_refresh" in governance:
        refresh = governance.get("final_refresh")
        if not isinstance(refresh, Mapping):
            return EVIDENCE_MALFORMED, False, "FINAL_REFRESH_EVIDENCE_MALFORMED"
        status = _upper(refresh.get("final_refresh_status") or refresh.get("status"))
        blockers = refresh.get("reasons") or refresh.get("blockers") or []
        if (
            not status
            or "can_execute" not in refresh
            or not isinstance(blockers, (list, tuple))
            or (
                "probability_invalidated" in refresh
                and not isinstance(refresh.get("probability_invalidated"), bool)
            )
            or (
                "rerun_required" in refresh
                and not isinstance(refresh.get("rerun_required"), bool)
            )
        ):
            return EVIDENCE_MALFORMED, False, "FINAL_REFRESH_EVIDENCE_MALFORMED"
        if _refresh_is_stale(refresh):
            return EVIDENCE_STALE, False, "FINAL_REFRESH_EVIDENCE_STALE"
        nested_ok = bool(
            status in _PASS_DISPOSITIONS
            and not blockers
            and refresh.get("probability_invalidated") is not True
            and refresh.get("rerun_required") is not True
            and refresh.get("can_execute") is False
        )
        legacy_type, legacy_ok, legacy_blocker = _legacy_final_refresh_values(row)
        if legacy_type == EVIDENCE_CONFLICT:
            return EVIDENCE_CONFLICT, False, legacy_blocker
        if legacy_type != EVIDENCE_NOT_EMITTED and legacy_ok != nested_ok:
            return EVIDENCE_CONFLICT, False, "FINAL_REFRESH_EVIDENCE_CONFLICT"
        if nested_ok:
            return EVIDENCE_EMITTED, True, None
        return EVIDENCE_FAILED, False, "FINAL_REFRESH_NOT_COMPLETE"
    return _legacy_final_refresh_values(row)


def _bound_identity_conflicts(row: Mapping[str, Any], receipt: Mapping[str, Any]) -> bool:
    pairs = (
        ("research_run_id", "bound_research_run_id"),
        ("event_key", "bound_event_key"),
        ("official_event_id", "bound_official_event_id"),
        ("score_snapshot_id", "bound_score_snapshot_id"),
    )
    for row_key, bound_key in pairs:
        row_value = row.get(row_key)
        bound_value = receipt.get(bound_key)
        if row_value not in (None, "") and bound_value not in (None, "") and str(row_value) != str(bound_value):
            return True
    return False


def _interpret_contract_receipt(
    row: Mapping[str, Any],
    stage: str,
    receipt: Any,
) -> tuple[str, bool, str | None]:
    audit_stage = _AUDIT_STAGE_BY_CONTRACT[stage]
    if receipt is None:
        return EVIDENCE_NOT_EMITTED, False, _ABSENT_BLOCKER[audit_stage]
    if not isinstance(receipt, Mapping):
        return EVIDENCE_MALFORMED, False, _blocker_for(audit_stage, EVIDENCE_MALFORMED)
    evidence_type = _upper(receipt.get("evidence_type"))
    if evidence_type not in {
        EVIDENCE_EMITTED,
        EVIDENCE_NOT_EMITTED,
        EVIDENCE_FAILED,
        EVIDENCE_STALE,
        EVIDENCE_MALFORMED,
        EVIDENCE_CONFLICT,
    }:
        return EVIDENCE_MALFORMED, False, "STAGE_PROOF_RECEIPT_MALFORMED"
    if _bound_identity_conflicts(row, receipt):
        return EVIDENCE_CONFLICT, False, "STAGE_PROOF_IDENTITY_CONFLICT"
    if evidence_type == EVIDENCE_NOT_EMITTED:
        return evidence_type, False, str(receipt.get("reason") or _ABSENT_BLOCKER[audit_stage])
    if evidence_type == EVIDENCE_STALE:
        return evidence_type, False, str(receipt.get("reason") or _blocker_for(audit_stage, evidence_type))
    if evidence_type in {EVIDENCE_FAILED, EVIDENCE_MALFORMED, EVIDENCE_CONFLICT}:
        return evidence_type, False, str(receipt.get("reason") or _blocker_for(audit_stage, evidence_type))
    disposition = _upper(receipt.get("disposition"))
    authority = str(receipt.get("authority") or "").strip()
    required_bindings = (
        "bound_research_run_id",
        "bound_event_key",
        "bound_official_event_id",
        "bound_score_snapshot_id",
    )
    if (
        disposition not in _PASS_DISPOSITIONS
        or not authority
        or any(receipt.get(field) in (None, "") for field in required_bindings)
        or receipt.get("can_execute") is not False
    ):
        return EVIDENCE_MALFORMED, False, "STAGE_PROOF_PASS_RECEIPT_MALFORMED"
    if stage == STAGE_FINAL_REFRESH and _refresh_is_stale(receipt):
        return EVIDENCE_STALE, False, "FINAL_REFRESH_EVIDENCE_STALE"
    return EVIDENCE_EMITTED, True, None


def _contract_identity_conflicts(row: Mapping[str, Any], contract: Mapping[str, Any]) -> bool:
    for field in _IDENTITY_FIELDS:
        row_value = row.get(field)
        contract_value = contract.get(field)
        if row_value not in (None, "") and contract_value not in (None, "") and str(row_value) != str(contract_value):
            return True
    return False


def classify_publication_evidence(row: Mapping[str, Any]) -> dict[str, Any]:
    """Classify each publication stage without inferring a missing receipt."""
    contract = row.get("stage_proof_contract")
    use_contract = isinstance(contract, Mapping) and contract.get("contract_version") == CONTRACT_VERSION
    if "stage_proof_contract" in row and not use_contract:
        return {
            "stages": {stage: False for stage in _FAILED_BLOCKER},
            "stage_evidence": {stage: EVIDENCE_MALFORMED for stage in _FAILED_BLOCKER},
            "first_blocker": "STAGE_PROOF_CONTRACT_MALFORMED",
            "first_evidence": EVIDENCE_MALFORMED,
        }
    if use_contract and _contract_identity_conflicts(row, contract):
        return {
            "stages": {stage: False for stage in _FAILED_BLOCKER},
            "stage_evidence": {stage: EVIDENCE_CONFLICT for stage in _FAILED_BLOCKER},
            "first_blocker": "STAGE_PROOF_IDENTITY_CONFLICT",
            "first_evidence": EVIDENCE_CONFLICT,
        }

    stages: dict[str, bool] = {}
    evidence: dict[str, str] = {}
    blockers: dict[str, str | None] = {}

    if use_contract:
        contract_stages = contract.get("stages") if isinstance(contract.get("stages"), Mapping) else {}
        for contract_stage, audit_stage in _AUDIT_STAGE_BY_CONTRACT.items():
            evidence_type, ok, blocker = _interpret_contract_receipt(
                {**dict(contract), **dict(row)},
                contract_stage,
                contract_stages.get(contract_stage),
            )
            stages[audit_stage] = ok
            evidence[audit_stage] = evidence_type
            blockers[audit_stage] = blocker
    else:
        package_type, package_ok = _legacy_bool(
            row, "probability_package_valid", "probability_validity_status", _PASS_DISPOSITIONS
        )
        calibration_type, calibration_ok = _legacy_bool(
            row, "dynamic_calibration_complete", "calibration_status", _PASS_DISPOSITIONS
        )
        if "probability_audit_passed" in row or "llp_probability_audit_result" in row:
            if "probability_audit_passed" in row and row.get("probability_audit_passed") is not True:
                audit_type, audit_ok = EVIDENCE_FAILED, False
            elif "probability_audit_passed" in row and row.get("probability_audit_passed") is True:
                audit_type, audit_ok = EVIDENCE_EMITTED, True
            else:
                audit_ok = _upper(row.get("llp_probability_audit_result")) in _PASS_AUDIT_RESULTS
                audit_type = EVIDENCE_EMITTED if audit_ok else EVIDENCE_FAILED
        else:
            audit_type, audit_ok = EVIDENCE_NOT_EMITTED, False
        if "event_governor_complete" in row or "event_mutex_status" in row:
            if "event_governor_complete" in row and row.get("event_governor_complete") is not True:
                governor_type, governor_ok = EVIDENCE_FAILED, False
            elif "event_governor_complete" in row and row.get("event_governor_complete") is True:
                governor_type, governor_ok = EVIDENCE_EMITTED, True
            else:
                governor_ok = _upper(row.get("event_mutex_status")) == "PASS"
                governor_type = EVIDENCE_EMITTED if governor_ok else EVIDENCE_FAILED
        else:
            governor_type, governor_ok = EVIDENCE_NOT_EMITTED, False
        refresh_type, refresh_ok, refresh_blocker = _final_refresh_evidence(row)
        for audit_stage, evidence_type, ok in (
            ("probability_package_valid", package_type, package_ok),
            ("dynamic_calibration_complete", calibration_type, calibration_ok),
            ("probability_audit_passed", audit_type, audit_ok),
            ("event_governor_complete", governor_type, governor_ok),
            ("final_refresh_passed", refresh_type, refresh_ok),
        ):
            stages[audit_stage] = ok
            evidence[audit_stage] = evidence_type
            blockers[audit_stage] = None if ok else (
                refresh_blocker if audit_stage == "final_refresh_passed" else _blocker_for(audit_stage, evidence_type)
            )

    numeric_ok = all(
        row.get(field) is not None for field in ("calibrated_probability", "calibrated_lower_bound")
    ) or all(
        row.get(field) is not None for field in ("calibrated_home_probability", "calibrated_home_lower_bound")
    )
    stages["numerical_bounds_present"] = numeric_ok
    evidence["numerical_bounds_present"] = EVIDENCE_EMITTED if numeric_ok else EVIDENCE_NOT_EMITTED
    blockers["numerical_bounds_present"] = None if numeric_ok else _ABSENT_BLOCKER["numerical_bounds_present"]

    authority = _upper(row.get("global_terminal_authority") or row.get("terminal_authority"))
    terminal_ok = authority in {TERMINAL_AUTHORITY, ""} and row.get("can_execute") is False
    if "can_execute" not in row and "global_terminal_authority" not in row and "terminal_authority" not in row:
        terminal_type = EVIDENCE_NOT_EMITTED
    elif row.get("can_execute") is True:
        terminal_type = EVIDENCE_FAILED
    elif terminal_ok:
        terminal_type = EVIDENCE_EMITTED
    else:
        terminal_type = EVIDENCE_FAILED
    stages["terminal_authority_preserved"] = terminal_ok
    evidence["terminal_authority_preserved"] = terminal_type
    blockers["terminal_authority_preserved"] = None if terminal_ok else _FAILED_BLOCKER["terminal_authority_preserved"]

    first_blocker = None
    first_evidence = None
    for stage, ok in stages.items():
        if not ok:
            first_blocker = blockers.get(stage) or _blocker_for(stage, evidence[stage])
            first_evidence = evidence[stage]
            break
    return {
        "stages": stages,
        "stage_evidence": evidence,
        "first_blocker": first_blocker,
        "first_evidence": first_evidence,
    }


def contract_publication_block(contract: Mapping[str, Any] | None) -> str | None:
    """Return the first NFL producer stage that does not prove publication."""
    if not isinstance(contract, Mapping) or contract.get("contract_version") != CONTRACT_VERSION:
        return "STAGE_PROOF_CONTRACT_MALFORMED"
    stages = contract.get("stages") if isinstance(contract.get("stages"), Mapping) else {}
    synthetic_row = {field: contract.get(field) for field in _IDENTITY_FIELDS}
    for stage in _CONTRACT_STAGES:
        evidence_type, ok, blocker = _interpret_contract_receipt(synthetic_row, stage, stages.get(stage))
        if not ok:
            return blocker or _blocker_for(_AUDIT_STAGE_BY_CONTRACT[stage], evidence_type)
    return None


def finalize_nfl_governed_publication(
    result: Mapping[str, Any],
    *,
    research_run_id: str,
    event_key: str,
    official_event_id: str,
    score_snapshot_id: str | None,
    source_snapshot_id: str | None,
    model_artifact_id: str | None,
    model_version: str | None,
    package_validated: bool,
    governance_from_bridge: bool,
    final_refresh: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach NFL stage proof and hold publication when proof is incomplete.

    Sporting probabilities already on ``result`` are preserved. Rank floors are
    not recomputed here. ``can_execute`` stays false.
    """
    from v17.team_event_official_publication_guard import evaluate_team_event_official_publication

    out = dict(result)
    contract = build_nfl_stage_proof_contract(
        research_run_id=research_run_id,
        event_key=event_key,
        official_event_id=official_event_id,
        score_snapshot_id=score_snapshot_id,
        source_snapshot_id=source_snapshot_id,
        model_artifact_id=model_artifact_id,
        model_version=model_version,
        package_validated=package_validated,
        governance_from_bridge=governance_from_bridge,
        probability_audit_result=out.get("llp_probability_audit_result"),
        event_mutex_status=out.get("event_mutex_status"),
        calibration_health_status=out.get("calibration_health_status"),
        final_refresh=final_refresh,
    )
    out["stage_proof_contract"] = contract
    blocker = contract_publication_block(contract)
    if blocker is not None:
        if out.get("terminal_label") == "FINAL_APPROVED":
            out["upstream_terminal_label"] = out.get("terminal_label")
            out["terminal_label"] = "MODEL_QUALIFIED_HOLD"
            out["terminal_ceiling"] = "MODEL_QUALIFIED_HOLD"
        if out.get("code") in _PUBLICATION_CODES:
            out["code"] = "STAGE_PROOF_UNPROVEN"
        blockers = [str(value) for value in (out.get("blockers") or [])]
        blockers.append(blocker)
        out["blockers"] = list(dict.fromkeys(blockers))
        out["probability_publishable"] = False
        out["rank_eligible"] = False
    out["can_execute"] = False
    guard = evaluate_team_event_official_publication(out)
    if blocker is not None:
        guard = dict(guard)
        guard["status"] = "HELD"
        guard["official_publication_allowed"] = False
        guard["rank_eligible"] = False
        guard["probability_publishable"] = False
        guard["blockers"] = list(dict.fromkeys([blocker, *(guard.get("blockers") or [])]))
        guard["can_execute"] = False
    out["official_publication_guard"] = guard
    out["publication_agreement"] = {
        "contract_version": CONTRACT_VERSION,
        "status": "PASS" if blocker is None and guard.get("official_publication_allowed") is True else "UNPROVEN",
        "reason": blocker,
        "rank_eligible": out.get("rank_eligible") is True and blocker is None,
        "probability_publishable": out.get("probability_publishable") is True and blocker is None,
        "upstream_terminal_label": out.get("upstream_terminal_label") or out.get("terminal_label"),
        "can_execute": False,
    }
    if blocker is not None:
        out["publication_agreement"]["status"] = "UNPROVEN"
        out["publication_agreement"]["rank_eligible"] = False
        out["publication_agreement"]["probability_publishable"] = False
    return out


def assess_publication_agreement(row: Mapping[str, Any], audit: Mapping[str, Any]) -> dict[str, Any]:
    """Compare scorer flags, official guard, and stage proof before reconciliation."""
    detail = row.get("detail") if isinstance(row.get("detail"), Mapping) else {}
    guard = detail.get("official_publication_guard") if isinstance(detail, Mapping) else None
    if not isinstance(guard, Mapping):
        guard = row.get("official_publication_guard") if isinstance(row.get("official_publication_guard"), Mapping) else None
    guard_allowed = isinstance(guard, Mapping) and guard.get("official_publication_allowed") is True
    detail_allowed = (
        detail.get("probability_publishable") is True and detail.get("rank_eligible") is True
    ) or (
        row.get("probability_publishable") is True and row.get("rank_eligible") is True
    )
    proof_pass = audit.get("status") == "PASS"
    upstream_guard_status = guard.get("status") if isinstance(guard, Mapping) else None
    if isinstance(guard, Mapping) and guard_allowed != detail_allowed:
        status = "CONFLICT"
        reason = "SCORER_GUARD_PUBLICATION_MISMATCH"
    elif not proof_pass:
        status = str(audit.get("status") or "UNPROVEN")
        reason = audit.get("first_blocker")
    elif guard_allowed or not isinstance(guard, Mapping):
        status = "PASS"
        reason = None
    else:
        status = "CONFLICT"
        reason = "SCORER_GUARD_PUBLICATION_MISMATCH"
    return {
        "contract_version": CONTRACT_VERSION,
        "status": status,
        "reason": reason,
        "upstream_official_publication_guard_status": upstream_guard_status,
        "upstream_rank_eligible": row.get("rank_eligible") is True or detail.get("rank_eligible") is True,
        "upstream_probability_publishable": (
            row.get("probability_publishable") is True or detail.get("probability_publishable") is True
        ),
        "can_execute": False,
    }


def apply_scan_row_stage_proof_ceiling(
    row: Mapping[str, Any],
    audit_row: Callable[[Mapping[str, Any]], Mapping[str, Any] | None],
) -> dict[str, Any]:
    """Make one discovered scan row agree on publication when proof is missing.

    Probabilities and lower bounds already present on the specialist payload are
    left unchanged. A completed sporting score stays visible and becomes
    unranked when the stage contract does not prove publication.
    """
    from v17.cross_sport_winner_discovery import MODEL_COMPLETED, OTHER_GOVERNED_HOLD

    item = dict(row)
    detail = dict(item.get("detail") or {}) if isinstance(item.get("detail"), Mapping) else {}
    if detail:
        item["detail"] = detail
    audit = audit_row(item)
    item["can_execute"] = False
    if not isinstance(audit, Mapping):
        return item
    agreement = assess_publication_agreement(item, audit)
    if agreement["status"] != "PASS":
        if item.get("bucket") == MODEL_COMPLETED:
            item["bucket"] = OTHER_GOVERNED_HOLD
        item["rank_eligible"] = False
        item["probability_publishable"] = False
        detail["rank_eligible"] = False
        detail["probability_publishable"] = False
        detail["can_execute"] = False
        if detail.get("terminal_label") == "FINAL_APPROVED":
            detail["upstream_terminal_label"] = detail.get("terminal_label")
            detail["terminal_label"] = "MODEL_QUALIFIED_HOLD"
            detail["terminal_ceiling"] = "MODEL_QUALIFIED_HOLD"
        if detail.get("code") in _PUBLICATION_CODES:
            detail["code"] = "STAGE_PROOF_UNPROVEN"
        reason = str(agreement.get("reason") or "STAGE_PROOF_UNPROVEN")
        blockers = [str(value) for value in (detail.get("blockers") or [])]
        blockers.append(reason)
        detail["blockers"] = list(dict.fromkeys(blockers))
        existing_guard = detail.get("official_publication_guard")
        guard = dict(existing_guard) if isinstance(existing_guard, Mapping) else {}
        guard["upstream_official_publication_guard_status"] = (
            existing_guard.get("status") if isinstance(existing_guard, Mapping) else None
        )
        guard["status"] = "HELD"
        guard["official_publication_allowed"] = False
        guard["rank_eligible"] = False
        guard["probability_publishable"] = False
        guard["blockers"] = list(dict.fromkeys([reason, *(guard.get("blockers") or [])]))
        guard["terminal_authority"] = TERMINAL_AUTHORITY
        guard["can_execute"] = False
        detail["official_publication_guard"] = guard
        detail["official_leaderboard_eligible"] = False
        if any(
            detail.get(field) is not None
            for field in ("calibrated_probability", "calibrated_home_probability", "calibrated_lower_bound")
        ):
            detail["model_probability_available"] = True
            detail["probability_visibility_status"] = "MODELED_HELD"
        item["detail"] = detail
        recomputed = audit_row(item)
        if isinstance(recomputed, Mapping):
            audit = recomputed
    item["publication_chain_audit"] = dict(audit)
    item["publication_chain_audit"]["can_execute"] = False
    item["publication_agreement"] = agreement
    return item
