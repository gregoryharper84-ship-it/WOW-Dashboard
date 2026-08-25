"""
gate_engine/source_provenance/auditor.py

Main auditor: auditSourceProvenance(fact, checkpoint)

auditSourceProvenance is the single entry point for provenance evaluation.
It:
  1. Looks up the FactPolicy for (fact_type, checkpoint).
  2. Evaluates freshness using the policy's freshness_basis (INVARIANT-1).
  3. Checks whether the fact's source_type is in accepted_source_types for
     this checkpoint (INVARIANT-2 — no universal ceiling by source class).
  4. Sets max_supportable_ceiling reflecting the policy's defined cap when
     either check fails; leaves it None when both pass (unconstrained).
  5. Runs conflict detection when existing_facts is supplied (INVARIANT-3).

Returns a ProvenanceAuditResult; callers must:
  - Apply result.fact (the updated evidence object) downstream.
  - Respect result.ceiling_imposed if non-None.
  - Route result.conflict_pairs to conflict resolution — never discard them.

can_execute = False : this module is audit-only; it never routes markets,
places bets, or modifies live scoring decisions directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

from .conflict_detector import ConflictPair, detect_conflicts
from .evidence_contract import (
    ConflictStatus,
    FreshnessBasis,
    FreshnessStatus,
    SourceType,
    StructuredEvidence,
)
from .fact_policy_registry import FactPolicy, lookup_policy
from .freshness_engine import evaluate_freshness

log = logging.getLogger(__name__)

can_execute    = False
execution_rule = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ProvenanceAuditResult:
    """
    Output of auditSourceProvenance.

    fact                : The StructuredEvidence object with freshness_status,
                          freshness_policy_id, freshness_basis, conflict_status,
                          and max_supportable_ceiling set by this audit pass.
    policy              : The FactPolicy used; None if POLICY_ABSENT.
    freshness_status    : Convenience alias for fact.freshness_status.
    age_seconds         : Exact age in seconds from freshness_basis anchor, or None.
    ceiling_imposed     : Non-None when this audit imposes a ceiling; the ceiling label.
    ceiling_reason      : Human-readable explanation of why the ceiling was imposed.
    conflict_pairs      : ConflictPair list when conflicts were detected.
    audit_flags         : List of string flags for observability / logging.
    """
    fact:             StructuredEvidence
    policy:           FactPolicy | None
    freshness_status: FreshnessStatus
    age_seconds:      float | None
    ceiling_imposed:  str | None
    ceiling_reason:   str | None
    conflict_pairs:   list[ConflictPair] = field(default_factory=list)
    audit_flags:      list[str]          = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "evidence_id":        self.fact.evidence_id,
            "fact_type":          self.fact.fact_type,
            "freshness_status":   self.freshness_status.value,
            "age_seconds":        self.age_seconds,
            "ceiling_imposed":    self.ceiling_imposed,
            "ceiling_reason":     self.ceiling_reason,
            "policy_id":          self.policy.policy_id if self.policy else None,
            "freshness_basis":    self.policy.freshness_basis.value if self.policy else None,
            "source_type":        self.fact.source_type.value,
            "conflict_count":     len(self.conflict_pairs),
            "conflict_pairs":     [p.to_dict() for p in self.conflict_pairs],
            "audit_flags":        list(self.audit_flags),
            "max_supportable_ceiling": self.fact.max_supportable_ceiling,
            "can_execute":        can_execute,
            "execution_rule":     execution_rule,
        }


# ---------------------------------------------------------------------------
# Main auditor
# ---------------------------------------------------------------------------

def auditSourceProvenance(
    fact: StructuredEvidence,
    checkpoint: str,
    *,
    as_of: datetime | None = None,
    existing_facts: Sequence[StructuredEvidence] | None = None,
    route: str | None = None,
    event_proximity: str | None = None,
) -> ProvenanceAuditResult:
    """
    Evaluate the provenance, freshness, and source-type acceptance of *fact*
    at the given *checkpoint*, and detect conflicts against *existing_facts*.

    Parameters
    ----------
    fact            : The StructuredEvidence to audit.  Modified in place
                      (freshness_status, freshness_policy_id, freshness_basis,
                      max_supportable_ceiling, conflict_status, conflicts_with).
    checkpoint      : Lifecycle checkpoint being evaluated (e.g. "market_gate",
                      "llp_calibration", "uac_evidence_intake").
    as_of           : Reference time for freshness evaluation; defaults to utcnow().
    existing_facts  : Other evidence objects already attached to this candidate.
                      When supplied, conflict detection runs across the full set.
    route           : Optional routing context for future policy specialization.
    event_proximity : Optional event-proximity context (e.g. "pre_game_6h", "live").

    Returns
    -------
    ProvenanceAuditResult (see docstring on that class).

    INVARIANT-1 (freshness)  : uses policy.freshness_basis, not hardcoded retrieved_at.
    INVARIANT-2 (ceiling)    : ceiling is from policy.insufficient_source_ceiling, not
                               a per-SourceType global cap.
    INVARIANT-3 (conflicts)  : both records preserved; no automatic resolution.
    """
    audit_flags: list[str] = []
    ceiling_imposed: str | None = None
    ceiling_reason:  str | None = None

    # ── 1. Policy lookup ────────────────────────────────────────────────────
    policy = lookup_policy(
        fact.fact_type,
        checkpoint,
        route=route,
        event_proximity=event_proximity,
        materiality=fact.materiality.value,
    )

    if policy is None:
        # No policy at all (should not happen with the wildcard fallback, but
        # handle defensively)
        fact.freshness_status    = FreshnessStatus.POLICY_ABSENT
        fact.freshness_policy_id = None
        fact.freshness_basis     = None
        audit_flags.append("POLICY_ABSENT")
        return ProvenanceAuditResult(
            fact=fact,
            policy=None,
            freshness_status=FreshnessStatus.POLICY_ABSENT,
            age_seconds=None,
            ceiling_imposed=None,
            ceiling_reason="No policy registered for this fact_type + checkpoint.",
            audit_flags=audit_flags,
        )

    # ── 2. Stamp policy metadata onto the evidence object ───────────────────
    fact.freshness_policy_id = policy.policy_id
    fact.freshness_basis     = policy.freshness_basis

    # ── 3. Freshness evaluation (INVARIANT-1) ───────────────────────────────
    freshness_status, age_seconds = evaluate_freshness(fact, policy, as_of=as_of)
    fact.freshness_status = freshness_status

    if freshness_status == FreshnessStatus.UNVERIFIABLE:
        audit_flags.append("FRESHNESS_UNVERIFIABLE")
        # Do not impose a ceiling for an unverifiable freshness — the timestamp
        # is absent, which is a data-quality issue, not a staleness failure.

    elif freshness_status in (FreshnessStatus.STALE, FreshnessStatus.EXPIRED):
        audit_flags.append(f"FRESHNESS_{freshness_status.value}")
        if policy.stale_ceiling:
            ceiling_imposed = policy.stale_ceiling
            ceiling_reason  = (
                f"Fact '{fact.fact_type}' at checkpoint '{checkpoint}' is "
                f"{freshness_status.value} "
                f"(age={age_seconds:.0f}s, max={policy.max_age_seconds}s, "
                f"basis={policy.freshness_basis.value}). "
                f"Policy '{policy.policy_id}' imposes ceiling={policy.stale_ceiling}."
            )
            audit_flags.append(f"STALE_CEILING={policy.stale_ceiling}")

    # ── 4. Source-type acceptance (INVARIANT-2) ─────────────────────────────
    #
    # The ceiling is from policy.insufficient_source_ceiling for THIS checkpoint,
    # NOT a universal cap derived from source_type alone.
    source_accepted = fact.source_type in policy.accepted_source_types

    if not source_accepted:
        audit_flags.append(f"SOURCE_TYPE_REJECTED:{fact.source_type.value}")
        if policy.insufficient_source_ceiling:
            # Only upgrade the ceiling; never lower it
            ceiling_imposed = _stricter_ceiling(ceiling_imposed, policy.insufficient_source_ceiling)
            ceiling_reason = (
                (ceiling_reason + " | " if ceiling_reason else "")
                + f"Source type '{fact.source_type.value}' is not in accepted types "
                f"for checkpoint '{checkpoint}' per policy '{policy.policy_id}'. "
                f"Accepted: {sorted(t.value for t in policy.accepted_source_types)}. "
                f"Ceiling={policy.insufficient_source_ceiling}."
            )
            audit_flags.append(f"INSUFFICIENT_SOURCE_CEILING={policy.insufficient_source_ceiling}")

    # ── 5. Set ceiling on evidence object ───────────────────────────────────
    # max_supportable_ceiling is None when BOTH checks pass (unconstrained).
    fact.max_supportable_ceiling = ceiling_imposed

    # ── 6. Conflict detection (INVARIANT-3) ─────────────────────────────────
    conflict_pairs: list[ConflictPair] = []
    if existing_facts:
        all_facts = list(existing_facts) + [fact]
        conflict_pairs = detect_conflicts(all_facts, mutate_evidence=True)
        if conflict_pairs:
            audit_flags.append(f"CONFLICTS_DETECTED:{len(conflict_pairs)}")
            log.warning(
                "auditSourceProvenance: %d MATERIAL_SOURCE_CONFLICT pair(s) detected "
                "for fact_type=%r at checkpoint=%r. Both records preserved.",
                len(conflict_pairs), fact.fact_type, checkpoint,
            )

    _log_audit(fact, checkpoint, policy, freshness_status, age_seconds,
               ceiling_imposed, audit_flags)

    return ProvenanceAuditResult(
        fact=fact,
        policy=policy,
        freshness_status=freshness_status,
        age_seconds=age_seconds,
        ceiling_imposed=ceiling_imposed,
        ceiling_reason=ceiling_reason,
        conflict_pairs=conflict_pairs,
        audit_flags=audit_flags,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Ceiling rank: higher index = more restrictive (lower ceiling label)
_CEILING_ORDER = [
    "FINAL_APPROVED",
    "RANK",
    "FINAL",
    "QUALIFIED",
    "WATCH",
    "SCOUT",
    "RESEARCH",
    "NO_PLAY",
]


def _ceiling_rank(label: str | None) -> int:
    if label is None:
        return -1
    try:
        return _CEILING_ORDER.index(label)
    except ValueError:
        return 0  # Unknown labels treated as least restrictive for safety


def _stricter_ceiling(existing: str | None, candidate: str) -> str:
    """
    Return whichever ceiling is more restrictive (higher rank).
    We never lower an existing ceiling when a second check fires.
    """
    if _ceiling_rank(candidate) > _ceiling_rank(existing):
        return candidate
    return existing  # type: ignore[return-value]


def _log_audit(
    fact: StructuredEvidence,
    checkpoint: str,
    policy: FactPolicy,
    freshness_status: FreshnessStatus,
    age_seconds: float | None,
    ceiling_imposed: str | None,
    audit_flags: list[str],
) -> None:
    msg = (
        "provenance_audit evidence_id=%r fact_type=%r checkpoint=%r "
        "policy=%r freshness=%s age_s=%s ceiling=%s flags=%s"
    )
    log.debug(
        msg,
        fact.evidence_id, fact.fact_type, checkpoint,
        policy.policy_id, freshness_status.value,
        f"{age_seconds:.1f}" if age_seconds is not None else "N/A",
        ceiling_imposed or "none",
        audit_flags,
    )
