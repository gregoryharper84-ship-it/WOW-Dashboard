"""
gate_engine/universal_agent — Universal Agent Core B0 + B1 + B2
WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1 / Phases B0–B2

Lane-agnostic shared infrastructure generalizing patterns proven in the
Kalshi Weather shadow pilot (WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW).

Phase B0 components:
  evidence_packet     — Shared Immutable Evidence Packet
  agent_registry      — Shared Agent Registry (advisory_only always True)
  handoff_contract    — Canonical Handoff Contract (data schema only)
  output_contract     — Closed Output Contract + allowlist validator
  capability_boundary — Common Capability Boundary (deny-by-default)
  audit_store         — Shared Audit/Cost Structures (Postgres-backed)

Phase B1 components (gate_engine/universal_agent/roles/):
  Six advisory role contracts with closed schemas and two-phase validators.

Phase B2 components:
  role_runner         — RoleRunnerStatus constants + MockRoleRunner
  role_result         — RoleResult frozen dataclass
  contradiction_detector — Four deterministic cross-role contradiction rules
  bundle_assembler    — EvidenceBundle frozen dataclass + assemble_bundle()
  orchestrator        — run_orchestrator() entry point + OrchestratorResult

Phase B3A components (gate_engine/universal_agent/lanes/mlb_moneyline/):
  MLB Moneyline lane adapter — maps WOW/LLP MLB moneyline evidence rows
  (read-only, post-preflight) into EvidencePacket + six B1 role payloads.

Phase B3B components (gate_engine/universal_agent/shadow/):
  Offline, default-off MLB Moneyline shadow pipeline —
  DeterministicAdapterRunner + ShadowPipeline (SHADOW_ENABLED=False default).
  Patch: WOW-PATCH-2026-08-10-UNIVERSAL-AGENT-CORE-V1-B3B

Phase B3C components (gate_engine/universal_agent/canary/):
  Bounded real-Claude canary infrastructure for offline audit —
  ClaudeRoleRunner + CanaryPipeline (UAC_MLB_ML_CLAUDE_SHADOW_ENABLED=False
  default, pinned model, hard budget caps, zero retries).
  Patch: WOW-PATCH-2026-08-10-UNIVERSAL-AGENT-CORE-V1 / Phase B3C

Phase B4 components (gate_engine/universal_agent/lanes/wnba_props/ and
gate_engine/universal_agent/model_validation/):
  WNBA/NBA Props lane adapter + game-script shadow, plus the Shared Model
  Consistency & Validation Layer (feature store, manifests, champion/
  challenger registry, calibration, drift, health state, promotion gates,
  walk-forward replay, validation wrapper).
  Patches: WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4,
           WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4-MODELVAL

NOT BUILT (requires separate governance approval):
  Production routing / app.py wiring
  Live LLM runner implementations beyond the bounded B3C canary

INVARIANTS — permanently enforced:
  advisory_only = True in all registry entries (cannot be overridden)
  No terminal_label authority in any output contract
  No can_execute or capital-allocation fields permitted anywhere
  Deny-by-default capability boundary
  Durable state only in Postgres (uac_* tables), never in-memory ledger
  Same EvidencePacket object passed to all runners (identity guaranteed)
"""
can_execute           = False
PRODUCTION_AUTHORITY  = False
USER_OUTPUT_AUTHORITY = False
CAPITAL_AUTHORITY     = False
NO_AUTO_PROMOTION     = True
PATCH_ID              = "WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1"

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
