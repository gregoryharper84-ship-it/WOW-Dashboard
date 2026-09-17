"""Machine-readable closure state for WOW V17 autonomous engineering.

Workflow governance only. This module cannot execute wagers, alter model math,
or authorize production action. can_execute=false is invariant.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

NONTERMINAL_STATES = {
    "ACTIVE", "REPRODUCING", "PATCHING", "TESTING", "CI_WAIT", "CI_REWORK",
    "MERGING", "DEPLOYING", "DEPLOY_REWORK", "ACCEPTANCE", "ACCEPTANCE_REWORK",
    "RELEASE_OBSERVABILITY",
}
TERMINAL_STATES = {"FIXED_VERIFIED", "HARD_BLOCKED"}
ALL_STATES = NONTERMINAL_STATES | TERMINAL_STATES
LEGAL_TRANSITIONS = {
    "ACTIVE": {"REPRODUCING", "HARD_BLOCKED"},
    "REPRODUCING": {"PATCHING", "TESTING", "HARD_BLOCKED"},
    "PATCHING": {"TESTING", "HARD_BLOCKED"},
    "TESTING": {"PATCHING", "CI_WAIT", "CI_REWORK", "HARD_BLOCKED"},
    "CI_WAIT": {"CI_REWORK", "MERGING", "HARD_BLOCKED"},
    "CI_REWORK": {"PATCHING", "TESTING", "CI_WAIT", "HARD_BLOCKED"},
    "MERGING": {"DEPLOYING", "ACCEPTANCE", "RELEASE_OBSERVABILITY", "HARD_BLOCKED"},
    "DEPLOYING": {"DEPLOY_REWORK", "ACCEPTANCE", "HARD_BLOCKED"},
    "DEPLOY_REWORK": {"DEPLOYING", "HARD_BLOCKED"},
    "ACCEPTANCE": {"ACCEPTANCE_REWORK", "FIXED_VERIFIED", "HARD_BLOCKED"},
    "ACCEPTANCE_REWORK": {"PATCHING", "TESTING", "DEPLOYING", "ACCEPTANCE", "HARD_BLOCKED"},
    "RELEASE_OBSERVABILITY": {"DEPLOYING", "ACCEPTANCE", "FIXED_VERIFIED", "HARD_BLOCKED"},
    "FIXED_VERIFIED": set(), "HARD_BLOCKED": set(),
}
HARD_BLOCK_FIELDS = {
    "stop_reason", "first_blocked_operation", "exact_error_or_status",
    "missing_capability_or_authority", "work_completed_before_block", "smallest_next_action",
}

@dataclass(frozen=True)
class ClosureState:
    incident_id: str
    risk: str
    state: str
    head_sha: str | None = None
    merge_sha: str | None = None
    deployed_sha: str | None = None
    acceptance_replay: str | None = None
    blocker: dict[str, Any] | None = None
    can_execute: bool = False
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

def is_terminal(state: str) -> bool:
    if state not in ALL_STATES:
        raise ValueError(f"invalid closure state: {state}")
    return state in TERMINAL_STATES

def transition_allowed(current: str, target: str) -> bool:
    return current in ALL_STATES and target in ALL_STATES and target in LEGAL_TRANSITIONS[current]

def validate_closure(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    state = payload.get("state")
    if state not in ALL_STATES:
        errors.append(f"invalid state: {state!r}")
    if payload.get("can_execute") is not False:
        errors.append("can_execute must remain false")
    if not str(payload.get("incident_id") or "").strip():
        errors.append("incident_id is required")
    if payload.get("risk") not in {"R0", "R1", "R2-restorative", "R2", "R3"}:
        errors.append("valid risk classification is required")
    if state == "FIXED_VERIFIED":
        if not payload.get("merge_sha"):
            errors.append("FIXED_VERIFIED requires merge_sha")
        if not payload.get("acceptance_replay"):
            errors.append("FIXED_VERIFIED requires acceptance_replay")
    if state == "HARD_BLOCKED":
        blocker = payload.get("blocker")
        if not isinstance(blocker, dict):
            errors.append("HARD_BLOCKED requires blocker object")
        else:
            missing = sorted(HARD_BLOCK_FIELDS - set(blocker))
            if missing:
                errors.append("HARD_BLOCKED missing blocker fields: " + ", ".join(missing))
            for field in HARD_BLOCK_FIELDS:
                if field in blocker and not str(blocker.get(field) or "").strip():
                    errors.append(f"HARD_BLOCKED blocker field {field} must be non-empty")
    elif payload.get("blocker"):
        errors.append("blocker is only valid for HARD_BLOCKED")
    return errors

def require_terminal(payload: dict[str, Any]) -> None:
    errors = validate_closure(payload)
    if errors:
        raise ValueError("; ".join(errors))
    state = str(payload["state"])
    if state not in TERMINAL_STATES:
        raise RuntimeError(f"incident is nonterminal: {state}")
