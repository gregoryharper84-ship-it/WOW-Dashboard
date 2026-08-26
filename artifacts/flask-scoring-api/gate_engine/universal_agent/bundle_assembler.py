"""
gate_engine/universal_agent/bundle_assembler.py
WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1 / Phase B2

EvidenceBundle: canonical, immutable research/evidence bundle assembled from
all role results produced by the orchestrator.

Design
------
- Frozen dataclass: immutable once assembled.
- assemble_bundle() is a pure function — no I/O, no side effects.
- Deterministic: accepted_role_ids and failed_role_ids are sorted by role_id;
  data_gaps and source_conflicts are deduplicated in a stable order.
- Missing roles (expected but with no result) are preserved explicitly in
  missing_role_ids — never silently treated as empty or successful.
- SKIPPED_RESUMED roles count as effectively accepted (prior-run acceptance
  preserved). Their advisory_findings are None in this bundle because they
  are not reloaded from the database in B2.
- bundle_status:
    COMPLETE — all expected roles effectively accepted + no HIGH contradictions.
    PARTIAL  — at least one effectively accepted, but not all.
    FAILED   — zero roles effectively accepted.
- Source provenance and source_failures are carried from the EvidencePacket.
- data_gaps and source_conflicts are merged from packet + accepted role outputs.

No app.py import, no Flask route, no live API call, no Weather code.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence


# ── Bundle status constants ────────────────────────────────────────────────────

class BundleStatus:
    """
    Canonical status of an assembled EvidenceBundle.

    COMPLETE — All expected roles effectively accepted; no HIGH-severity
               contradictions detected.
    PARTIAL  — At least one role effectively accepted, but not all expected
               roles (some failed, missing, or a HIGH contradiction exists).
    FAILED   — Zero roles effectively accepted.
    """
    COMPLETE = "COMPLETE"
    PARTIAL  = "PARTIAL"
    FAILED   = "FAILED"


# ── Evidence bundle ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EvidenceBundle:
    """
    Canonical, immutable research/evidence bundle.

    Fields
    ------
    run_id, snapshot_id, canonical_event_id, lane
        Identity fields copied from the EvidencePacket.
    accepted_role_ids
        Sorted tuple of role_ids with ACCEPTED or SKIPPED_RESUMED status.
    failed_role_ids
        Sorted tuple of role_ids with any non-effective-accepted status.
    missing_role_ids
        Sorted tuple of expected role_ids that produced no RoleResult.
    contradictions
        Tuple of ContradictionRecord detected across accepted roles.
    bundle_status
        BundleStatus.* constant.
    assembled_at
        ISO-8601 UTC timestamp of assembly (deterministic per run invocation).
    source_provenance
        Copied from EvidencePacket.source_provenance.
    source_failures
        Copied from EvidencePacket.source_failures.
    source_conflicts
        Merged from EvidencePacket.source_conflicts + accepted role outputs
        (deduplicated, stable order).
    data_gaps
        Merged from accepted role raw_output.get("data_gaps", [])
        (deduplicated, sorted by role_id then insertion order).
    accepted_findings
        dict[role_id → advisory_findings] for ACCEPTED roles only.
        SKIPPED_RESUMED roles have no in-memory findings (not loaded from DB).
    """
    run_id:             str
    snapshot_id:        str
    canonical_event_id: str
    lane:               str
    accepted_role_ids:  tuple
    failed_role_ids:    tuple
    missing_role_ids:   tuple
    contradictions:     tuple
    bundle_status:      str
    assembled_at:       str
    source_provenance:  dict
    source_failures:    tuple
    source_conflicts:   tuple
    data_gaps:          tuple
    accepted_findings:  dict

    def to_dict(self) -> dict:
        return {
            "run_id":             self.run_id,
            "snapshot_id":        self.snapshot_id,
            "canonical_event_id": self.canonical_event_id,
            "lane":               self.lane,
            "accepted_role_ids":  list(self.accepted_role_ids),
            "failed_role_ids":    list(self.failed_role_ids),
            "missing_role_ids":   list(self.missing_role_ids),
            "contradictions":     [c.to_dict() for c in self.contradictions],
            "bundle_status":      self.bundle_status,
            "assembled_at":       self.assembled_at,
            "source_provenance":  self.source_provenance,
            "source_failures":    list(self.source_failures),
            "source_conflicts":   list(self.source_conflicts),
            "data_gaps":          list(self.data_gaps),
            "accepted_findings":  self.accepted_findings,
        }


# ── Assembler ─────────────────────────────────────────────────────────────────

def assemble_bundle(
    *,
    packet: Any,
    role_results: Sequence,
    all_expected_role_ids: Sequence[str],
    contradictions: tuple = (),
    assembled_at: str = "",
) -> EvidenceBundle:
    """
    Pure function: assemble an EvidenceBundle from orchestrator role results.

    Parameters
    ----------
    packet
        The original EvidencePacket (provides identity + source fields).
    role_results
        Sequence[RoleResult] — all role outcomes (accepted, failed, skipped).
    all_expected_role_ids
        Sequence of all role_id strings expected in a complete run
        (B1_ROLE_IDS from orchestrator).
    contradictions
        Tuple of ContradictionRecord from detect_contradictions().
    assembled_at
        ISO-8601 UTC string. If empty, the current UTC time is used.
        Provided as a parameter so tests can inject a fixed value for
        deterministic comparison (without it, two calls differ in timestamp).

    Returns
    -------
    EvidenceBundle (frozen dataclass, immutable).
    """
    from gate_engine.universal_agent.role_runner import RoleRunnerStatus

    # Classify results
    effective_accepted_statuses = {
        RoleRunnerStatus.ACCEPTED,
        RoleRunnerStatus.SKIPPED_RESUMED,
    }
    accepted = [r for r in role_results if r.status in effective_accepted_statuses]
    failed   = [r for r in role_results if r.status not in effective_accepted_statuses]

    result_role_ids = {r.role_id for r in role_results}
    missing_ids     = [rid for rid in all_expected_role_ids if rid not in result_role_ids]

    # advisory_findings only for truly ACCEPTED (SKIPPED_RESUMED has no in-memory findings)
    accepted_findings: dict = {}
    for r in accepted:
        if r.status == RoleRunnerStatus.ACCEPTED and r.advisory_findings is not None:
            accepted_findings[r.role_id] = r.advisory_findings

    # Merge data_gaps from accepted roles (stable: sort by role_id, deduplicate)
    merged_gaps: list = []
    seen_gap_keys: set = set()
    for r in sorted(accepted, key=lambda x: x.role_id):
        raw = r.raw_output or {}
        for gap in raw.get("data_gaps", []):
            k = str(gap)
            if k not in seen_gap_keys:
                merged_gaps.append(gap)
                seen_gap_keys.add(k)

    # Merge source_conflicts: packet first, then accepted roles (deduplicate)
    merged_conflicts: list = []
    seen_conflict_keys: set = set()
    for sc in list(getattr(packet, "source_conflicts", ())):
        k = str(sc)
        if k not in seen_conflict_keys:
            merged_conflicts.append(sc)
            seen_conflict_keys.add(k)
    for r in sorted(accepted, key=lambda x: x.role_id):
        raw = r.raw_output or {}
        for sc in raw.get("source_conflicts", []):
            k = str(sc)
            if k not in seen_conflict_keys:
                merged_conflicts.append(sc)
                seen_conflict_keys.add(k)

    # Bundle status
    has_high_contradiction = any(c.severity == "HIGH" for c in contradictions)
    all_expected_set       = set(all_expected_role_ids)
    accepted_role_id_set   = {r.role_id for r in accepted}

    if len(accepted) == 0:
        bundle_status = BundleStatus.FAILED
    elif accepted_role_id_set == all_expected_set and not has_high_contradiction:
        bundle_status = BundleStatus.COMPLETE
    else:
        bundle_status = BundleStatus.PARTIAL

    ts = assembled_at or datetime.now(timezone.utc).isoformat()

    return EvidenceBundle(
        run_id=packet.run_id,
        snapshot_id=packet.snapshot_id,
        canonical_event_id=packet.canonical_event_id,
        lane=packet.lane,
        accepted_role_ids=tuple(sorted(accepted_role_id_set)),
        failed_role_ids=tuple(sorted(r.role_id for r in failed)),
        missing_role_ids=tuple(sorted(missing_ids)),
        contradictions=contradictions,
        bundle_status=bundle_status,
        assembled_at=ts,
        source_provenance=dict(getattr(packet, "source_provenance", {})),
        source_failures=tuple(getattr(packet, "source_failures", ())),
        source_conflicts=tuple(merged_conflicts),
        data_gaps=tuple(merged_gaps),
        accepted_findings=accepted_findings,
    )
