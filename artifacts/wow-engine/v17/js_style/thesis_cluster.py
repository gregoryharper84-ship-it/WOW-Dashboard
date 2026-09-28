"""Same-game thesis/dependence metadata without joint probability math."""
from __future__ import annotations

from dataclasses import dataclass

from v17.js_style.contracts import (
    EVIDENCE_COMPLETE,
    EVIDENCE_INCOMPLETE,
    THESIS_CLASSIFICATIONS,
    ThesisClusterResult,
    validate_closed_vocabulary,
)


@dataclass(frozen=True)
class ThesisEvidence:
    cluster_thesis_id: str
    cluster_thesis_version: str
    cluster_direction: str | None
    shared_driver: str | None
    joint_benefit_paths: tuple[str, ...] = ()
    joint_failure_paths: tuple[str, ...] = ()
    dependence_type: str | None = None
    dependence_evidence_complete: bool = False
    conflicting_evidence: bool = False
    shared_fragility: bool = False


def classify_thesis(evidence: ThesisEvidence) -> ThesisClusterResult:
    if not str(evidence.cluster_thesis_id or "").strip():
        raise ValueError("JS_CLUSTER_THESIS_ID_REQUIRED")
    if not str(evidence.cluster_thesis_version or "").strip():
        raise ValueError("JS_CLUSTER_THESIS_VERSION_REQUIRED")

    if evidence.shared_fragility:
        classification = "SHARED_FRAGILITY"
    elif evidence.conflicting_evidence:
        classification = "THESIS_CONFLICTING"
    elif (
        evidence.dependence_evidence_complete
        and evidence.shared_driver
        and evidence.joint_benefit_paths
        and not evidence.joint_failure_paths
    ):
        classification = "THESIS_COHERENT"
    else:
        classification = "THESIS_NEUTRAL"

    validate_closed_vocabulary(
        (classification,), THESIS_CLASSIFICATIONS, code="JS_THESIS_CLASSIFICATION_UNKNOWN"
    )
    return ThesisClusterResult(
        cluster_thesis_id=evidence.cluster_thesis_id,
        cluster_thesis_version=evidence.cluster_thesis_version,
        cluster_direction=evidence.cluster_direction,
        shared_driver=evidence.shared_driver,
        joint_benefit_paths=evidence.joint_benefit_paths,
        joint_failure_paths=evidence.joint_failure_paths,
        dependence_type=evidence.dependence_type,
        dependence_evidence_status=(
            EVIDENCE_COMPLETE if evidence.dependence_evidence_complete else EVIDENCE_INCOMPLETE
        ),
        classification=classification,
        joint_probability_computed=False,
    )


__all__ = ["ThesisEvidence", "classify_thesis"]
