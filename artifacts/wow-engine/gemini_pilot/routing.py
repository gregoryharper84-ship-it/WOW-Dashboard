"""Deterministic research-only admission planner for the Gemini pilot.

A plan is NOT an execution queue. Durable scheduling, role identity and
lease ownership must be integrated into existing WOW control-plane services
in a separate reviewed PR. Defaults to disabled.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

from contracts import PilotTask


@dataclass(frozen=True)
class PlannedTask:
    task_id: str
    lane: str
    disposition: Literal["READY_FOR_READONLY_RESEARCH", "BLOCKED"]
    reason: str
    input_sha256: str


@dataclass(frozen=True)
class PilotPlan:
    plan_id: str
    tasks: tuple[PlannedTask, ...]
    accepted: int
    blocked: int
    can_execute: bool = False


def plan_readonly(
    tasks: list[PilotTask], *, enabled: bool = False,
    max_tasks: int = 25, max_source_chars: int = 500000,
) -> PilotPlan:
    """Pure admission pass. No model calls, DB writes, or operational leases.

    Quotas are hard upper bounds for a proposed batch; daily/monthly provider
    billing quotas must be enforced in the durable orchestration layer.
    """
    if max_tasks < 1 or max_source_chars < 1:
        raise ValueError("INVALID_PILOT_BUDGET")
    if len(tasks) > max_tasks:
        raise ValueError("PILOT_TASK_CAP_EXCEEDED")
    if len({t.task_id for t in tasks}) != len(tasks):
        raise ValueError("DUPLICATE_TASK_ID")
    if sum(len(t.source_material) for t in tasks) > max_source_chars:
        raise ValueError("PILOT_CONTEXT_CAP_EXCEEDED")

    accepted = 0
    planned = []
    for task in tasks:
        digest = sha256(task.model_dump_json().encode("utf-8")).hexdigest()
        source_kinds = {e.kind for e in task.evidence}
        reason = "ADMITTED"
        if not enabled:
            reason = "PILOT_DISABLED"
        elif task.lane == "KALSHI_CONTRACT_AUDIT" and "KALSHI_RULE" not in source_kinds:
            reason = "MISSING_CONTRACT_AUTHORITY"
        elif task.lane == "WOW_DEFECT_DIAGNOSIS" and "GITHUB" not in source_kinds:
            reason = "MISSING_GITHUB_PROVENANCE"
        if reason == "ADMITTED":
            accepted += 1
        planned.append(PlannedTask(
            task_id=task.task_id, lane=task.lane,
            disposition=("READY_FOR_READONLY_RESEARCH" if reason == "ADMITTED"
                         else "BLOCKED"),
            reason=reason, input_sha256=digest,
        ))
    plan_id = sha256("".join(x.input_sha256 for x in planned).encode()).hexdigest()
    return PilotPlan(plan_id=plan_id, tasks=tuple(planned),
                     accepted=accepted, blocked=len(planned) - accepted)
