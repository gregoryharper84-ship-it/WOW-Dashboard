"""
gate_engine/universal_agent/role_runner.py
WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1 / Phase B2

RoleRunner status constants and MockRoleRunner test helper.

A RoleRunner is any callable accepting (AgentRegistryEntry, EvidencePacket) and
returning a dict.  It is dependency-injected into run_orchestrator() — the
orchestrator never calls Anthropic, OpenAI, or any external API directly.

Production callers supply a concrete implementation (B3+).
Test harnesses use MockRoleRunner to provide preset payloads without live calls.

No app.py import, no Flask route, no live API call, no Weather code.
"""
from __future__ import annotations

from typing import Any, Optional

from gate_engine.universal_agent.agent_registry import AgentRegistryEntry
from gate_engine.universal_agent.evidence_packet import EvidencePacket


# ── Status constants ──────────────────────────────────────────────────────────

class RoleRunnerStatus:
    """
    Outcome classification for one role execution attempt.

    ACCEPTED            — Runner produced valid output; passed B0 + B1 validation.
    INVALID             — Runner output failed the B1 role-specific validator.
    GOVERNANCE_REJECTED — Runner output contained a forbidden governance key
                          (caught by B0 post-hook recursive scan).
    RUNNER_FAILED       — Runner raised an exception or returned a non-dict.
    SKIPPED_RESUMED     — Work unit already completed in a prior run; skipped
                          for idempotence (uac_run_resumability record exists).
    NO_RUNNER           — No runner callable was registered for this agent_id.
                          Fail-closed: never silently ACCEPTED.
    BOUNDARY_BLOCKED    — B0 pre-hook blocked execution before runner was called.
    """
    ACCEPTED            = "ACCEPTED"
    INVALID             = "INVALID"
    GOVERNANCE_REJECTED = "GOVERNANCE_REJECTED"
    RUNNER_FAILED       = "RUNNER_FAILED"
    SKIPPED_RESUMED     = "SKIPPED_RESUMED"
    NO_RUNNER           = "NO_RUNNER"
    BOUNDARY_BLOCKED    = "BOUNDARY_BLOCKED"

    @classmethod
    def all_statuses(cls) -> frozenset:
        return frozenset({
            cls.ACCEPTED, cls.INVALID, cls.GOVERNANCE_REJECTED,
            cls.RUNNER_FAILED, cls.SKIPPED_RESUMED, cls.NO_RUNNER,
            cls.BOUNDARY_BLOCKED,
        })


# ── Mock runner for tests ──────────────────────────────────────────────────────

class MockRoleRunner:
    """
    Deterministic, test-only role runner.

    Usage::

        runner = MockRoleRunner(presets={
            "uac-data-slate-integrity-v1": valid_data_slate_integrity_payload(),
            "uac-news-status-v1": RuntimeError("simulated failure"),
            ...
        })
        result = run_orchestrator(packet, registry, role_runners={
            entry.agent_id: runner for entry in registry.all_agents()
        })

    Preset values:
      dict      — returned as the raw output payload.
      Exception — raised (orchestrator catches → RUNNER_FAILED).

    If agent_id is not in presets, raises RuntimeError
    (simulates a runner that fails for an unknown agent).

    call_log records every invocation with agent_id and Python id() of the
    packet — tests can assert that all six roles received the SAME packet object.
    """

    def __init__(self, presets: Optional[dict[str, Any]] = None) -> None:
        self._presets: dict[str, Any] = dict(presets or {})
        self.call_log: list[dict[str, Any]] = []

    def __call__(
        self,
        entry: AgentRegistryEntry,
        packet: EvidencePacket,
    ) -> dict:
        """
        Return the preset for entry.agent_id, or raise if not registered.
        Records packet identity for test assertions.
        """
        self.call_log.append({
            "agent_id":    entry.agent_id,
            "packet_id":   id(packet),
            "snapshot_id": packet.snapshot_id,
        })
        if entry.agent_id not in self._presets:
            raise RuntimeError(
                f"MockRoleRunner: no preset for agent_id={entry.agent_id!r}. "
                f"Register a preset in MockRoleRunner(presets={{...}})."
            )
        result = self._presets[entry.agent_id]
        if isinstance(result, Exception):
            raise result
        return result

    def packet_ids_seen(self) -> list[int]:
        """Return the Python id() of every packet received, in call order."""
        return [e["packet_id"] for e in self.call_log]

    def snapshot_ids_seen(self) -> list[str]:
        """Return snapshot_id of every packet received, in call order."""
        return [e["snapshot_id"] for e in self.call_log]

    def agents_called(self) -> list[str]:
        """Return agent_ids in call order."""
        return [e["agent_id"] for e in self.call_log]
