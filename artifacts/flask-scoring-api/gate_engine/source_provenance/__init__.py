"""
gate_engine/source_provenance
=============================
WOW-PATCH-2026-08-14-SOURCE-PROVENANCE-FRESHNESS-AUDITOR-v2  (Patch 4, P0 governance tier)

Public API surface for the source provenance and freshness audit module.

Three design invariants enforced throughout this module:

  INVARIANT-1 (Freshness):
    Freshness is NOT computed as ``now - retrieved_at``.  Each fact_type+checkpoint
    pair has a registered FactPolicy that specifies *which* timestamp field to treat as
    the freshness anchor (freshness_basis) and the maximum acceptable age for that
    specific fact at that specific checkpoint.  retrieved_at is only the fallback
    basis when no more semantically precise timestamp is present.

  INVARIANT-2 (Source ceilings):
    A SourceType class does NOT by itself impose a universal ceiling cap.  The
    ceiling (max_supportable_ceiling) is determined per fact_type × checkpoint by the
    FactPolicy.insufficient_source_ceiling value, NOT by the source class alone.

  INVARIANT-3 (Conflicts):
    When two sources materially conflict on the same fact, both records are preserved,
    conflict_status is set to MATERIAL_SOURCE_CONFLICT, and the pair is surfaced for
    downstream resolution.  The auditor never silently selects the more convenient or
    higher-graded source.

Isolation scope:
    This module does NOT touch Command Center, SkillOrchestrator, LLP scoring logic,
    or the probability ledger enforcer.  Those are separate patches.

ENGINE_VERSION  = "v1.0"
PATCH_ID        = "WOW-PATCH-2026-08-14-SOURCE-PROVENANCE-FRESHNESS-AUDITOR-v2"
PATCH_PRECEDENCE = 102
"""

from .evidence_contract import (
    SourceType,
    FreshnessBasis,
    FreshnessStatus,
    ConflictStatus,
    ReconstructionStatus,
    Materiality,
    StructuredEvidence,
    SOURCE_TYPE_NORMALIZER,
    build_evidence_from_dict,
)
from .fact_policy_registry import (
    FactPolicy,
    lookup_policy,
    POLICY_REGISTRY,
)
from .freshness_engine import evaluate_freshness
from .conflict_detector import detect_conflicts, ConflictPair
from .auditor import auditSourceProvenance, ProvenanceAuditResult
from .schema_migration import run_provenance_migration

__all__ = [
    # Evidence contract
    "SourceType",
    "FreshnessBasis",
    "FreshnessStatus",
    "ConflictStatus",
    "ReconstructionStatus",
    "Materiality",
    "StructuredEvidence",
    "SOURCE_TYPE_NORMALIZER",
    "build_evidence_from_dict",
    # Policy
    "FactPolicy",
    "lookup_policy",
    "POLICY_REGISTRY",
    # Engines
    "evaluate_freshness",
    "detect_conflicts",
    "ConflictPair",
    # Main auditor
    "auditSourceProvenance",
    "ProvenanceAuditResult",
    # Migration
    "run_provenance_migration",
]

PATCH_ID = "WOW-PATCH-2026-08-14-SOURCE-PROVENANCE-FRESHNESS-AUDITOR-v2"
PATCH_PRECEDENCE = 102
ENGINE_VERSION = "v1.0"
can_execute = False
execution_rule = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
