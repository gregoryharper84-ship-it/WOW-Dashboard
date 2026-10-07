"""Independent Class A SIRT assurance primitives for existing WOW evidence.

Pure/evidence-only: does not mutate the auditor, incident ledger, sporting
probabilities, or terminal governance. A proof of technical delivery is not a
proof of user-visible product acceptance.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
INDEPENDENT_VERIFIER = "INDEPENDENT_VERIFICATION"


@dataclass(frozen=True)
class AssuranceResult:
    status: str
    blockers: tuple[str, ...]
    can_execute: bool = False
    terminal_authority: str = TERMINAL_AUTHORITY

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "blockers": list(self.blockers),
            "can_execute": False,
            "terminal_authority": TERMINAL_AUTHORITY,
        }


def _passed(value: Any) -> bool:
    # Explicit PASS required. A missing/false value or a truthy string is not proof.
    return value is True or value == "PASS"


def verify_product_closure(receipt: Mapping[str, Any]) -> AssuranceResult:
    """Fail-closed predicate; does not replace independent verification.

    The receipt is an external signed/traceable claim, never proof by itself.
    Callers must independently validate references before publishing VERIFIED.
    """
    blockers: list[str] = []
    if receipt.get("can_execute") is not False:
        blockers.append("EXECUTION_BOUNDARY_UNPROVEN")
    if receipt.get("terminal_authority") != TERMINAL_AUTHORITY:
        blockers.append("TERMINAL_AUTHORITY_DRIFT")

    exact_head = str(receipt.get("head_sha") or "")
    ci_head = str(receipt.get("ci_head_sha") or "")
    merge_sha = str(receipt.get("merge_sha") or "")
    deployed_sha = str(receipt.get("deployed_sha") or "")
    for key, value in (("HEAD_SHA", exact_head), ("MERGE_SHA", merge_sha),
                       ("DEPLOYED_SHA", deployed_sha)):
        if len(value) != 40 or any(c not in "0123456789abcdef" for c in value.lower()):
            blockers.append(key + "_UNVERIFIED")
    if ci_head != exact_head or not _passed(receipt.get("exact_head_ci")):
        blockers.append("EXACT_HEAD_CI_UNVERIFIED")
    if deployed_sha != merge_sha and not _passed(receipt.get("deployment_ancestry_verified")):
        blockers.append("DEPLOYED_IDENTITY_UNVERIFIED")
    for field in ("unit_tests", "integration_tests", "regression_tests",
                  "negative_path_tests", "review", "qa_verification",
                  "production_acceptance", "independent_verification",
                  "candidate_reconciliation"):
        if not _passed(receipt.get(field)):
            blockers.append(field.upper() + "_UNVERIFIED")
    if receipt.get("independent_verifier") != INDEPENDENT_VERIFIER:
        blockers.append("INDEPENDENT_VERIFIER_IDENTITY_UNVERIFIED")
    for evidence in ("ci_url", "deployment_evidence_url",
                     "production_acceptance_url", "verification_evidence_url"):
        if not str(receipt.get(evidence) or "").startswith(("https://", "http://")):
            blockers.append(evidence.upper() + "_MISSING")
    # This is evidence-shape readiness, not authority to declare production live.
    return AssuranceResult("EVIDENCE_READY_FOR_INDEPENDENT_REVIEW" if not blockers
                           else "BLOCKED_INCOMPLETE_EVIDENCE", tuple(blockers))


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def check_auditor_heartbeat(observation: Mapping[str, Any], *,
                            now: datetime, max_age: timedelta = timedelta(minutes=5)) -> AssuranceResult:
    """Fail closed for stale, missing, or degraded auditor heartbeat.

    A live database POST alone cannot establish healthy monitoring.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    timestamp = _parse_utc(observation.get("last_heartbeat_at"))
    blockers: list[str] = []
    if timestamp is None:
        blockers.append("AUDITOR_HEARTBEAT_MISSING")
    elif timestamp > now.astimezone(timezone.utc) + timedelta(seconds=30):
        blockers.append("AUDITOR_CLOCK_SKEW")
    elif now.astimezone(timezone.utc) - timestamp > max_age:
        blockers.append("AUDITOR_HEARTBEAT_STALE")
    if observation.get("status") != "RUNNING":
        blockers.append("AUDITOR_RUNTIME_NOT_RUNNING")
    if observation.get("can_execute") is not False:
        blockers.append("AUDITOR_EXECUTION_BOUNDARY_UNPROVEN")
    if observation.get("terminal_authority") != TERMINAL_AUTHORITY:
        blockers.append("AUDITOR_TERMINAL_AUTHORITY_DRIFT")
    return AssuranceResult("HEALTHY_OBSERVED" if not blockers else "AUDITOR_UNHEALTHY",
                           tuple(blockers))


def recurring_incidents(records: Sequence[Mapping[str, Any]], *,
                        minimum_occurrences: int = 3) -> list[dict[str, Any]]:
    """Group *confirmed* matching causes; unknown causes cannot be conflated."""
    if minimum_occurrences < 2:
        raise ValueError("minimum_occurrences must be >= 2")
    grouped: dict[tuple[str, str, str], list[str]] = {}
    for record in records:
        if record.get("root_cause_status") != "CONFIRMED":
            continue
        cause = str(record.get("root_cause_code") or "").strip().upper()
        subsystem = str(record.get("primary_subsystem") or "").strip().upper()
        domain = str(record.get("domain") or "").strip().upper()
        incident_id = str(record.get("postmortem_id") or "").strip()
        if not all((cause, subsystem, domain, incident_id)):
            continue
        grouped.setdefault((domain, subsystem, cause), []).append(incident_id)
    results: list[dict[str, Any]] = []
    for (domain, subsystem, cause), incidents in sorted(grouped.items()):
        unique = sorted(set(incidents))
        if len(unique) < minimum_occurrences:
            continue
        fingerprint = hashlib.sha256(
            json.dumps([domain, subsystem, cause], separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        results.append({
            "fingerprint": fingerprint,
            "domain": domain,
            "subsystem": subsystem,
            "root_cause_code": cause,
            "incident_ids": unique,
            "occurrences": len(unique),
            "disposition": "PREVENTION_REVIEW_REQUIRED",
            "owner": "SYSTEMS_INTELLIGENCE_RELIABILITY",
            "engineering_handoff_required": True,
            "can_execute": False,
            "terminal_authority": TERMINAL_AUTHORITY,
        })
    return results
