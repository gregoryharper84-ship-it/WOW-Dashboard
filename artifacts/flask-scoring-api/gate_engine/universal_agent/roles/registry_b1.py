"""
gate_engine/universal_agent/roles/registry_b1.py
WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1 / Phase B1

B1 Registry — registers exactly the six advisory role entries into an
AgentRegistry instance. Does NOT mutate the module-level REGISTRY singleton
from B0 — callers choose which registry to populate.

Usage:
    registry = build_b1_registry()         # fresh isolated registry
    register_b1_roles(existing_registry)   # inject into any registry

Duplicate registration → KeyError (fail-closed, from AgentRegistry.register).
Unknown agent_id lookup → KeyError (fail-closed, from AgentRegistry.get).
"""
from __future__ import annotations

from gate_engine.universal_agent.agent_registry import AgentRegistry, AgentRegistryEntry

# Import the six canonical registry entries (one per role module).
from gate_engine.universal_agent.roles.data_slate_integrity import (
    REGISTRY_ENTRY as _DSI_ENTRY,
)
from gate_engine.universal_agent.roles.news_status import (
    REGISTRY_ENTRY as _NS_ENTRY,
)
from gate_engine.universal_agent.roles.market_exact_line import (
    REGISTRY_ENTRY as _MEL_ENTRY,
)
from gate_engine.universal_agent.roles.sport_specialist import (
    REGISTRY_ENTRY as _SS_ENTRY,
)
from gate_engine.universal_agent.roles.failure_contradiction import (
    REGISTRY_ENTRY as _FC_ENTRY,
)
from gate_engine.universal_agent.roles.final_refresh import (
    REGISTRY_ENTRY as _FR_ENTRY,
)


# ── Canonical ordered list of all six B1 entries ──────────────────────────────
# Ordering matches the advisory pipeline sequence:
#   1. Data/Slate Integrity  — validates evidence freshness first
#   2. News/Status           — player/team status
#   3. Market/Exact-Line     — live market confirmation
#   4. Sport Specialist      — statistical assessment
#   5. Failure/Contradiction — detects conflicts across prior roles
#   6. Final Refresh         — synthesizes all outputs, issues readiness signal

ALL_B1_ENTRIES: tuple[AgentRegistryEntry, ...] = (
    _DSI_ENTRY,
    _NS_ENTRY,
    _MEL_ENTRY,
    _SS_ENTRY,
    _FC_ENTRY,
    _FR_ENTRY,
)


def register_b1_roles(registry: AgentRegistry) -> None:
    """
    Register all six B1 advisory roles into the given registry.

    Raises:
        KeyError   — if any role's agent_id is already registered
                     (fail-closed, no silent overwrite)
        ValueError — if any entry has advisory_only != True
                     (structural guard in AgentRegistry.register)
    """
    for entry in ALL_B1_ENTRIES:
        registry.register(entry)


def build_b1_registry() -> AgentRegistry:
    """
    Create a fresh AgentRegistry populated with exactly the six B1 roles.
    Does not touch the module-level REGISTRY singleton.

    Tests that need an isolated registry should call this rather than
    mutating the shared singleton.
    """
    registry = AgentRegistry()
    register_b1_roles(registry)
    return registry
