"""
gate_engine/universal_agent/role_result.py
WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1 / Phase B2

RoleResult: immutable record of one advisory role's execution outcome.

Frozen dataclass — produced by _run_one_role() in orchestrator.py and
consumed by bundle_assembler.py, contradiction_detector.py, and persistence.
Never mutated after construction.

No app.py import, no Flask route, no live API call, no Weather code.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RoleResult:
    """
    Immutable record of one advisory role execution outcome.

    Fields
    ------
    agent_id
        agent_id from the AgentRegistryEntry.
    role_id
        role string from the AgentRegistryEntry (e.g. "DATA_SLATE_INTEGRITY").
    status
        RoleRunnerStatus.* constant describing the outcome.
    raw_output
        The dict returned by the runner. None if the runner raised, returned
        non-dict, or was not called (NO_RUNNER / BOUNDARY_BLOCKED / SKIPPED_RESUMED).
    advisory_findings
        The advisory_findings sub-dict from raw_output. Only populated when
        status == ACCEPTED. None in all other cases.
    violation_code
        OutputViolationCode or RoleViolationCode string when validation failed.
        None on ACCEPTED or SKIPPED_RESUMED.
    violation_message
        Human-readable description of the violation. None on ACCEPTED.
    latency_ms
        Runner wall-clock latency in milliseconds. None when runner not called.
    error_message
        Exception message when status == RUNNER_FAILED. None otherwise.
    """
    agent_id:          str
    role_id:           str
    status:            str
    raw_output:        Optional[dict]
    advisory_findings: Optional[dict]
    violation_code:    Optional[str]
    violation_message: Optional[str]
    latency_ms:        Optional[int]
    error_message:     Optional[str]

    @property
    def accepted(self) -> bool:
        """True when status is ACCEPTED (output validated and usable)."""
        from gate_engine.universal_agent.role_runner import RoleRunnerStatus
        return self.status == RoleRunnerStatus.ACCEPTED

    @property
    def effectively_accepted(self) -> bool:
        """
        True when status is ACCEPTED or SKIPPED_RESUMED.
        SKIPPED_RESUMED means the role was accepted in a prior run; for bundle
        assembly purposes it counts as a completed role.
        """
        from gate_engine.universal_agent.role_runner import RoleRunnerStatus
        return self.status in (
            RoleRunnerStatus.ACCEPTED,
            RoleRunnerStatus.SKIPPED_RESUMED,
        )

    @property
    def failed(self) -> bool:
        """True when status is anything other than ACCEPTED or SKIPPED_RESUMED."""
        return not self.effectively_accepted

    def to_dict(self) -> dict:
        return {
            "agent_id":          self.agent_id,
            "role_id":           self.role_id,
            "status":            self.status,
            "advisory_findings": self.advisory_findings,
            "violation_code":    self.violation_code,
            "violation_message": self.violation_message,
            "latency_ms":        self.latency_ms,
            "error_message":     self.error_message,
        }
