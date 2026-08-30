"""Run and job state machines (WOW-AGENT-RUNTIME-V1 packet section 5).

Only the orchestrator may transition run state. Workers transition their own
job state but cannot mark a run complete — that split is enforced by which
module calls which function here, not by anything in this file alone;
agent_runtime_api.py is the only caller of transition_run().
"""
from __future__ import annotations

from dataclasses import dataclass


class IllegalTransitionError(ValueError):
    """Raised when a state transition is not in the allowed map. Fail closed:
    callers must not coerce this into a silent no-op or a best-guess state."""


RUN_STATES = frozenset({
    "CREATED", "VALIDATING_REQUEST", "DISCOVERY_QUEUED", "DISCOVERY_RUNNING",
    "ROUTING", "EVIDENCE_QUEUED", "EVIDENCE_RUNNING", "MODELING_QUEUED",
    "MODELING_RUNNING", "AUDIT_QUEUED", "AUDIT_RUNNING", "FINAL_REFRESH",
    "RECONCILING", "COMPLETED", "COMPLETED_WITH_BLOCKERS", "FAILED", "CANCELED",
})

RUN_TERMINAL_STATES = frozenset({"COMPLETED", "COMPLETED_WITH_BLOCKERS", "FAILED", "CANCELED"})

# Linear happy path plus the escape hatches every stage needs: FAILED and
# CANCELED are reachable from any non-terminal state (an infrastructure
# failure or an administrative cancel can happen at any stage), and
# RECONCILING is where COMPLETED vs COMPLETED_WITH_BLOCKERS is decided.
#
# The direct-to-RECONCILING edges (ROUTING, EVIDENCE_RUNNING, MODELING_RUNNING,
# AUDIT_RUNNING) and VALIDATING_REQUEST -> ROUTING exist for the case where a
# stage produces zero downstream work — e.g. discovery finds no candidates, or
# discovery is disabled and the caller supplied no candidate_inputs either.
# Without them a run with nothing to do could never reach a terminal state
# without an illegal-transition error. Adopted from PR #33
# (feature/wow-agent-runtime-v1) during the convergence pass, which built the
# real coordinator that first needed this and found the gap.
_RUN_TRANSITIONS: dict[str, frozenset[str]] = {
    "CREATED": frozenset({"VALIDATING_REQUEST", "FAILED", "CANCELED"}),
    "VALIDATING_REQUEST": frozenset({"DISCOVERY_QUEUED", "ROUTING", "FAILED", "CANCELED"}),
    "DISCOVERY_QUEUED": frozenset({"DISCOVERY_RUNNING", "FAILED", "CANCELED"}),
    "DISCOVERY_RUNNING": frozenset({"ROUTING", "FAILED", "CANCELED"}),
    "ROUTING": frozenset({"EVIDENCE_QUEUED", "RECONCILING", "FAILED", "CANCELED"}),
    "EVIDENCE_QUEUED": frozenset({"EVIDENCE_RUNNING", "FAILED", "CANCELED"}),
    "EVIDENCE_RUNNING": frozenset({"MODELING_QUEUED", "RECONCILING", "FAILED", "CANCELED"}),
    "MODELING_QUEUED": frozenset({"MODELING_RUNNING", "FAILED", "CANCELED"}),
    "MODELING_RUNNING": frozenset({"AUDIT_QUEUED", "RECONCILING", "FAILED", "CANCELED"}),
    "AUDIT_QUEUED": frozenset({"AUDIT_RUNNING", "FAILED", "CANCELED"}),
    "AUDIT_RUNNING": frozenset({"FINAL_REFRESH", "RECONCILING", "FAILED", "CANCELED"}),
    "FINAL_REFRESH": frozenset({"RECONCILING", "FAILED", "CANCELED"}),
    "RECONCILING": frozenset({"COMPLETED", "COMPLETED_WITH_BLOCKERS", "FAILED", "CANCELED"}),
    "COMPLETED": frozenset(),
    "COMPLETED_WITH_BLOCKERS": frozenset(),
    "FAILED": frozenset(),
    "CANCELED": frozenset(),
}

JOB_STATES = frozenset({
    "QUEUED", "RUNNING", "SUCCEEDED", "BLOCKED", "REJECTED", "TIMED_OUT",
    "RETRY_PENDING", "DEAD_LETTERED", "CANCELED",
})

JOB_NONTERMINAL_STATES = frozenset({"QUEUED", "RUNNING", "RETRY_PENDING"})
JOB_TERMINAL_STATES = JOB_STATES - JOB_NONTERMINAL_STATES

_JOB_TRANSITIONS: dict[str, frozenset[str]] = {
    "QUEUED": frozenset({"RUNNING", "CANCELED", "DEAD_LETTERED"}),
    "RUNNING": frozenset({
        "SUCCEEDED", "BLOCKED", "REJECTED", "TIMED_OUT", "RETRY_PENDING", "CANCELED",
    }),
    "RETRY_PENDING": frozenset({"QUEUED", "DEAD_LETTERED", "CANCELED"}),
    "SUCCEEDED": frozenset(),
    "BLOCKED": frozenset(),
    "REJECTED": frozenset(),
    "TIMED_OUT": frozenset(),
    "DEAD_LETTERED": frozenset(),
    "CANCELED": frozenset(),
}


@dataclass(frozen=True)
class TransitionCheck:
    allowed: bool
    current: str
    next: str


def check_run_transition(current: str, next_state: str) -> TransitionCheck:
    if current not in RUN_STATES:
        raise IllegalTransitionError(f"Unknown run state {current!r}")
    if next_state not in RUN_STATES:
        raise IllegalTransitionError(f"Unknown run state {next_state!r}")
    allowed = next_state in _RUN_TRANSITIONS[current]
    return TransitionCheck(allowed=allowed, current=current, next=next_state)


def assert_run_transition(current: str, next_state: str) -> None:
    result = check_run_transition(current, next_state)
    if not result.allowed:
        raise IllegalTransitionError(
            f"Run cannot transition {current!r} -> {next_state!r}"
        )


def check_job_transition(current: str, next_state: str) -> TransitionCheck:
    if current not in JOB_STATES:
        raise IllegalTransitionError(f"Unknown job state {current!r}")
    if next_state not in JOB_STATES:
        raise IllegalTransitionError(f"Unknown job state {next_state!r}")
    allowed = next_state in _JOB_TRANSITIONS[current]
    return TransitionCheck(allowed=allowed, current=current, next=next_state)


def assert_job_transition(current: str, next_state: str) -> None:
    result = check_job_transition(current, next_state)
    if not result.allowed:
        raise IllegalTransitionError(
            f"Job cannot transition {current!r} -> {next_state!r}"
        )
