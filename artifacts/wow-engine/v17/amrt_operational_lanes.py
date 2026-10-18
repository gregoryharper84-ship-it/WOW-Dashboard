"""AMRT seven-role decision pipeline for the existing governed Engineering worker.

This module does not start separate agents. It performs deterministic admission,
evidence triage, routing, repair/review planning and external-receipt verification
behind the existing Conductor, GitHub dispatch and Supabase writer leases.
No probability changes, credentials, GitHub writes, self-approval, or deployments.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from v17.amrt_closure_gate import (
    TEAM, TERMINAL_AUTHORITY, closure_gate, lease_reentry,
    provider_attempt, specialist_for,
)

ALLOWED_SOURCES = frozenset({
    "GITHUB", "RENDER", "SUPABASE", "DATADOG", "MODEL_RUN", "ACTION", "SIRT",
})
SHA = re.compile(r"^[0-9a-fA-F]{40}$")
SEVERITIES = frozenset({"P0", "P1", "P2", "P3"})
MODEL_FAILURE_MARKERS = ("MODEL_", "SCORER", "CALIBRAT", "STAGE_PROOF", "PUBLICATION")
DATA_FAILURE_MARKERS = ("IDENTITY", "FEED", "SOURCE", "HYDRATION", "PERSISTENCE", "EVENT_ID")


@dataclass(frozen=True)
class RoleReceipt:
    role: str
    state: str
    incident_id: str
    next_owner: str
    reasons: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    can_execute: bool = False
    terminal_authority: str = TERMINAL_AUTHORITY


def _receipt(role: str, state: str, incident: str, owner: str,
             reasons: Sequence[str] = (), refs: Sequence[str] = ()) -> RoleReceipt:
    if role not in TEAM or state not in {
        "READY", "ROUTED", "HELD", "PR_REVIEW", "REENTRY_REQUIRED",
        "FIXED_AND_VERIFIED",
    }:
        raise ValueError("AMRT_INVALID_ROLE_OR_STATE")
    return RoleReceipt(role, state, incident, owner, tuple(reasons), tuple(refs))


def _valid_ref(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 1024


def detect_blockers(signals: Sequence[Mapping[str, Any]],
                    known_fingerprints: Sequence[str] = ()) -> tuple[RoleReceipt, ...]:
    """Sentinel normalizes trusted, attributed error signals and deduplicates.

    Signals are untrusted evidence, never model output or an instruction to
    execute. Known fingerprints represent already-owned incidents.
    """
    seen = set(known_fingerprints)
    out: list[RoleReceipt] = []
    for signal in signals:
        source = str(signal.get("source") or "").upper()
        incident = str(signal.get("incident_id") or "").strip()
        code = str(signal.get("typed_failure") or "").strip().upper()
        event = str(signal.get("failure_boundary") or "").strip().upper()
        ref = signal.get("evidence_ref")
        severity = str(signal.get("severity") or "").upper()
        if not incident or source not in ALLOWED_SOURCES or severity not in SEVERITIES:
            out.append(_receipt("BLOCKER_SENTINEL", "HELD", incident, "BLOCKER_SENTINEL",
                                ("UNTRUSTED_OR_INCOMPLETE_SIGNAL",)))
            continue
        if not code or not event or not _valid_ref(ref):
            out.append(_receipt("BLOCKER_SENTINEL", "HELD", incident, "BLOCKER_SENTINEL",
                                ("MISSING_SOURCE_EVIDENCE",)))
            continue
        # Stable fingerprint ignores transient run IDs while retaining source
        # and the first failing boundary. This never equates different incidents.
        basis = json.dumps([incident, source, code, event], separators=(",", ":"))
        fingerprint = hashlib.sha256(basis.encode("utf-8")).hexdigest()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        out.append(_receipt("BLOCKER_SENTINEL", "ROUTED", incident,
                            "ROOT_CAUSE_INVESTIGATOR", (), (str(ref),)))
    return tuple(out)


def investigate_root_cause(incident: Mapping[str, Any]) -> RoleReceipt:
    incident_id = str(incident.get("incident_id") or "")
    reproduction = incident.get("reproduction_ref")
    cause = incident.get("root_cause_ref")
    boundary = incident.get("typed_failure")
    if not all(_valid_ref(value) for value in (reproduction, cause, boundary)):
        return _receipt("ROOT_CAUSE_INVESTIGATOR", "HELD", incident_id,
                        "ROOT_CAUSE_INVESTIGATOR", ("ROOT_CAUSE_EVIDENCE_MISSING",))
    next_owner = specialist_for(str(boundary))
    if next_owner == "ROOT_CAUSE_INVESTIGATOR":
        return _receipt("ROOT_CAUSE_INVESTIGATOR", "HELD", incident_id,
                        "ROOT_CAUSE_INVESTIGATOR", ("FAILURE_BOUNDARY_UNCLASSIFIED",),
                        (str(reproduction), str(cause)))
    return _receipt("ROOT_CAUSE_INVESTIGATOR", "ROUTED", incident_id, next_owner,
                    refs=(str(reproduction), str(cause)))


def model_recovery(incident: Mapping[str, Any]) -> RoleReceipt:
    incident_id = str(incident.get("incident_id") or "")
    code = str(incident.get("typed_failure") or "").upper()
    clazz = incident.get("change_class")
    if not any(marker in code for marker in MODEL_FAILURE_MARKERS):
        return _receipt("MODEL_RECOVERY_ENGINEER", "HELD", incident_id,
                        "ROOT_CAUSE_INVESTIGATOR", ("WRONG_MODEL_ROUTE",))
    if clazz == "C":
        return _receipt("MODEL_RECOVERY_ENGINEER", "HELD", incident_id,
                        "MODEL_RECOVERY_ENGINEER", ("CLASS_C_MODEL_OWNER_CERTIFICATION_REQUIRED",))
    if clazz not in ("A", "B"):
        return _receipt("MODEL_RECOVERY_ENGINEER", "HELD", incident_id,
                        "ROOT_CAUSE_INVESTIGATOR", ("CHANGE_CLASS_MISSING",))
    if not _valid_ref(incident.get("root_cause_ref")):
        return _receipt("MODEL_RECOVERY_ENGINEER", "HELD", incident_id,
                        "ROOT_CAUSE_INVESTIGATOR", ("ROOT_CAUSE_EVIDENCE_MISSING",))
    # Model specialist alone can change fitted probabilities; this lane only
    # returns an implementation route, never revised probability or approval.
    return _receipt("MODEL_RECOVERY_ENGINEER", "ROUTED", incident_id,
                    "CODE_CI_ENGINEER", refs=(str(incident["root_cause_ref"]),))


def data_integration_recovery(incident: Mapping[str, Any]) -> RoleReceipt:
    incident_id = str(incident.get("incident_id") or "")
    code = str(incident.get("typed_failure") or "").upper()
    if not any(marker in code for marker in DATA_FAILURE_MARKERS):
        return _receipt("DATA_INTEGRATION_ENGINEER", "HELD", incident_id,
                        "ROOT_CAUSE_INVESTIGATOR", ("WRONG_DATA_ROUTE",))
    if incident.get("change_class") == "C":
        return _receipt("DATA_INTEGRATION_ENGINEER", "HELD", incident_id,
                        "MODEL_RECOVERY_ENGINEER", ("CLASS_C_GOVERNANCE_REQUIRED",))
    if incident.get("change_class") not in ("A", "B") or not _valid_ref(incident.get("root_cause_ref")):
        return _receipt("DATA_INTEGRATION_ENGINEER", "HELD", incident_id,
                        "ROOT_CAUSE_INVESTIGATOR", ("EVIDENCE_OR_CHANGE_CLASS_MISSING",))
    return _receipt("DATA_INTEGRATION_ENGINEER", "ROUTED", incident_id,
                    "CODE_CI_ENGINEER", refs=(str(incident["root_cause_ref"]),))


def code_ci_recovery(incident: Mapping[str, Any], *,
                     now: datetime, active_source_pr: bool,
                     attempts: int, repeated_failure: bool,
                     authorized_fallback: bool) -> RoleReceipt:
    incident_id = str(incident.get("incident_id") or "")
    if active_source_pr:
        return _receipt("CODE_CI_ENGINEER", "PR_REVIEW", incident_id,
                        "INDEPENDENT_QA", ("EXISTING_SOURCE_PR_REQUIRES_REVIEW",))
    lease = lease_reentry(incident, now=now)
    if lease.status != "OWNED":
        return _receipt("CODE_CI_ENGINEER",
                        "REENTRY_REQUIRED" if lease.status == "REENTRY_REQUIRED" else "HELD",
                        incident_id, lease.next_owner, lease.blockers)
    if incident.get("lease_authority") != "EXISTING_AUTHENTICATED_ENGINEERING_WORKER":
        return _receipt("CODE_CI_ENGINEER", "HELD", incident_id,
                        "CODE_CI_ENGINEER", ("AUTHORIZED_WRITER_UNPROVEN",))
    retry = provider_attempt(attempts, repeated_failure, authorized_fallback)
    if retry.status not in ("RETRY_CANDIDATE", "FALLBACK_CANDIDATE"):
        return _receipt("CODE_CI_ENGINEER", "HELD", incident_id,
                        retry.next_owner, retry.blockers)
    return _receipt("CODE_CI_ENGINEER", "READY", incident_id,
                    "CODE_CI_ENGINEER", refs=("EXISTING_GITHUB_WORKER_ONLY",))


def independent_qa(incident: Mapping[str, Any], external_review: Mapping[str, Any]) -> RoleReceipt:
    incident_id = str(incident.get("incident_id") or "")
    implementer = str(incident.get("implementation_actor") or "")
    qa_actor = str(external_review.get("actor") or "")
    head = incident.get("head_sha")
    reviewed = external_review.get("head_sha")
    if (not implementer or not qa_actor or implementer == qa_actor
            or not isinstance(head, str) or not SHA.fullmatch(head)
            or reviewed != head):
        return _receipt("INDEPENDENT_QA", "HELD", incident_id, "INDEPENDENT_QA",
                        ("INDEPENDENT_EXACT_HEAD_REVIEW_MISSING",))
    if not _valid_ref(external_review.get("receipt_ref")):
        return _receipt("INDEPENDENT_QA", "HELD", incident_id, "INDEPENDENT_QA",
                        ("QA_RECEIPT_NOT_DURABLE",))
    if external_review.get("decision") != "PASS" or external_review.get("negative_regression_pass") is not True:
        return _receipt("INDEPENDENT_QA", "HELD", incident_id, "CODE_CI_ENGINEER",
                        ("QA_REJECT_OR_NEGATIVE_REGRESSION_FAILED",),
                        (str(external_review["receipt_ref"]),))
    return _receipt("INDEPENDENT_QA", "ROUTED", incident_id,
                    "RELEASE_VERIFICATION", refs=(str(external_review["receipt_ref"]),))


def release_verification(incident: Mapping[str, Any], *,
                         production_receipt: Mapping[str, Any],
                         qa_receipt: RoleReceipt) -> RoleReceipt:
    """Only independent external evidence may permit closure. No synthetic PASS."""
    incident_id = str(incident.get("incident_id") or "")
    if qa_receipt.incident_id != incident_id or qa_receipt.role != "INDEPENDENT_QA" or qa_receipt.state != "ROUTED":
        return _receipt("RELEASE_VERIFICATION", "HELD", incident_id,
                        "INDEPENDENT_QA", ("INDEPENDENT_QA_RECEIPT_MISSING",))
    if not _valid_ref(production_receipt.get("release_receipt_ref")):
        return _receipt("RELEASE_VERIFICATION", "HELD", incident_id,
                        "RELEASE_VERIFICATION", ("RELEASE_RECEIPT_NOT_DURABLE",))
    proof = closure_gate(production_receipt)
    if proof.status != "FIXED_AND_VERIFIED":
        return _receipt("RELEASE_VERIFICATION", "HELD", incident_id,
                        proof.next_owner, proof.blockers)
    return _receipt("RELEASE_VERIFICATION", "FIXED_AND_VERIFIED", incident_id,
                    "INDEPENDENT_QA", refs=(str(production_receipt["release_receipt_ref"]),))


def dispatch_preflight(item: Mapping[str, Any]) -> RoleReceipt:
    """Only used for explicitly AMRT-admitted manifests, never a dispatch token."""
    incident = str(item.get("issue_number") or "")
    if item.get("amrt_owned") is not True:
        return _receipt("BLOCKER_SENTINEL", "HELD", incident, "ECOSYSTEM_CONDUCTOR",
                        ("AMRT_INCIDENT_NOT_ADMITTED",))
    if (item.get("severity") != "P0" or item.get("execution_lane") != "RAPID"
            or not str(item.get("lease_group") or "").startswith("P0_")
            or item.get("change_class") not in ("A", "B")
            or item.get("can_execute") is not False):
        return _receipt("BLOCKER_SENTINEL", "HELD", incident,
                        "ECOSYSTEM_CONDUCTOR", ("AMRT_DISPATCH_CONTRACT_INVALID",))
    keys = item.get("conflict_keys")
    if not isinstance(keys, list) or not keys:
        return _receipt("BLOCKER_SENTINEL", "HELD", incident,
                        "ECOSYSTEM_CONDUCTOR", ("AMRT_CONFLICT_KEYS_MISSING",))
    return _receipt("BLOCKER_SENTINEL", "READY", incident, "CODE_CI_ENGINEER")
