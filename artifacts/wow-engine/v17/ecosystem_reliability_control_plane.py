"""WOW Ecosystem Reliability Control Plane.

This is the Class A synthesis layer above live probes, durable receipts,
capability readiness, and incident routing. It reports ecosystem truth and
metrics without acquiring probability, Engineering, Independent Verification,
SAFE_HOLD, or terminal authority.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Iterable, Mapping, Sequence

import ecosystem_capability_matrix
import ecosystem_incident_router

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"


@dataclass(frozen=True)
class MetricReceipt:
    name: str
    status: str
    value: float | None
    numerator: int | float | None
    denominator: int | float | None
    unit: str
    evidence_complete: bool
    reason: str | None = None
    can_execute: bool = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ratio_metric(
    name: str,
    numerator: int,
    denominator: int,
    *,
    unit: str = "ratio",
) -> MetricReceipt:
    if numerator < 0 or denominator < 0:
        raise ValueError(f"{name} counts must be non-negative")
    if numerator > denominator:
        raise ValueError(f"{name} numerator cannot exceed denominator")
    if denominator == 0:
        return MetricReceipt(
            name=name,
            status="UNKNOWN",
            value=None,
            numerator=numerator,
            denominator=denominator,
            unit=unit,
            evidence_complete=False,
            reason="DENOMINATOR_ZERO_OR_UNPROVEN",
        )
    return MetricReceipt(
        name=name,
        status="PASS",
        value=numerator / denominator,
        numerator=numerator,
        denominator=denominator,
        unit=unit,
        evidence_complete=True,
    )


def product_outcome_completion_rate(
    *, completed_supported_requests: int, supported_requests: int
) -> MetricReceipt:
    return _ratio_metric(
        "PRODUCT_OUTCOME_COMPLETION_RATE",
        completed_supported_requests,
        supported_requests,
    )


def candidate_conservation_rate(
    *, terminal_candidates: int, admitted_candidates: int
) -> MetricReceipt:
    return _ratio_metric(
        "CANDIDATE_CONSERVATION_RATE",
        terminal_candidates,
        admitted_candidates,
    )


def first_pass_completion_rate(
    *, first_pass_requests: int, supported_requests: int
) -> MetricReceipt:
    return _ratio_metric(
        "FIRST_PASS_COMPLETION_RATE",
        first_pass_requests,
        supported_requests,
    )


def decision_integrity_rate(
    *, integrity_complete_decisions: int, published_decisions: int
) -> MetricReceipt:
    return _ratio_metric(
        "DECISION_INTEGRITY_RATE",
        integrity_complete_decisions,
        published_decisions,
    )


def repeat_failure_elimination_rate(
    *, eliminated_repeat_fingerprints: int, repeat_failure_fingerprints: int
) -> MetricReceipt:
    return _ratio_metric(
        "REPEAT_FAILURE_ELIMINATION_RATE",
        eliminated_repeat_fingerprints,
        repeat_failure_fingerprints,
    )


def reliable_decision_availability(
    matrix: Sequence[ecosystem_capability_matrix.CapabilityReadiness],
) -> MetricReceipt:
    intended = [row for row in matrix if row.intended]
    ready = [row for row in intended if row.product_ready]
    return _ratio_metric(
        "RELIABLE_DECISION_AVAILABILITY",
        len(ready),
        len(intended),
    )


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def detection_to_proven_root_cause_time(
    findings: Iterable[Mapping[str, Any]],
) -> MetricReceipt:
    durations: list[float] = []
    eligible = 0
    for row in findings:
        first = _parse_time(row.get("first_detected_at"))
        if first is None:
            continue
        eligible += 1
        proven = _parse_time(row.get("root_cause_proven_at"))
        if proven is None:
            continue
        seconds = (proven - first).total_seconds()
        if seconds < 0:
            raise ValueError("root_cause_proven_at precedes first_detected_at")
        durations.append(seconds)

    if not eligible:
        return MetricReceipt(
            name="DETECTION_TO_PROVEN_ROOT_CAUSE_TIME",
            status="UNKNOWN",
            value=None,
            numerator=0,
            denominator=0,
            unit="seconds_mean",
            evidence_complete=False,
            reason="NO_ELIGIBLE_FINDINGS",
        )
    if len(durations) != eligible:
        return MetricReceipt(
            name="DETECTION_TO_PROVEN_ROOT_CAUSE_TIME",
            status="UNKNOWN",
            value=None,
            numerator=len(durations),
            denominator=eligible,
            unit="seconds_mean",
            evidence_complete=False,
            reason="ROOT_CAUSE_PROOF_INCOMPLETE",
        )
    return MetricReceipt(
        name="DETECTION_TO_PROVEN_ROOT_CAUSE_TIME",
        status="PASS",
        value=mean(durations),
        numerator=len(durations),
        denominator=eligible,
        unit="seconds_mean",
        evidence_complete=True,
    )


def _count(metric_counts: Mapping[str, Any], key: str) -> int:
    value = metric_counts.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def build_metric_family(
    *,
    matrix: Sequence[ecosystem_capability_matrix.CapabilityReadiness],
    metric_counts: Mapping[str, Any],
    findings: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    receipts = [
        reliable_decision_availability(matrix),
        product_outcome_completion_rate(
            completed_supported_requests=_count(
                metric_counts, "completed_supported_requests"
            ),
            supported_requests=_count(metric_counts, "supported_requests"),
        ),
        candidate_conservation_rate(
            terminal_candidates=_count(metric_counts, "terminal_candidates"),
            admitted_candidates=_count(metric_counts, "admitted_candidates"),
        ),
        first_pass_completion_rate(
            first_pass_requests=_count(metric_counts, "first_pass_requests"),
            supported_requests=_count(metric_counts, "supported_requests"),
        ),
        decision_integrity_rate(
            integrity_complete_decisions=_count(
                metric_counts, "integrity_complete_decisions"
            ),
            published_decisions=_count(metric_counts, "published_decisions"),
        ),
        repeat_failure_elimination_rate(
            eliminated_repeat_fingerprints=_count(
                metric_counts, "eliminated_repeat_fingerprints"
            ),
            repeat_failure_fingerprints=_count(
                metric_counts, "repeat_failure_fingerprints"
            ),
        ),
        detection_to_proven_root_cause_time(findings),
    ]
    return {receipt.name: asdict(receipt) for receipt in receipts}


def _extract_evaluation(live_result: Mapping[str, Any]) -> Mapping[str, Any]:
    evaluation = live_result.get("evaluation")
    if isinstance(evaluation, Mapping):
        return evaluation
    return live_result


def _route_truth(
    matrix: Sequence[ecosystem_capability_matrix.CapabilityReadiness],
) -> dict[str, Any]:
    grouped = ecosystem_capability_matrix.matrix_by_product(matrix)
    return {
        product: {
            "status": (
                "READY"
                if rows["metric_status"] == "PASS"
                and rows["intended_capabilities"] > 0
                and rows["ready_capabilities"] == rows["intended_capabilities"]
                else "NOT_READY"
            ),
            "intended_capabilities": rows["intended_capabilities"],
            "ready_capabilities": rows["ready_capabilities"],
            "reliable_decision_availability": rows["reliable_decision_availability"],
            "by_status": rows["by_status"],
        }
        for product, rows in grouped.items()
    }


def build_control_plane_snapshot(
    *,
    registry: Mapping[str, Any],
    live_result: Mapping[str, Any],
    handoff_receipts: Sequence[Mapping[str, Any]],
    capability_items: Iterable[
        ecosystem_capability_matrix.CapabilityInput | Mapping[str, Any]
    ],
    metric_counts: Mapping[str, Any],
    prior_findings: Sequence[Mapping[str, Any]] = (),
    observed_at: str | None = None,
) -> dict[str, Any]:
    observed_at = observed_at or _utc_now()
    evaluation = _extract_evaluation(live_result)
    if evaluation.get("can_execute") is not False:
        raise ValueError("Conductor evaluation can_execute must remain false")
    if evaluation.get("terminal_authority") != TERMINAL_AUTHORITY:
        raise ValueError("terminal authority cannot be overridden")

    matrix = ecosystem_capability_matrix.build_matrix(capability_items)
    routed = ecosystem_incident_router.route_findings(
        registry=registry,
        handoff_receipts=handoff_receipts,
        prior_findings=prior_findings,
        observed_at=observed_at,
    )
    all_findings = list(prior_findings) + routed["findings"]
    metrics = build_metric_family(
        matrix=matrix,
        metric_counts=metric_counts,
        findings=all_findings,
    )

    route_truth = _route_truth(matrix)
    critical_metric_unknown = any(
        metrics[name]["status"] == "UNKNOWN"
        for name in (
            "RELIABLE_DECISION_AVAILABILITY",
            "PRODUCT_OUTCOME_COMPLETION_RATE",
            "DECISION_INTEGRITY_RATE",
        )
    )
    safe_hold = bool(evaluation.get("safe_hold_required"))
    ecosystem_status = str(evaluation.get("ecosystem_status") or "SAFE_HOLD")
    control_plane_status = (
        "SAFE_HOLD"
        if safe_hold
        else "DEGRADED"
        if critical_metric_unknown
        or any(row["status"] != "READY" for row in route_truth.values())
        else ecosystem_status
    )

    return {
        "observed_at": observed_at,
        "control_plane_status": control_plane_status,
        "ecosystem_status": ecosystem_status,
        "safe_hold_required": safe_hold,
        "safe_hold_authority": evaluation.get(
            "safe_hold_authority", "SYSTEMS_INTELLIGENCE_RELIABILITY"
        ),
        "false_green_detected": bool(evaluation.get("false_green_detected")),
        "component_states": dict(evaluation.get("component_states") or {}),
        "handoff_states": dict(evaluation.get("handoff_states") or {}),
        "golden_paths": dict(evaluation.get("golden_paths") or {}),
        "route_truth": route_truth,
        "capability_matrix": ecosystem_capability_matrix.serialize_matrix(matrix),
        "metrics": metrics,
        "new_findings": routed,
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


def readiness_persistence_payload(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    if snapshot.get("can_execute") is not False:
        raise ValueError("control-plane snapshot can_execute must remain false")
    if snapshot.get("terminal_authority") != TERMINAL_AUTHORITY:
        raise ValueError("terminal authority cannot be overridden")
    return {
        "observed_at": snapshot["observed_at"],
        "ecosystem_status": snapshot["ecosystem_status"],
        "safe_hold_required": bool(snapshot["safe_hold_required"]),
        "false_green_detected": bool(snapshot["false_green_detected"]),
        "component_states": snapshot.get("component_states") or {},
        "handoff_states": snapshot.get("handoff_states") or {},
        "golden_paths": snapshot.get("golden_paths") or {},
        "capability_matrix": snapshot.get("capability_matrix") or [],
        "metrics": snapshot.get("metrics") or {},
        "evidence_refs": [],
        "terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "MetricReceipt",
    "TERMINAL_AUTHORITY",
    "build_control_plane_snapshot",
    "build_metric_family",
    "candidate_conservation_rate",
    "decision_integrity_rate",
    "detection_to_proven_root_cause_time",
    "first_pass_completion_rate",
    "product_outcome_completion_rate",
    "readiness_persistence_payload",
    "reliable_decision_availability",
    "repeat_failure_elimination_rate",
]
