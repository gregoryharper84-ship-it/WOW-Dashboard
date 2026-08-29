"""Deterministic terminal ceiling reducer (packet section 16).

Ordinary code, exhaustively tested — never an LLM call, never a vote among
workers. Inputs are the required workers' terminal job results for one
candidate; output is one immutable native terminal label.

Unknown ceilings fail closed as GOVERNANCE_LABEL_UNKNOWN rather than being
silently accepted or defaulted to the loosest ceiling — an unrecognized
ceiling string is far more likely a registry/config typo than a genuinely
new, unratified state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from agent_runtime.state_machine import JOB_TERMINAL_STATES

# Explicit ordered enum, strictest first. Never compare ceilings lexically —
# "MODEL_QUALIFIED_HOLD" sorting before "RESEARCH_INTEREST" alphabetically
# would silently invert the intended strictness ordering.
CEILING_ORDER: tuple[str, ...] = (
    "REJECT",
    "GOVERNANCE_LABEL_UNKNOWN",
    "MODEL_UNAVAILABLE",
    "NO_SPECIALIST_COVERAGE",
    "RUN_NOT_TERMINAL",
    "HOLD",
    "MODEL_QUALIFIED_HOLD",
    "MARKET_VERIFIED_HOLD",
    "ADVISORY",
    "RESEARCH_INTEREST",
    "WATCH",
    "VERIFIED",
    "MARKET_VERIFIED",
    "FINAL_APPROVED",
)

_CEILING_RANK = {name: index for index, name in enumerate(CEILING_ORDER)}


def strictest(ceilings: list[str]) -> str:
    """Return the strictest (lowest-rank) ceiling in the list. An unknown
    ceiling is treated as maximally strict — see module docstring."""
    if not ceilings:
        return "GOVERNANCE_LABEL_UNKNOWN"
    ranked = sorted(
        ceilings,
        key=lambda c: _CEILING_RANK.get(c, -1),
    )
    return ranked[0]


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


def reduce_candidate(
    *,
    controlling_worker_id: Optional[str],
    controlling_job_status: Optional[str],
    required_jobs: list[RequiredJobResult],
) -> ReducedDecision:
    """Packet section 16 pseudocode, implemented.

    Order of checks matters: a not-yet-terminal required job blocks any
    decision (RUN_NOT_TERMINAL is not itself a publishable label — callers
    must not persist it as a terminal_decision row); a candidate with no
    routed controlling specialist can never reach a probability regardless of
    what other workers found; a controlling job that didn't succeed means
    MODEL_UNAVAILABLE even if every other worker passed.
    """
    if any(job.status not in JOB_TERMINAL_STATES for job in required_jobs):
        return ReducedDecision(
            label="RUN_NOT_TERMINAL", ceiling="RUN_NOT_TERMINAL",
            blockers=(), probability_publishable=False,
        )

    if controlling_worker_id is None:
        return ReducedDecision(
            label="NO_SPECIALIST_COVERAGE", ceiling="NO_SPECIALIST_COVERAGE",
            blockers=(), probability_publishable=False,
        )

    if controlling_job_status != "SUCCEEDED":
        return ReducedDecision(
            label="MODEL_UNAVAILABLE", ceiling="MODEL_UNAVAILABLE",
            blockers=(), probability_publishable=False,
        )

    all_blockers: list[str] = []
    for job in required_jobs:
        all_blockers.extend(job.blockers)
    # Stable de-duplication (dict.fromkeys preserves first-seen order).
    blockers = tuple(dict.fromkeys(all_blockers))

    known_ceilings = [job.ceiling for job in required_jobs if job.ceiling is not None]
    ceiling = strictest(known_ceilings) if known_ceilings else "GOVERNANCE_LABEL_UNKNOWN"

    if ceiling == "GOVERNANCE_LABEL_UNKNOWN" or ceiling not in _CEILING_RANK:
        return ReducedDecision(
            label="GOVERNANCE_LABEL_UNKNOWN", ceiling="GOVERNANCE_LABEL_UNKNOWN",
            blockers=blockers, probability_publishable=False,
        )

    publishable = ceiling in {"VERIFIED", "MARKET_VERIFIED", "FINAL_APPROVED"} and not blockers
    return ReducedDecision(
        label=ceiling, ceiling=ceiling, blockers=blockers,
        probability_publishable=publishable,
    )
