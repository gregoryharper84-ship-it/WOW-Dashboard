"""Deterministic terminal ceiling reducer for the WOW Agent Runtime.

Worker ``authority_ceiling`` values answer a permission question: how far a
worker is allowed to advance a row. They are not, by themselves, active row
blockers. A blocker-free successful identity worker returning
``IDENTITY_VERIFIED`` proves that stage passed; it must not permanently pin a
row at ``IDENTITY_VERIFIED`` after later mandatory stages also pass.

Only an actual blocker/failure ceiling may lower a candidate from the terminal
state proven by the completed mandatory stage graph. Unknown ceiling labels
fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from agent_runtime.state_machine import JOB_TERMINAL_STATES

# Native candidate terminal ladder, strictest first. Never compare labels
# lexically.
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

# The fixed candidate-stage graph currently wired by Coordinator. Discovery is
# run-scoped and the terminal reducer is downstream of these candidate-scoped
# mandatory stages, so neither belongs in this set.
MANDATORY_CANDIDATE_WORKERS: tuple[str, ...] = (
    "wow.slate-integrity-expert",
    "wow.evidence-hydration",
    "wow.controlling-model",
    "wow.failure-path-framework",
    "wow.dynamic-calibration-expert",
    "wow.exact-line-market-auditor",
    "wow.structure-exposure-governor",
    "wow.final-refresh-governor",
)


def strictest(ceilings: list[str]) -> str:
    """Return the strictest native ceiling; unknown values fail closed."""
    if not ceilings:
        return "GOVERNANCE_LABEL_UNKNOWN"
    if any(ceiling not in _CEILING_RANK for ceiling in ceilings):
        return "GOVERNANCE_LABEL_UNKNOWN"
    return min(ceilings, key=lambda ceiling: _CEILING_RANK[ceiling])


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
    can_execute: bool = False


def _stable_blockers(required_jobs: list[RequiredJobResult]) -> tuple[str, ...]:
    values: list[str] = []
    for job in required_jobs:
        values.extend(str(blocker) for blocker in job.blockers if str(blocker))
    return tuple(dict.fromkeys(values))


def _missing_mandatory_workers(required_jobs: list[RequiredJobResult]) -> tuple[str, ...]:
    present = {job.worker_id for job in required_jobs}
    return tuple(worker_id for worker_id in MANDATORY_CANDIDATE_WORKERS if worker_id not in present)


def reduce_candidate(
    *,
    controlling_worker_id: Optional[str],
    controlling_job_status: Optional[str],
    required_jobs: list[RequiredJobResult],
) -> ReducedDecision:
    """Reduce one fully evaluated candidate to one immutable native label.

    Safety rules:
    - a non-terminal required job cannot produce a terminal decision;
    - missing specialist coverage or a failed controlling specialist can never
      be replaced by another stage;
    - every mandatory candidate stage must be represented before approval;
    - every supplied ceiling label must be recognized;
    - blocker-free successful stages are progression evidence, not active caps;
    - only jobs carrying a real blocker or non-success terminal state contribute
      candidate-limiting ceilings;
    - all mandatory stages succeeding with no blockers proves FINAL_APPROVED.
    """
    if any(job.status not in JOB_TERMINAL_STATES for job in required_jobs):
        return ReducedDecision(
            label="RUN_NOT_TERMINAL",
            ceiling="RUN_NOT_TERMINAL",
            blockers=(),
            probability_publishable=False,
        )

    if controlling_worker_id is None:
        return ReducedDecision(
            label="NO_SPECIALIST_COVERAGE",
            ceiling="NO_SPECIALIST_COVERAGE",
            blockers=(),
            probability_publishable=False,
        )

    if controlling_job_status != "SUCCEEDED":
        return ReducedDecision(
            label="MODEL_UNAVAILABLE",
            ceiling="MODEL_UNAVAILABLE",
            blockers=(),
            probability_publishable=False,
        )

    blockers = _stable_blockers(required_jobs)

    # Registry/config drift is a governance failure even if the affected job
    # otherwise says SUCCEEDED. Never silently ignore an unknown label merely
    # because that stage has no blockers.
    supplied_ceilings = [job.ceiling for job in required_jobs if job.ceiling is not None]
    if any(ceiling not in _CEILING_RANK for ceiling in supplied_ceilings):
        return ReducedDecision(
            label="GOVERNANCE_LABEL_UNKNOWN",
            ceiling="GOVERNANCE_LABEL_UNKNOWN",
            blockers=blockers,
            probability_publishable=False,
        )

    missing = _missing_mandatory_workers(required_jobs)
    if missing:
        missing_blockers = tuple(f"MANDATORY_STAGE_MISSING:{worker_id}" for worker_id in missing)
        return ReducedDecision(
            label="RESEARCH_INTEREST",
            ceiling="RESEARCH_INTEREST",
            blockers=tuple(dict.fromkeys((*blockers, *missing_blockers))),
            probability_publishable=False,
        )

    # A successful blocker-free stage reports the highest label that worker is
    # authorized to prove at its point in the graph. It is NOT an active cap on
    # downstream stages. Real blockers/non-success states are candidate caps.
    limiting_ceilings = [
        job.ceiling
        for job in required_jobs
        if job.ceiling is not None and (job.status != "SUCCEEDED" or bool(job.blockers))
    ]

    if limiting_ceilings:
        ceiling = strictest(limiting_ceilings)
        if ceiling == "GOVERNANCE_LABEL_UNKNOWN":
            return ReducedDecision(
                label="GOVERNANCE_LABEL_UNKNOWN",
                ceiling="GOVERNANCE_LABEL_UNKNOWN",
                blockers=blockers,
                probability_publishable=False,
            )
        return ReducedDecision(
            label=ceiling,
            ceiling=ceiling,
            blockers=blockers,
            probability_publishable=False,
        )

    # The reducer is invoked only after final refresh. With the complete fixed
    # mandatory graph represented, every job terminal+SUCCEEDED, and no blocker,
    # the candidate has proven every runtime gate and may reach the graph's
    # terminal approval label. Execution remains impossible by contract.
    return ReducedDecision(
        label="FINAL_APPROVED",
        ceiling="FINAL_APPROVED",
        blockers=(),
        probability_publishable=True,
        can_execute=False,
    )
