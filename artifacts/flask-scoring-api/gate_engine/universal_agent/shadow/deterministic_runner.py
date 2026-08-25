"""
gate_engine/universal_agent/shadow/deterministic_runner.py
WOW-PATCH-2026-08-10-UNIVERSAL-AGENT-CORE-V1-B3B

DeterministicAdapterRunner — offline role runner that returns pre-built B1
advisory payloads from the B3A adapter.

Design
------
The B2 orchestrator dependency-injects role runners as a dict[agent_id →
callable(entry, packet) → dict].  DeterministicAdapterRunner is a single
callable object that handles all six roles by looking up the pre-built payload
keyed by entry.role (role_id).  One runner instance covers all six agents.

The runner:
- Returns the pre-built adapter payload for the requested role_id.
- Raises RuntimeError (fail-closed) if no payload is registered for the role.
- Records every (agent_id, packet id()) call in call_log — tests can assert
  that the same EvidencePacket identity is received by all six roles.
- Makes no network, LLM, or external API calls.
- Does NOT validate outputs — validation is handled by the B2 orchestrator's
  B0 post-hook and B1 role-specific validator downstream.

Usage
-----
    adapter_result = MlbMoneylineAdapter().adapt(row=row, run_id="r1")
    runner = DeterministicAdapterRunner(adapter_result.role_payloads)
    registry = build_b1_registry()
    role_runners = runner.build_role_runners(registry)
    orch_result = run_orchestrator(adapter_result.packet, registry, role_runners)

can_execute = False
"""
from __future__ import annotations

from typing import Any, Optional

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
MODULE         = "deterministic_adapter_runner"
VERSION        = "v1.0"


class DeterministicAdapterRunner:
    """
    Offline, deterministic role runner.

    Wraps pre-built B1 advisory payloads produced by MlbMoneylineAdapter
    and exposes them to the B2 orchestrator without any LLM or API call.

    Parameters
    ----------
    role_payloads
        dict[role_id → validated B1 payload dict].  The role_id strings must
        match the `role` attribute of AgentRegistryEntry (e.g.
        "DATA_SLATE_INTEGRITY", "NEWS_STATUS", …).

    Invariants
    ----------
    - can_execute = False
    - No network, LLM, or external API calls at any point.
    - Fail-closed: missing role_id → RuntimeError (never silently returns None).
    - Same callable handles all six B1 roles; role is resolved via entry.role.
    - call_log tracks every invocation with agent_id and packet Python id()
      so tests can assert identity preservation.
    """

    def __init__(
        self,
        role_payloads: dict[str, Any],
    ) -> None:
        if not isinstance(role_payloads, dict):
            raise TypeError(
                f"role_payloads must be a dict, got {type(role_payloads).__name__}"
            )
        self._payloads: dict[str, Any] = dict(role_payloads)
        self.call_log: list[dict[str, Any]] = []

    def __call__(
        self,
        entry: Any,
        packet: Any,
    ) -> dict:
        """
        Return the pre-built payload for entry.role.

        Raises
        ------
        RuntimeError
            If entry.role has no registered payload (fail-closed).
        """
        agent_id = entry.agent_id
        role_id  = entry.role

        self.call_log.append({
            "agent_id":    agent_id,
            "role_id":     role_id,
            "packet_id":   id(packet),
            "snapshot_id": getattr(packet, "snapshot_id", None),
        })

        if role_id not in self._payloads:
            raise RuntimeError(
                f"DeterministicAdapterRunner: no payload registered for "
                f"role_id={role_id!r} (agent_id={agent_id!r}). "
                f"Registered roles: {sorted(self._payloads)}"
            )

        return self._payloads[role_id]

    def build_role_runners(self, registry: Any) -> dict[str, Any]:
        """
        Build the role_runners dict required by run_orchestrator().

        Maps every agent_id in the registry to this runner instance.
        A single DeterministicAdapterRunner handles all six roles by
        dispatching on entry.role in __call__.

        Parameters
        ----------
        registry
            AgentRegistry (B0) with the B1 roles registered.

        Returns
        -------
        dict[agent_id → self]  — one entry per registered agent.
        """
        return {entry.agent_id: self for entry in registry.all_agents()}

    # ── Call-log inspection helpers (for tests) ───────────────────────────────

    def packet_ids_seen(self) -> list[int]:
        """Return the Python id() of every packet received, in call order."""
        return [e["packet_id"] for e in self.call_log]

    def role_ids_called(self) -> list[str]:
        """Return role_ids called, in call order."""
        return [e["role_id"] for e in self.call_log]

    def agent_ids_called(self) -> list[str]:
        """Return agent_ids called, in call order."""
        return [e["agent_id"] for e in self.call_log]

    def snapshot_ids_seen(self) -> list[Optional[str]]:
        """Return snapshot_id of every packet received, in call order."""
        return [e["snapshot_id"] for e in self.call_log]
