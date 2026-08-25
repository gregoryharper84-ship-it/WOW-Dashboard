"""
gate_engine/universal_agent/capability_boundary.py
WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1 / Phase B0

Common Capability Boundary — lane-agnostic generalization of
gate_engine/kalshi_wx_shadow_capability_boundary.py.

Key patterns preserved from the Weather build:
- Deny-by-default: if a tool is not in an agent's explicit allowlist, it is denied.
  An agent with no registered allowlist has zero allowed tools.
- Pre-hook BLOCKS before execution: a denied pre-hook means the tool call is
  never made. Return value .blocked must be checked before proceeding.
- Post-hook SCANS after execution: output is already produced; a violation is
  recorded and returned for the caller to handle (mark BLOCKED in results).
- Recursive forbidden key scan at any nesting depth (shared from output_contract).
- from_registry_entries() builds the boundary from AgentRegistryEntry objects,
  so the allowlist and registry stay in sync without manual duplication.

FORBIDDEN_GOVERNANCE_KEYS is imported from output_contract — single source of truth.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Union

from gate_engine.universal_agent.output_contract import (
    FORBIDDEN_GOVERNANCE_KEYS,
    _scan_forbidden_keys,
    OutputContractViolation,
)


# ── Hook result types ─────────────────────────────────────────────────────────

class HookStatus:
    ALLOWED                    = "ALLOWED"
    DENIED_TOOL_NOT_PERMITTED  = "DENIED_TOOL_NOT_PERMITTED"
    DENIED_FORBIDDEN_KEY       = "DENIED_FORBIDDEN_KEY"
    DENIED_AGENT_NOT_REGISTERED = "DENIED_AGENT_NOT_REGISTERED"
    SCAN_ERROR                 = "SCAN_ERROR"


@dataclass(frozen=True)
class PreHookResult:
    """
    Result of pre_tool_use_hook().

    blocked=True  → tool call must NOT be executed.
    blocked=False → proceed with tool execution.

    The caller is responsible for checking .blocked before executing.
    """
    status:  str
    message: str = ""
    blocked: bool = False


@dataclass(frozen=True)
class PostHookResult:
    """
    Result of post_tool_use_hook().

    A violation does not retroactively block the already-completed tool call.
    The caller should record the violation and mark the work unit as BLOCKED.
    """
    status:    str
    message:   str = ""
    violation: Optional[OutputContractViolation] = None

    @property
    def passed(self) -> bool:
        return self.status == HookStatus.ALLOWED


# ── Capability boundary ───────────────────────────────────────────────────────

class UniversalCapabilityBoundary:
    """
    Lane-agnostic capability boundary enforcing:
      1. Per-agent tool allowlists (deny-by-default)
      2. Recursive forbidden governance key scan on tool inputs (pre-hook)
      3. Recursive forbidden governance key scan on tool outputs (post-hook)

    Usage:
      boundary = UniversalCapabilityBoundary({"agent-a": {"tool_x", "tool_y"}})
      pre  = boundary.pre_tool_use_hook("agent-a", "tool_x", tool_input)
      if pre.blocked:
          # do not execute
      else:
          output = execute_tool(...)
          post = boundary.post_tool_use_hook("agent-a", "tool_x", output)
          if not post.passed:
              # mark result as BLOCKED
    """

    def __init__(
        self,
        per_agent_allowed_tools: dict[str, Union[frozenset, set, list]],
    ) -> None:
        """
        per_agent_allowed_tools: {agent_id: collection of tool name strings}

        An agent_id absent from the dict is treated as having no allowed tools
        (deny-all). An agent_id present with an empty set also has no allowed tools.
        """
        self._allowed: dict[str, frozenset[str]] = {
            agent_id: frozenset(tools)
            for agent_id, tools in per_agent_allowed_tools.items()
        }

    @classmethod
    def from_registry_entries(
        cls,
        entries: list,  # list[AgentRegistryEntry]; avoid circular import with string hint
    ) -> "UniversalCapabilityBoundary":
        """
        Build from a list of AgentRegistryEntry objects.
        Each entry's allowed_capabilities becomes that agent's tool allowlist.
        Keeps the capability boundary in sync with the registry.
        """
        return cls({
            entry.agent_id: frozenset(entry.allowed_capabilities)
            for entry in entries
        })

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _is_registered(self, agent_id: str) -> bool:
        """Return True if the agent_id has an entry (even an empty allowlist)."""
        return agent_id in self._allowed

    def _is_tool_allowed(self, agent_id: str, tool_name: str) -> bool:
        """Return True only if tool_name is in this agent's explicit allowlist."""
        return tool_name in self._allowed.get(agent_id, frozenset())

    # ── Public hooks ──────────────────────────────────────────────────────────

    def pre_tool_use_hook(
        self,
        agent_id: str,
        tool_name: str,
        tool_input: Any,
    ) -> PreHookResult:
        """
        Called BEFORE a tool is executed. Blocks on:
          1. agent_id not in registered set (deny-by-default)
          2. tool_name not in agent's explicit allowlist (deny-by-default)
          3. tool_input contains any forbidden governance key (recursive scan)

        Returns PreHookResult; caller MUST check .blocked before executing.
        Fail-closed on scan error (.blocked=True).
        """
        try:
            # 1. Agent must be registered
            if not self._is_registered(agent_id):
                return PreHookResult(
                    status=HookStatus.DENIED_AGENT_NOT_REGISTERED,
                    message=(
                        f"Agent '{agent_id}' is not registered in this capability boundary. "
                        f"Registered agents: {sorted(self._allowed.keys())}"
                    ),
                    blocked=True,
                )

            # 2. Tool must be in the agent's explicit allowlist (deny-by-default)
            if not self._is_tool_allowed(agent_id, tool_name):
                allowed = sorted(self._allowed.get(agent_id, frozenset()))
                return PreHookResult(
                    status=HookStatus.DENIED_TOOL_NOT_PERMITTED,
                    message=(
                        f"Tool '{tool_name}' is not in the allowlist for agent '{agent_id}'. "
                        f"Allowed: {allowed}"
                    ),
                    blocked=True,
                )

            # 3. Recursive forbidden key scan on tool_input
            if isinstance(tool_input, dict):
                violation = _scan_forbidden_keys(tool_input, path="tool_input")
                if violation is not None:
                    return PreHookResult(
                        status=HookStatus.DENIED_FORBIDDEN_KEY,
                        message=(
                            f"Forbidden governance key in tool input for agent '{agent_id}': "
                            f"{violation.message}"
                        ),
                        blocked=True,
                    )

            return PreHookResult(status=HookStatus.ALLOWED, blocked=False)

        except Exception as exc:  # noqa: BLE001
            return PreHookResult(
                status=HookStatus.SCAN_ERROR,
                message=f"Pre-hook scan raised unexpected error: {exc}",
                blocked=True,   # Fail closed
            )

    def post_tool_use_hook(
        self,
        agent_id: str,
        tool_name: str,
        tool_output: Any,
    ) -> PostHookResult:
        """
        Called AFTER a tool executes. Scans tool_output for forbidden keys.

        Does NOT block the already-completed call; returns a violation record
        for the caller to handle (e.g., mark work unit as BLOCKED and clear
        the tool output from persistence — the Weather outer enforcement pattern).

        Returns PostHookResult with status ALLOWED if clean, DENIED_FORBIDDEN_KEY
        if a governance key is found, SCAN_ERROR on unexpected exception.
        """
        try:
            if not isinstance(tool_output, dict):
                return PostHookResult(
                    status=HookStatus.DENIED_FORBIDDEN_KEY,
                    message=(
                        f"Tool output from agent '{agent_id}' tool '{tool_name}' "
                        f"must be a dict, got {type(tool_output).__name__}"
                    ),
                )

            violation = _scan_forbidden_keys(tool_output, path="tool_output")
            if violation is not None:
                return PostHookResult(
                    status=HookStatus.DENIED_FORBIDDEN_KEY,
                    message=(
                        f"Forbidden governance key in tool output from agent '{agent_id}': "
                        f"{violation.message}"
                    ),
                    violation=violation,
                )

            return PostHookResult(status=HookStatus.ALLOWED)

        except Exception as exc:  # noqa: BLE001
            return PostHookResult(
                status=HookStatus.SCAN_ERROR,
                message=f"Post-hook scan raised unexpected error: {exc}",
            )

    def allowed_tools_for_agent(self, agent_id: str) -> frozenset[str]:
        """Return the set of allowed tool names (empty frozenset = deny-all)."""
        return self._allowed.get(agent_id, frozenset())

    def all_registered_agents(self) -> list[str]:
        """Return all agent IDs registered in this boundary."""
        return sorted(self._allowed.keys())

    def __repr__(self) -> str:
        return (
            f"UniversalCapabilityBoundary("
            f"{{{', '.join(f'{k}: {sorted(v)}' for k, v in sorted(self._allowed.items()))}}})"
        )
