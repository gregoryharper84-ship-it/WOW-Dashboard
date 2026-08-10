"""
gate_engine/universal_agent — Universal Agent Core B0
WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1 / Phase B0

Lane-agnostic shared infrastructure generalizing patterns proven in the
Kalshi Weather shadow pilot (WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW).

Phase B0 components:
  evidence_packet    — Shared Immutable Evidence Packet
  agent_registry     — Shared Agent Registry (advisory_only always True)
  handoff_contract   — Canonical Handoff Contract (data schema only)
  output_contract    — Closed Output Contract + allowlist validator
  capability_boundary — Common Capability Boundary (deny-by-default)
  audit_store        — Shared Audit/Cost Structures (Postgres-backed)

NOT YET BUILT (Phase B1+):
  Lane adapters (MLB, WNBA, Tennis, etc.)
  Production routing
  Automatic agent execution
  User-facing label changes

INVARIANTS — permanently enforced:
  advisory_only = True in all registry entries (cannot be overridden)
  No terminal_label authority in any output contract
  No can_execute or capital-allocation fields permitted anywhere
  Deny-by-default capability boundary
  Durable state only in Postgres, never in-memory ledger
"""
from gate_engine.universal_agent.evidence_packet import (
    Lane,
    EvidencePacket,
    build_evidence_packet,
    build_test_packet,
)
from gate_engine.universal_agent.agent_registry import (
    AgentRole,
    BudgetConfig,
    AgentRegistryEntry,
    AgentRegistry,
    REGISTRY,
)
from gate_engine.universal_agent.handoff_contract import (
    AuthorityRequest,
    NextAction,
    HandoffContract,
    build_handoff_contract,
)
from gate_engine.universal_agent.output_contract import (
    OUTPUT_VALID,
    OutputContractViolation,
    OutputViolationCode,
    validate_output_contract,
    FORBIDDEN_GOVERNANCE_KEYS,
)
from gate_engine.universal_agent.capability_boundary import (
    HookStatus,
    PreHookResult,
    PostHookResult,
    UniversalCapabilityBoundary,
)
from gate_engine.universal_agent.audit_store import (
    UsageStatus,
    ensure_tables,
    record_evidence_packet,
    get_evidence_packet,
    record_agent_result,
    record_budget_event,
    get_run_budget_summary,
    mark_work_completed,
    is_work_completed,
    compute_budget_guard,
)

__all__ = [
    # evidence_packet
    "Lane", "EvidencePacket", "build_evidence_packet", "build_test_packet",
    # agent_registry
    "AgentRole", "BudgetConfig", "AgentRegistryEntry", "AgentRegistry", "REGISTRY",
    # handoff_contract
    "AuthorityRequest", "NextAction", "HandoffContract", "build_handoff_contract",
    # output_contract
    "OUTPUT_VALID", "OutputContractViolation", "OutputViolationCode",
    "validate_output_contract", "FORBIDDEN_GOVERNANCE_KEYS",
    # capability_boundary
    "HookStatus", "PreHookResult", "PostHookResult", "UniversalCapabilityBoundary",
    # audit_store
    "UsageStatus", "ensure_tables", "record_evidence_packet", "get_evidence_packet",
    "record_agent_result", "record_budget_event", "get_run_budget_summary",
    "mark_work_completed", "is_work_completed", "compute_budget_guard",
]
