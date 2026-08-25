"""
gate_engine/universal_agent/agent_registry.py
WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1 / Phase B0

Shared Agent Registry — tracks registered universal agents with hard-wired
advisory_only=True that cannot be overridden by any registry entry.

Design decisions (from Weather shadow pilot lessons):
- advisory_only is a class-level read-only property, not a dataclass field.
  Any attempt to set it to False raises AttributeError immediately.
  This matches how CAN_EXECUTE=False is hardcoded in KalshiWxShadowResearchClient
  and cannot be altered by a subclass setting the constant.
- model is stored as a (module, attribute_name) reference pair, read via
  resolve_model() at call time — not a hardcoded string literal. This ensures
  future model changes propagate without code changes (Weather Step 14D fix 1).
- Arbitrary role and lane strings are accepted — no closed enum dependency on B1.
  B1 role names are forward-declared as AgentRole constants for documentation.
- deny_unregistered() in AgentRegistry ensures fail-closed on unknown agent IDs.
"""
from __future__ import annotations

import types
from typing import Any, Optional


# ── B1 role name constants (forward-declared, B1 not yet built) ───────────────
# These are documentation/tab-completion only; arbitrary strings are accepted.

class AgentRole:
    FORECAST_CONTEXT       = "FORECAST_CONTEXT"
    SOURCE_RECONCILIATION  = "SOURCE_RECONCILIATION"
    CONTRADICTION_DETECTOR = "CONTRADICTION_DETECTOR"
    UNUSUAL_REGIME         = "UNUSUAL_REGIME"
    UNCERTAINTY_EXPLAINER  = "UNCERTAINTY_EXPLAINER"
    SUMMARY_SYNTHESIZER    = "SUMMARY_SYNTHESIZER"


# ── Budget configuration ──────────────────────────────────────────────────────

class BudgetConfig:
    """
    Per-agent budget limits. Immutable once constructed.
    All fields use explicit defaults; callers may override any subset.
    """
    __slots__ = (
        "max_input_tokens", "max_output_tokens",
        "max_cost_usd", "timeout_s",
    )

    def __init__(
        self,
        *,
        max_input_tokens: int   = 8_000,
        max_output_tokens: int  = 1_000,
        max_cost_usd: float     = 0.10,
        timeout_s: int          = 90,
    ) -> None:
        object.__setattr__(self, "max_input_tokens",  max_input_tokens)
        object.__setattr__(self, "max_output_tokens", max_output_tokens)
        object.__setattr__(self, "max_cost_usd",      max_cost_usd)
        object.__setattr__(self, "timeout_s",         timeout_s)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("BudgetConfig is immutable after construction.")

    def __repr__(self) -> str:
        return (
            f"BudgetConfig(max_input_tokens={self.max_input_tokens}, "
            f"max_output_tokens={self.max_output_tokens}, "
            f"max_cost_usd={self.max_cost_usd}, timeout_s={self.timeout_s})"
        )


# ── Agent registry entry ──────────────────────────────────────────────────────

class AgentRegistryEntry:
    """
    One registered universal agent.

    advisory_only is a read-only property hardcoded to True.
    It cannot be set to False by any caller, subclass, or registry operation.
    Attempting to set it raises AttributeError.

    model_module + model_attr encode a module-level attribute reference.
    Call resolve_model() at execution time to read the current model string
    (Weather Step 14D fix 1 pattern: never hardcode the model literal).
    """

    def __init__(
        self,
        *,
        agent_id: str,
        role: str,
        lane: str,
        allowed_capabilities: list[str],
        input_schema_ref: str,
        output_schema_ref: str,
        model_module: Optional[types.ModuleType] = None,
        model_attr: str = "_MODEL",
        budget: Optional[BudgetConfig] = None,
    ) -> None:
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError("agent_id must be a non-empty string")
        if not isinstance(role, str) or not role.strip():
            raise ValueError("role must be a non-empty string")
        if not isinstance(lane, str) or not lane.strip():
            raise ValueError("lane must be a non-empty string")

        self._agent_id            = agent_id
        self._role                = role
        self._lane                = lane
        self._allowed_capabilities = list(allowed_capabilities)
        self._input_schema_ref    = input_schema_ref
        self._output_schema_ref   = output_schema_ref
        self._model_module        = model_module
        self._model_attr          = model_attr
        self._budget              = budget if budget is not None else BudgetConfig()

    # ── Public properties (read-only) ─────────────────────────────────────────

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def role(self) -> str:
        return self._role

    @property
    def lane(self) -> str:
        return self._lane

    @property
    def allowed_capabilities(self) -> list[str]:
        return list(self._allowed_capabilities)  # defensive copy

    @property
    def input_schema_ref(self) -> str:
        return self._input_schema_ref

    @property
    def output_schema_ref(self) -> str:
        return self._output_schema_ref

    @property
    def model_module(self) -> Optional[types.ModuleType]:
        return self._model_module

    @property
    def model_attr(self) -> str:
        return self._model_attr

    @property
    def budget(self) -> BudgetConfig:
        return self._budget

    @property
    def advisory_only(self) -> bool:
        """
        ALWAYS True. This property has no setter.
        Any attempt to assign advisory_only raises AttributeError.
        """
        return True

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "advisory_only":
            raise AttributeError(
                "advisory_only is a fixed constant (True) on AgentRegistryEntry "
                "and cannot be set. This matches the CAN_EXECUTE=False hardcoding "
                "pattern from KalshiWxShadowResearchClient."
            )
        super().__setattr__(name, value)

    # ── Model resolution ──────────────────────────────────────────────────────

    def resolve_model(self) -> Optional[str]:
        """
        Return the model identifier by reading model_module.<model_attr> at
        call time, not at registration time. If model_module is None, return None.

        This is the Weather Step 14D fix 1 pattern: store a module reference,
        read the attribute dynamically so future model changes propagate without
        code changes (previously the model was hardcoded as None in call_one_agent).
        """
        if self._model_module is None:
            return None
        return getattr(self._model_module, self._model_attr, None)

    def __repr__(self) -> str:
        return (
            f"AgentRegistryEntry(agent_id={self._agent_id!r}, role={self._role!r}, "
            f"lane={self._lane!r}, advisory_only=True)"
        )


# ── Registry ──────────────────────────────────────────────────────────────────

class AgentRegistry:
    """
    Lane-agnostic registry of universal agents.

    Only registered agents are accepted for execution.
    Attempting to get() an unregistered agent_id raises KeyError (fail-closed).
    """

    def __init__(self) -> None:
        self._entries: dict[str, AgentRegistryEntry] = {}

    def register(self, entry: AgentRegistryEntry) -> None:
        """
        Register an agent. Raises:
          KeyError   — if agent_id is already registered (no silent overwrite)
          ValueError — if advisory_only is somehow not True (fail-closed guard)
        """
        if entry.agent_id in self._entries:
            raise KeyError(
                f"Agent '{entry.agent_id}' is already registered. "
                f"Unregister it first if replacement is intentional."
            )
        if not entry.advisory_only:
            # Should never happen given the property hardcoding, but fail-closed.
            raise ValueError(
                f"Agent '{entry.agent_id}' has advisory_only != True — rejected. "
                f"All universal agents must be advisory-only."
            )
        self._entries[entry.agent_id] = entry

    def unregister(self, agent_id: str) -> None:
        """Remove a registered agent (for testing / reconfiguration only)."""
        if agent_id not in self._entries:
            raise KeyError(f"Agent '{agent_id}' is not registered.")
        del self._entries[agent_id]

    def get(self, agent_id: str) -> AgentRegistryEntry:
        """Return the entry for agent_id. Raises KeyError if not registered."""
        if agent_id not in self._entries:
            raise KeyError(
                f"Agent '{agent_id}' is not registered. "
                f"Only registered agents may execute. Registered: {sorted(self._entries)}"
            )
        return self._entries[agent_id]

    def is_registered(self, agent_id: str) -> bool:
        return agent_id in self._entries

    def all_agents(self) -> list[AgentRegistryEntry]:
        return list(self._entries.values())

    def agents_for_lane(self, lane: str) -> list[AgentRegistryEntry]:
        return [e for e in self._entries.values() if e.lane == lane]

    def agents_for_role(self, role: str) -> list[AgentRegistryEntry]:
        return [e for e in self._entries.values() if e.role == role]

    def __len__(self) -> int:
        return len(self._entries)

    def __repr__(self) -> str:
        return f"AgentRegistry({sorted(self._entries.keys())})"


# ── Module-level singleton registry ──────────────────────────────────────────
# The module-level REGISTRY is the shared instance. Tests that need isolation
# should instantiate their own AgentRegistry() rather than mutating this one.
REGISTRY: AgentRegistry = AgentRegistry()
