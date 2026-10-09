"""Evidence-backed incident and Engineering Opportunity routing.

Class A only. Findings describe proven system/handoff problems and ownership.
They never alter sporting/weather probability, certification, or terminal state.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
from typing import Any, Iterable, Mapping, Sequence

CAN_EXECUTE = False
SYSTEMS_OWNER = "SYSTEMS_INTELLIGENCE_RELIABILITY"
ENGINEERING_OWNER = "ENGINEERING_CLOSURE"
INDEPENDENT_VERIFIER = "INDEPENDENT_VERIFICATION"

BAD_STATUSES = {"FAIL", "BLOCKED", "UNKNOWN"}
REPEAT_OPPORTUNITY_THRESHOLD = 3


@dataclass(frozen=True)
class Finding:
    finding_id: str
    finding_type: str
    fingerprint: str
    severity: str
    status: str
    first_detected_at: str
    last_observed_at: str
    occurrence_count: int
    affected_paths: tuple[str, ...]
    first_failing_boundary: str | None
    typed_failure: str | None
    diagnostic_owner: str
    closure_owner: str
    change_class: str
    evidence_refs: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    root_cause_proven_at: str | None = None
    verified_closed_at: str | None = None
    can_execute: bool = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def fingerprint_for(
    *,
    handoff_id: str,
    first_failing_boundary: str | None,
    typed_failure: str | None,
) -> str:
    raw = "|".join(
        (
            handoff_id.strip(),
            str(first_failing_boundary or "UNKNOWN_BOUNDARY").strip(),
            str(typed_failure or "UNKNOWN_FAILURE").strip(),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _change_class(boundary: str | None, typed_failure: str | None) -> str:
    text = f"{boundary or ''} {typed_failure or ''}".upper()
    if any(
        token in text
        for token in (
            "PROBABILITY_MATH",
            "CALIBRATION",
            "LOWER_BOUND",
            "THRESHOLD",
            "COEFFICIENT",
            "FITTED_ARTIFACT",
            "MODEL_DISTRIBUTION",
        )
    ):
        return "C"
    if any(
        token in text
        for token in (
            "ROUTING",
            "IDENTITY",
            "HYDRATION",
            "MODEL_REGISTRATION",
            "SPECIALIST_REGISTRATION",
            "FEATURE_CONTRACT",
        )
    ):
        return "B"
    return "A"


def _severity(status: str, critical: bool, affected_paths: Sequence[str]) -> str:
    status = str(status).upper()
    if critical and affected_paths and status in {"FAIL", "BLOCKED"}:
        return "P0"
    if critical and status in BAD_STATUSES:
        return "P1"
    if status in {"FAIL", "BLOCKED"}:
        return "P1"
    if status == "UNKNOWN":
        return "P2"
    return "P3"


def _affected_paths(
    registry: Mapping[str, Any],
    handoff_id: str,
) -> tuple[str, ...]:
    result = [
        name
        for name, handoffs in (registry.get("golden_paths") or {}).items()
        if handoff_id in handoffs
    ]
    return tuple(sorted(map(str, result)))


def _acceptance_criteria(
    handoff_id: str,
    affected_paths: Sequence[str],
    change_class: str,
) -> tuple[str, ...]:
    criteria = [
        f"authoritative handoff receipt {handoff_id}=PASS",
        "first proven failing boundary cleared without typed-failure relabeling",
        "adjacent regression coverage passes",
        "exact-head protected CI passes",
        "Independent Verification returns VERIFIED",
        "can_execute=false remains invariant",
    ]
    if affected_paths:
        criteria.append(
            "affected golden path(s) reconcile READY or terminate with an explicit legitimate blocker: "
            + ",".join(affected_paths)
        )
    if change_class == "B":
        criteria.append("Class B routing/hydration review and governed promotion requirements satisfied")
    if change_class == "C":
        criteria.append(
            "Class C challenger/replay/counterexample/holdout/regression/governed-review requirements satisfied"
        )
    return tuple(criteria)


def _existing_by_fingerprint(
    prior_findings: Iterable[Mapping[str, Any]],
) -> dict[tuple[str, str], Mapping[str, Any]]:
    output: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in prior_findings:
        fingerprint = str(row.get("fingerprint") or "")
        finding_type = str(row.get("finding_type") or "INCIDENT").upper()
        if fingerprint:
            current = output.get((finding_type, fingerprint))
            if current is None or str(row.get("last_observed_at") or "") > str(
                current.get("last_observed_at") or ""
            ):
                output[(finding_type, fingerprint)] = row
    return output


def _finding_id(kind: str, fingerprint: str, recurrence: int = 0) -> str:
    base = f"ECO-{kind[:3]}-{fingerprint[:12]}"
    return f"{base}-R{recurrence}" if recurrence else base


def _next_occurrence(
    previous: Mapping[str, Any] | None,
) -> tuple[int, str, int]:
    if not previous:
        return 1, _utc_now(), 0
    previous_count = int(previous.get("occurrence_count") or 0)
    first = str(previous.get("first_detected_at") or _utc_now())
    recurrence = 0
    if str(previous.get("status") or "").upper() == "VERIFIED_CLOSED":
        recurrence = previous_count + 1
        return 1, _utc_now(), recurrence
    return previous_count + 1, first, recurrence


def route_findings(
    *,
    registry: Mapping[str, Any],
    handoff_receipts: Sequence[Mapping[str, Any]],
    prior_findings: Sequence[Mapping[str, Any]] = (),
    observed_at: str | None = None,
) -> dict[str, Any]:
    observed_at = observed_at or _utc_now()
    existing = _existing_by_fingerprint(prior_findings)
    incidents: list[Finding] = []
    opportunities: list[Finding] = []

    for row in handoff_receipts:
        status = str(row.get("status") or "UNKNOWN").upper()
        if status not in BAD_STATUSES:
            continue

        handoff_id = str(row.get("handoff_id") or "")
        spec = (registry.get("handoffs") or {}).get(handoff_id) or {}
        affected_paths = _affected_paths(registry, handoff_id)
        fingerprint = fingerprint_for(
            handoff_id=handoff_id,
            first_failing_boundary=row.get("first_failing_boundary"),
            typed_failure=row.get("typed_failure"),
        )
        previous = existing.get(("INCIDENT", fingerprint))
        count, first_detected_at, recurrence = _next_occurrence(previous)
        change_class = _change_class(
            row.get("first_failing_boundary"),
            row.get("typed_failure"),
        )
        incident = Finding(
            finding_id=(
                str(previous.get("finding_id"))
                if previous and recurrence == 0
                else _finding_id("INCIDENT", fingerprint, recurrence)
            ),
            finding_type="INCIDENT",
            fingerprint=fingerprint,
            severity=_severity(status, bool(spec.get("critical")), affected_paths),
            status="OPEN",
            first_detected_at=first_detected_at,
            last_observed_at=observed_at,
            occurrence_count=count,
            affected_paths=affected_paths,
            first_failing_boundary=row.get("first_failing_boundary"),
            typed_failure=row.get("typed_failure"),
            diagnostic_owner=SYSTEMS_OWNER,
            closure_owner=ENGINEERING_OWNER,
            change_class=change_class,
            evidence_refs=tuple(map(str, row.get("evidence_refs") or ())),
            acceptance_criteria=_acceptance_criteria(
                handoff_id,
                affected_paths,
                change_class,
            ),
        )
        incidents.append(incident)

        lifetime_occurrences = count
        if previous and recurrence:
            lifetime_occurrences = int(previous.get("occurrence_count") or 0) + 1
        if lifetime_occurrences >= REPEAT_OPPORTUNITY_THRESHOLD:
            opportunity_fingerprint = hashlib.sha256(
                f"OPPORTUNITY|{fingerprint}".encode("utf-8")
            ).hexdigest()
            old_opportunity = existing.get(("OPPORTUNITY", opportunity_fingerprint))
            opp_count, opp_first, opp_recurrence = _next_occurrence(old_opportunity)
            criteria = (
                f"root cause for recurring {handoff_id} failure proven",
                "permanent prevention/regression control implemented",
                "repeat failure no longer appears across the governed observation window",
                "Engineering capacity loss from this fingerprint is measurably reduced",
                "Independent Verification returns VERIFIED",
                "can_execute=false remains invariant",
            )
            opportunities.append(
                Finding(
                    finding_id=(
                        str(old_opportunity.get("finding_id"))
                        if old_opportunity and opp_recurrence == 0
                        else _finding_id("OPPORTUNITY", opportunity_fingerprint, opp_recurrence)
                    ),
                    finding_type="OPPORTUNITY",
                    fingerprint=opportunity_fingerprint,
                    severity="P1" if incident.severity in {"P0", "P1"} else "P2",
                    status="OPEN",
                    first_detected_at=opp_first,
                    last_observed_at=observed_at,
                    occurrence_count=opp_count,
                    affected_paths=affected_paths,
                    first_failing_boundary=incident.first_failing_boundary,
                    typed_failure=incident.typed_failure,
                    diagnostic_owner=SYSTEMS_OWNER,
                    closure_owner=ENGINEERING_OWNER,
                    change_class=change_class,
                    evidence_refs=incident.evidence_refs,
                    acceptance_criteria=criteria,
                )
            )

    return {
        "incidents": [asdict(item) for item in incidents],
        "opportunities": [asdict(item) for item in opportunities],
        "findings": [asdict(item) for item in (*incidents, *opportunities)],
        "incident_count": len(incidents),
        "opportunity_count": len(opportunities),
        "can_execute": False,
    }


def close_finding(
    finding: Mapping[str, Any],
    *,
    independent_verification: bool,
    exact_head_regression_pass: bool,
    boundary_cleared: bool,
    closed_at: str | None = None,
) -> dict[str, Any]:
    if not (independent_verification and exact_head_regression_pass and boundary_cleared):
        raise ValueError("finding cannot be VERIFIED_CLOSED without independent verified closure")
    output = dict(finding)
    output["status"] = "VERIFIED_CLOSED"
    output["verified_closed_at"] = closed_at or _utc_now()
    output["last_observed_at"] = output["verified_closed_at"]
    output["can_execute"] = False
    return output


__all__ = [
    "BAD_STATUSES",
    "CAN_EXECUTE",
    "ENGINEERING_OWNER",
    "Finding",
    "INDEPENDENT_VERIFIER",
    "REPEAT_OPPORTUNITY_THRESHOLD",
    "SYSTEMS_OWNER",
    "close_finding",
    "fingerprint_for",
    "route_findings",
]
