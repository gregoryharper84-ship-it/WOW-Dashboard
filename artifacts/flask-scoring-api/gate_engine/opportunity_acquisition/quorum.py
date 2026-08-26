"""
quorum.py — Multi-source minutes projection quorum and conflict resolver.

When ≥2 adapters return projected-minutes distributions:
  - Agree within 15%: conservative consensus (min lows, weighted avg modes, max highs)
  - Disagree >15%:    SOURCE_CONFLICT, widen uncertainty by 25%, penalty += 0.20/conflict

resolve_quorum(packets) → QuorumResult
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .types import MinutesDistribution, VendorPacket


# ---------------------------------------------------------------------------
# Agreement threshold
# ---------------------------------------------------------------------------
_AGREE_THRESHOLD   = 0.15   # relative difference in modal minutes
_WIDEN_FACTOR      = 1.25   # distribution spread multiplier on conflict
_CONFLICT_PENALTY  = 0.20   # CLB penalty per conflicting pair (capped at 1.0)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class QuorumResult:
    consensus_distribution: MinutesDistribution | None
    agreement:              bool
    confidence:             float                   # 0.0–1.0
    conflict_pairs:         list[tuple[str, str]]   = field(default_factory=list)
    minutes_conflict_penalty: float                 = 0.0
    notes:                  list[str]               = field(default_factory=list)
    contributing_sources:   list[str]               = field(default_factory=list)
    can_execute:            bool                    = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "can_execute":              False,
            "agreement":                self.agreement,
            "confidence":               self.confidence,
            "conflict_pairs":           [list(p) for p in self.conflict_pairs],
            "minutes_conflict_penalty": self.minutes_conflict_penalty,
            "contributing_sources":     self.contributing_sources,
            "notes":                    self.notes,
            "consensus_distribution": (
                self.consensus_distribution.to_dict()
                if self.consensus_distribution else None
            ),
        }


# ---------------------------------------------------------------------------
# Core resolver
# ---------------------------------------------------------------------------

def resolve_quorum(packets: list[VendorPacket]) -> QuorumResult:
    """
    Resolve projected-minutes distributions across vendor packets.

    Returns a QuorumResult with a consensus distribution (or None if no
    packets carry minutes data), agreement flag, conflict pairs, and the
    minutes_conflict_penalty to pass downstream to calibration.
    """
    packets_with_minutes = [
        p for p in packets
        if p.minutes_distribution is not None
        and p.request_status == "success"
    ]

    if not packets_with_minutes:
        return QuorumResult(
            consensus_distribution=None,
            agreement=False,
            confidence=0.0,
            notes=["NO_MINUTES_DATA: no vendor packets with minutes distribution"],
        )

    if len(packets_with_minutes) == 1:
        single = packets_with_minutes[0]
        dist   = single.minutes_distribution
        return QuorumResult(
            consensus_distribution=dist,
            agreement=True,
            confidence=dist.confidence * 0.80,   # single-source discount
            contributing_sources=[single.source],
            notes=["SINGLE_SOURCE: quorum requires ≥2; using single source with discount"],
        )

    # -----------------------------------------------------------------------
    # Compare all pairs: flag conflicts where modal values differ >15%
    # -----------------------------------------------------------------------
    conflict_pairs: list[tuple[str, str]] = []
    for i in range(len(packets_with_minutes)):
        for j in range(i + 1, len(packets_with_minutes)):
            a = packets_with_minutes[i]
            b = packets_with_minutes[j]
            mode_a = a.minutes_distribution.mode
            mode_b = b.minutes_distribution.mode
            if mode_a <= 0 or mode_b <= 0:
                continue
            rel_diff = abs(mode_a - mode_b) / max(mode_a, mode_b)
            if rel_diff > _AGREE_THRESHOLD:
                conflict_pairs.append((a.source, b.source))

    sources = [p.source for p in packets_with_minutes]
    dists   = [p.minutes_distribution for p in packets_with_minutes]

    if conflict_pairs:
        # -----------------------------------------------------------------------
        # Conflict path: widen the distribution, apply CLB penalty
        # -----------------------------------------------------------------------
        widened = _widen_distribution(dists, sources)
        penalty = min(1.0, len(conflict_pairs) * _CONFLICT_PENALTY)
        notes   = [
            f"SOURCE_CONFLICT: {len(conflict_pairs)} conflicting pair(s); "
            f"uncertainty widened ×{_WIDEN_FACTOR}; "
            f"minutes_conflict_penalty={penalty:.2f}"
        ]
        for s1, s2 in conflict_pairs:
            notes.append(f"  conflict_pair: {s1} vs {s2}")
        return QuorumResult(
            consensus_distribution=widened,
            agreement=False,
            confidence=max(0.10, _avg_confidence(dists) - penalty),
            conflict_pairs=conflict_pairs,
            minutes_conflict_penalty=penalty,
            contributing_sources=sources,
            notes=notes,
        )

    # -----------------------------------------------------------------------
    # Consensus path: all sources agree
    # -----------------------------------------------------------------------
    consensus = _conservative_consensus(dists, sources)
    return QuorumResult(
        consensus_distribution=consensus,
        agreement=True,
        confidence=_avg_confidence(dists),
        contributing_sources=sources,
        notes=[f"CONSENSUS: {len(dists)} sources agree within {_AGREE_THRESHOLD*100:.0f}%"],
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _avg_confidence(dists: list[MinutesDistribution]) -> float:
    confs = [d.confidence for d in dists if d.confidence is not None]
    return sum(confs) / len(confs) if confs else 0.5


def _conservative_consensus(
    dists: list[MinutesDistribution],
    sources: list[str],
) -> MinutesDistribution:
    """
    Conservative consensus:
      low  = min of all lows  (most pessimistic floor)
      mode = weighted average of modes (confidence-weighted)
      high = max of all highs (most pessimistic ceiling)
    """
    lows   = [d.low  for d in dists]
    highs  = [d.high for d in dists]
    confs  = [max(0.01, d.confidence) for d in dists]
    modes  = [d.mode for d in dists]

    total_w      = sum(confs)
    weighted_mode = sum(m * w for m, w in zip(modes, confs)) / total_w

    return MinutesDistribution(
        low        = min(lows),
        mode       = round(weighted_mode, 2),
        high       = max(highs),
        confidence = _avg_confidence(dists),
        source     = "consensus:" + ",".join(sources),
    )


def _widen_distribution(
    dists: list[MinutesDistribution],
    sources: list[str],
) -> MinutesDistribution:
    """
    Conflict path: build a widened distribution.
    Mode = confidence-weighted avg; low/high spread by _WIDEN_FACTOR.
    """
    confs  = [max(0.01, d.confidence) for d in dists]
    modes  = [d.mode for d in dists]
    total_w = sum(confs)
    weighted_mode = sum(m * w for m, w in zip(modes, confs)) / total_w

    spread = weighted_mode * (_WIDEN_FACTOR - 1.0) / 2.0
    low    = max(0.0, min(d.low for d in dists) - spread)
    high   = max(d.high for d in dists) + spread

    return MinutesDistribution(
        low        = round(low, 2),
        mode       = round(weighted_mode, 2),
        high       = round(high, 2),
        confidence = max(0.10, _avg_confidence(dists) - 0.20),
        source     = "conflict-widened:" + ",".join(sources),
    )
