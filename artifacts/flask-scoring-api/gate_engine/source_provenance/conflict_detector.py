"""
gate_engine/source_provenance/conflict_detector.py

MATERIAL_SOURCE_CONFLICT detection.

INVARIANT-3: When two sources materially conflict on the same fact, do NOT
silently pick whichever is more convenient or higher-graded.  Flag it as
MATERIAL_SOURCE_CONFLICT, preserve both source records, and route the pair
to conflict_status for downstream resolution.

A conflict is declared when:
  - Two StructuredEvidence objects share the same fact_type
  - Their fact_value_hash values differ
  - At least one of them has materiality HIGH (or both have MEDIUM and
    the threshold is set to MEDIUM or lower)

Callers receive a list of ConflictPair namedtuples; they must:
  - Preserve both evidence objects (never discard one)
  - Set conflict_status = MATERIAL_SOURCE_CONFLICT on each member of the pair
  - Populate conflicts_with with the opposing evidence_id
  - Route to downstream conflict resolution (human review, escalation gate)

The detector never resolves conflicts automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .evidence_contract import (
    ConflictStatus,
    Materiality,
    StructuredEvidence,
)


@dataclass(frozen=True)
class ConflictPair:
    """
    Two evidence records that materially conflict on the same fact_type.
    Both are preserved; neither is selected as authoritative.
    """
    fact_type:    str
    evidence_a:   StructuredEvidence
    evidence_b:   StructuredEvidence
    conflict_kind: str  # e.g. "VALUE_HASH_MISMATCH"

    def to_dict(self) -> dict:
        return {
            "fact_type":     self.fact_type,
            "evidence_a_id": self.evidence_a.evidence_id,
            "evidence_b_id": self.evidence_b.evidence_id,
            "conflict_kind": self.conflict_kind,
            "hash_a":        self.evidence_a.fact_value_hash,
            "hash_b":        self.evidence_b.fact_value_hash,
            "source_a":      self.evidence_a.source,
            "source_b":      self.evidence_b.source,
            "materiality_a": self.evidence_a.materiality.value,
            "materiality_b": self.evidence_b.materiality.value,
        }


_MATERIALITY_RANK: dict[Materiality, int] = {
    Materiality.LOW:    0,
    Materiality.MEDIUM: 1,
    Materiality.HIGH:   2,
}


def detect_conflicts(
    evidence_list: Sequence[StructuredEvidence],
    *,
    materiality_threshold: Materiality = Materiality.HIGH,
    mutate_evidence: bool = True,
) -> list[ConflictPair]:
    """
    Detect MATERIAL_SOURCE_CONFLICT pairs within *evidence_list*.

    Parameters
    ----------
    evidence_list         : Evidence objects to compare (same candidate / fact space)
    materiality_threshold : Only flag pairs where at least one object's materiality
                            is >= this threshold.  Default HIGH (most conservative).
    mutate_evidence       : When True (default), update conflict_status and
                            conflicts_with on the evidence objects in place.
                            Set False in read-only contexts.

    Returns
    -------
    List of ConflictPair; each pair surfaces both conflicting records.

    Guarantees
    ----------
    - Both evidence objects are preserved; neither is discarded.
    - conflict_status is set to MATERIAL_SOURCE_CONFLICT on each member.
    - conflicts_with is populated with the opposing evidence_id.
    """
    threshold_rank = _MATERIALITY_RANK[materiality_threshold]
    pairs: list[ConflictPair] = []

    # Group by fact_type
    by_fact_type: dict[str, list[StructuredEvidence]] = {}
    for ev in evidence_list:
        by_fact_type.setdefault(ev.fact_type, []).append(ev)

    for fact_type, group in by_fact_type.items():
        if len(group) < 2:
            continue

        # Pairwise comparison (O(n²) — evidence lists are small in practice)
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                ev_a = group[i]
                ev_b = group[j]

                if ev_a.fact_value_hash == ev_b.fact_value_hash:
                    # Identical fact values — no conflict
                    continue

                # Check materiality threshold: at least one must meet or exceed threshold
                rank_a = _MATERIALITY_RANK[ev_a.materiality]
                rank_b = _MATERIALITY_RANK[ev_b.materiality]
                if max(rank_a, rank_b) < threshold_rank:
                    continue

                pair = ConflictPair(
                    fact_type=fact_type,
                    evidence_a=ev_a,
                    evidence_b=ev_b,
                    conflict_kind="VALUE_HASH_MISMATCH",
                )
                pairs.append(pair)

                if mutate_evidence:
                    _apply_conflict(ev_a, ev_b)
                    _apply_conflict(ev_b, ev_a)

    return pairs


def _apply_conflict(subject: StructuredEvidence, opponent: StructuredEvidence) -> None:
    """
    Mutate *subject* in place to record the conflict with *opponent*.

    INVARIANT-3: Both records are preserved.  We mark each as conflicting
    and record the opponent's evidence_id in conflicts_with.
    """
    if opponent.evidence_id not in subject.conflicts_with:
        subject.conflicts_with.append(opponent.evidence_id)
    subject.conflict_status = ConflictStatus.MATERIAL_SOURCE_CONFLICT


def classify_conflict_severity(pair: ConflictPair) -> str:
    """
    Return a severity label for logging / downstream routing.

    Severity is determined by the materiality of the higher-ranked member
    of the pair, not by source grade.
    """
    rank_a = _MATERIALITY_RANK[pair.evidence_a.materiality]
    rank_b = _MATERIALITY_RANK[pair.evidence_b.materiality]
    max_rank = max(rank_a, rank_b)
    if max_rank >= _MATERIALITY_RANK[Materiality.HIGH]:
        return "HIGH"
    elif max_rank >= _MATERIALITY_RANK[Materiality.MEDIUM]:
        return "MEDIUM"
    return "LOW"
