"""Deterministic terminal ceiling reducer (packet section 16).

Ordinary code, exhaustively tested — never an LLM call, never a vote among
workers. Inputs are the required workers' terminal job results for one
candidate; output is one immutable native terminal label.

WOW-PATCH-2026-08-30 adds one monotonic rule here: a proven calibration /
publication-only blocker may cap an otherwise successful controlling model at
MODEL_QUALIFIED_HOLD, but it may not turn that successful specialist into
MODEL_UNAVAILABLE. Actual controlling-specialist failure still does.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from agent_runtime.state_machine import JOB_TERMINAL_STATES

CEILING_ORDER: tuple[str, ...] = (
    "RESEARCH_INTEREST",
    "IDENTITY_VERIFIED",
    "EVIDENCE_VERIFIED",
    "MODEL_QUALIFIED_HOLD",
    "MARKET_VERIFIED_HOLD",
    "STRUCTURE_VERIFIED_HOLD",
    "FINAL_REFRESH_HOLD",
    "FINAL_APPROVED",
)

_CEILING_RANK = {name: index for index, name in enumerate(CEILING_ORDER)}
_PUBLICATION_ONLY_BLOCKERS = {
    "FORWARD_SHADOW_NOT_COMPLETED",
    "CALIBRATION_HEALTH_BLOCKED",
    "CALIBRATION_HEALTH_NOT_PASS",
    "CALIBRATION_BLOCKED",
    "PROBABILITY_PUBLICATION_HELD",
    "GOVERNED_PROBABILITY_NOT_PUBLISHABLE",
    "PUBLICATION_NOT_RATIFIED",
    "PRODUCTION_FEATURE_READY_FALSE",
}


def strictest(ceilings: list[str]) -> str:
    """Return the strictest (lowest-rank) ceiling in the list."""
    if not ceilings:
        return "GOVERNANCE_LABEL_UNKNOWN"
    ranked = sorted(ceilings, key=lambda c: _CEILING_RANK.get(c, -1))
    return ranked[0]


def _canonical_publication_blocker(value: str) -> Optional[str]:
    upper = str(value or "").strip().upper()
    for code in _PUBLICATION_ONLY_BLOCKERS:
        if upper == code or code in upper:
            return code
    return None


def _publication_only(blockers: tuple[str, ...]) -> bool:
    if not blockers:
        return False
    return all(_canonical_publication_blocker(value) is not None for value in blockers)


@dataclass(frozen=True)
class RequiredJobResult:
    worker_id: str
    status: str
    ceiling: Optional[str] = None
    blockers: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ReducedDecision:
    label: str
    ceiling: str
    blockers: tuple[str, ...]
    probability_publishable: bool
    governed_publishable: bool = False
    failed_contract_scope: tuple[str, ...] = field(default_factory=tuple)
    probability_claim_status: Optional[str] = None
    specialist_model_capability: str = "UNKNOWN"
    can_execute: bool = False


def reduce_candidate(
    *,
    controlling_worker_id: Optional[str],
    controlling_job_status: Optional[str],
    required_jobs: list[RequiredJobResult],
) -> ReducedDecision:
    """Reduce one candidate while preserving lane-scoped blockers.

    A not-yet-terminal job blocks any decision. No controlling specialist means
    no specialist coverage. Only a controlling job that genuinely did not
    succeed maps to MODEL_UNAVAILABLE. A successful controlling specialist plus
    a calibration/publication lock remains a model-qualified research result.
    """
    if any(job.status not in JOB_TERMINAL_STATES for job in required_jobs):
        return ReducedDecision(
            label="RUN_NOT_TERMINAL",
            ceiling="RUN_NOT_TERMINAL",
            blockers=(),
            probability_publishable=False,
            governed_publishable=False,
            probability_claim_status=None,
            specialist_model_capability="UNKNOWN",
        )

    if controlling_worker_id is None:
        return ReducedDecision(
            label="NO_SPECIALIST_COVERAGE",
            ceiling="NO_SPECIALIST_COVERAGE",
            blockers=(),
            probability_publishable=False,
            governed_publishable=False,
            probability_claim_status="MODEL_UNAVAILABLE",
            specialist_model_capability="UNAVAILABLE",
        )

    if controlling_job_status != "SUCCEEDED":
        return ReducedDecision(
            label="MODEL_UNAVAILABLE",
            ceiling="MODEL_UNAVAILABLE",
            blockers=(),
            probability_publishable=False,
            governed_publishable=False,
            failed_contract_scope=("CONFIDENCE",),
            probability_claim_status="MODEL_UNAVAILABLE",
            specialist_model_capability="UNAVAILABLE",
        )

    all_blockers: list[str] = []
    for job in required_jobs:
        all_blockers.extend(job.blockers)
    blockers = tuple(dict.fromkeys(all_blockers))

    known_ceilings = [job.ceiling for job in required_jobs if job.ceiling is not None]
    ceiling = strictest(known_ceilings) if known_ceilings else "GOVERNANCE_LABEL_UNKNOWN"

    if ceiling == "GOVERNANCE_LABEL_UNKNOWN" or ceiling not in _CEILING_RANK:
        return ReducedDecision(
            label="GOVERNANCE_LABEL_UNKNOWN",
            ceiling="GOVERNANCE_LABEL_UNKNOWN",
            blockers=blockers,
            probability_publishable=False,
            governed_publishable=False,
            failed_contract_scope=("GLOBAL",),
            probability_claim_status=None,
            specialist_model_capability="AVAILABLE",
        )

    if _publication_only(blockers):
        # The publication lock is a ceiling, never an upgrade. If an upstream
        # stage is already stricter than MODEL_QUALIFIED_HOLD, preserve it.
        ceiling = strictest([ceiling, "MODEL_QUALIFIED_HOLD"])
        return ReducedDecision(
            label=ceiling,
            ceiling=ceiling,
            blockers=blockers,
            probability_publishable=False,
            governed_publishable=False,
            failed_contract_scope=("CALIBRATION", "PUBLICATION"),
            probability_claim_status="CALIBRATION_BLOCKED_NO_PUBLISH",
            specialist_model_capability="AVAILABLE",
        )

    publishable = (
        not blockers
        and _CEILING_RANK.get(ceiling, -1) >= _CEILING_RANK["MODEL_QUALIFIED_HOLD"]
    )
    return ReducedDecision(
        label=ceiling,
        ceiling=ceiling,
        blockers=blockers,
        probability_publishable=publishable,
        governed_publishable=publishable,
        failed_contract_scope=(),
        probability_claim_status=(
            "GOVERNED_CALIBRATED_PUBLISHABLE" if publishable else None
        ),
        specialist_model_capability="AVAILABLE",
    )
