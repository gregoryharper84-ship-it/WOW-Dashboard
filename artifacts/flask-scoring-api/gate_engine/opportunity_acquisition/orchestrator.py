"""
orchestrator.py — AcquisitionOrchestrator: multi-vendor acquisition entry point.

AcquisitionOrchestrator.acquire(row, enrichment) → OpportunityState

Workflow:
  1. Determine if row is an NBA/WNBA composite prop
  2. Call vendor adapters in priority order (A-grade first)
  3. Run quorum/conflict resolution on projected-minutes distributions
  4. Merge field-level outputs (first-non-None wins per field)
  5. Reconcile NOT_CALLED statuses (→ DATA_UNOBTAINABLE)
  6. Compute composite_confidence
  7. Return finalized OpportunityState with can_execute=False

can_execute=False is unconditional in every output.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from .types import (
    AcquisitionStatus,
    ComponentOpportunityRates,
    LineupStatus,
    MinutesDistribution,
    OpportunityState,
    VendorPacket,
)
from .adapters import (
    BallDontLieAdapter,
    OddsApiAdapter,
    InternalStatsApiAdapter,
    SportsDataIOAdapter,
    RotoWireAdapter,
    OpportunitySourceAdapter,
)
from .quorum import resolve_quorum
from .invalidation import InvalidationTracker
from .market_identity import MarketIdentity, canonicalize, compare_identity, IdentityMatch


# ---------------------------------------------------------------------------
# Required fields — all must be non-NOT_CALLED in the final state
# ---------------------------------------------------------------------------
_REQUIRED_FIELDS = [
    "minutes_distribution",
    "starter_probability",
    "lineup_status",
    "rotation_confidence",
    "component_opportunity",
    "event_status",
    "player_status",
]

# Canonical composite prop families that trigger orchestrator.
# Only canonical forms live here; raw aliases are normalized by
# gate_engine.component_composite.STAT_FAMILY_ALIASES before comparison.
_COMPOSITE_FAMILIES = frozenset({"pra", "p+r", "r+a", "p+a"})

_NBA_WNBA_SPORTS = frozenset({"nba", "wnba"})


# ---------------------------------------------------------------------------
# Module-level invalidation tracker (shared within a gunicorn worker)
# ---------------------------------------------------------------------------
_invalidation_tracker = InvalidationTracker()


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class AcquisitionOrchestrator:
    """
    Multi-vendor acquisition orchestrator for NBA/WNBA composite props.

    Usage:
        orchestrator = AcquisitionOrchestrator()
        state = orchestrator.acquire(row, enrichment)
        row["gates"]["opportunity_acquisition"] = state.to_dict()
    """

    def __init__(self) -> None:
        self.can_execute: bool = False  # unconditional
        # Adapters in priority order (A-grade first)
        self._adapters: list[OpportunitySourceAdapter] = [
            SportsDataIOAdapter(),
            RotoWireAdapter(),
            BallDontLieAdapter(),
            OddsApiAdapter(),
            InternalStatsApiAdapter(),   # always available; uses enrichment
        ]

    # -----------------------------------------------------------------------
    # Public entry point
    # -----------------------------------------------------------------------

    def acquire(
        self,
        row: dict[str, Any],
        enrichment: dict[str, Any],
    ) -> OpportunityState:
        """
        Run the full acquisition pipeline for one row.

        Returns a finalized OpportunityState.  NOT_CALLED statuses are
        replaced with DATA_UNOBTAINABLE before returning.
        """
        state = OpportunityState()

        # Initialize per-field statuses to NOT_CALLED
        for f in _REQUIRED_FIELDS:
            state.per_field_statuses[f] = AcquisitionStatus.NOT_CALLED

        sport     = (row.get("sport") or "").lower()
        prop_type = (row.get("prop_type") or row.get("market_type") or "").lower()
        player    = row.get("player") or row.get("team") or ""
        event_id  = row.get("event_id") or row.get("game_id")
        event_date= row.get("slate_date") or row.get("event_date") or row.get("game_date")

        # -----------------------------------------------------------------------
        # Step 1: Call all adapters
        # -----------------------------------------------------------------------
        packets: list[VendorPacket] = []
        for adapter in self._adapters:
            try:
                packet = adapter.fetch(
                    player     = player,
                    event_id   = event_id,
                    event_date = event_date,
                    sport      = sport,
                    prop_type  = prop_type,
                    enrichment = enrichment,
                )
                packets.append(packet)
                state.source_timestamps[adapter.source_name] = packet.retrieved_at
            except Exception as exc:
                state.notes.append(
                    f"ADAPTER_ERROR:{adapter.source_name}:{exc!s:.80}"
                )
                # Record a failure packet so the field never stays NOT_CALLED
                packets.append(VendorPacket(
                    source         = adapter.source_name,
                    retrieved_at   = datetime.now(timezone.utc).isoformat(),
                    source_grade   = adapter.source_grade,
                    request_status = "failed",
                    failure_reason = str(exc)[:80],
                ))

        state.vendor_packets = packets

        # -----------------------------------------------------------------------
        # Step 2: Quorum resolution for projected minutes
        # -----------------------------------------------------------------------
        quorum_result = resolve_quorum(packets)
        state.minutes_distribution     = quorum_result.consensus_distribution
        state.minutes_source_agreement = quorum_result.agreement
        state.minutes_conflict_penalty = quorum_result.minutes_conflict_penalty
        state.source_conflict_pairs    = list(quorum_result.conflict_pairs)
        state.notes.extend(quorum_result.notes)

        # Set minutes_distribution status
        if state.minutes_distribution is not None:
            if quorum_result.conflict_pairs:
                state.per_field_statuses["minutes_distribution"] = AcquisitionStatus.SOURCE_CONFLICT
            elif quorum_result.agreement:
                n_sources = len(quorum_result.contributing_sources)
                if n_sources >= 2:
                    state.per_field_statuses["minutes_distribution"] = AcquisitionStatus.RETRIEVED
                else:
                    # Single source
                    state.per_field_statuses["minutes_distribution"] = AcquisitionStatus.RECONSTRUCTED
            else:
                state.per_field_statuses["minutes_distribution"] = AcquisitionStatus.PROXY_ONLY
        # NOT_CALLED will be reconciled below

        # -----------------------------------------------------------------------
        # Step 3: Merge field-level outputs (first non-None wins per field)
        # -----------------------------------------------------------------------
        successful = [p for p in packets if p.request_status == "success"]
        any_packets = packets  # use all for fallback

        state.starter_probability  = _first_non_none(successful, "starter_probability")
        state.lineup_status        = _first_lineup(successful)
        state.rotation_confidence  = _first_non_none(successful, "rotation_confidence")
        state.minutes_restriction_prob = _first_non_none(successful, "minutes_restriction_prob")
        state.blowout_risk         = _first_non_none(successful, "blowout_risk")
        state.component_opportunity = _first_component_opp(successful)
        state.teammate_status      = _merge_teammate_status(successful)

        # Event and player status
        event_status  = _first_str(successful, "event_status")
        player_status = _first_str(successful, "player_status")

        # Source agreement: are all successful packets' lineup_status consistent?
        lineups = [p.lineup_status for p in successful if p.lineup_status != LineupStatus.UNKNOWN]
        state.source_agreement = len(set(l.value for l in lineups)) <= 1

        # -----------------------------------------------------------------------
        # Step 4: Per-field statuses for merged fields
        # -----------------------------------------------------------------------
        _set_status(state, "starter_probability",   successful, "starter_probability")
        _set_lineup_status(state, successful)
        _set_status(state, "rotation_confidence",   successful, "rotation_confidence")
        _set_component_status(state, successful)
        _set_event_status(state, successful, event_status)
        _set_player_status(state, successful, player_status)

        # -----------------------------------------------------------------------
        # Step 5: Board identity + market comparison
        # -----------------------------------------------------------------------
        board_line = _safe_float(row.get("line") or row.get("threshold"))
        board_raw = {
            "platform":       "prizepicks",
            "participant_id": player.lower() if player else None,
            "event_id":       event_id,
            "event_date":     event_date,
            "period":         "full_game",
            "stat_family":    prop_type,
            "exact_line":     board_line,
            "side":           row.get("side") or row.get("pick"),
        }
        board_identity = canonicalize(board_raw)

        # Compare against sportsbook odds entries
        market_comparison_results: list[dict] = []
        sportsbook_odds = enrichment.get("sportsbook_odds") or []
        for book_entry in sportsbook_odds:
            sb_raw = dict(book_entry)
            sb_raw.setdefault("stat_family", prop_type)
            sb_identity = canonicalize(sb_raw)
            match_result = compare_identity(board_identity, sb_identity)
            market_comparison_results.append({
                "sportsbook": book_entry.get("team") or book_entry.get("book") or "unknown",
                "match":      match_result.match.value,
                "explanation": match_result.explanation,
            })

        # -----------------------------------------------------------------------
        # Step 6: Invalidation check
        # -----------------------------------------------------------------------
        invalidation_result = _invalidation_tracker.check_and_invalidate(
            row               = row,
            new_opportunity_state = state,
            new_board_line    = board_line,
        )

        # -----------------------------------------------------------------------
        # Step 7: Composite confidence
        # -----------------------------------------------------------------------
        state.composite_confidence = _compute_composite_confidence(state, packets)

        # -----------------------------------------------------------------------
        # Step 8: Reconcile NOT_CALLED → DATA_UNOBTAINABLE
        # -----------------------------------------------------------------------
        state.reconcile()

        # Attach board identity and market comparison / invalidation to notes
        state.notes.append(
            f"board_identity: stat_family={board_identity.stat_family} "
            f"line={board_identity.exact_line} side={board_identity.side}"
        )
        if market_comparison_results:
            state.notes.append(
                f"market_comparison: {len(market_comparison_results)} sportsbook entries compared"
            )
        if invalidation_result.needs_rerun:
            state.notes.append(
                f"INVALIDATION:{invalidation_result.invalidation_reason}"
            )
            row.setdefault("blockers", []).append(
                f"OPPORTUNITY_ACQUISITION:INVALIDATED:{invalidation_result.invalidation_reason}"
            )

        # Store full report on row
        report = state.to_dict()
        report["board_identity"]         = board_identity.to_dict()
        report["market_comparison"]       = market_comparison_results
        report["invalidation_result"]     = invalidation_result.to_dict()
        report["quorum_result"]           = quorum_result.to_dict()
        row.setdefault("gates", {})["opportunity_acquisition"] = report

        # Expose minutes_conflict_penalty on enrichment for downstream gates
        if state.minutes_conflict_penalty > 0:
            enrichment["minutes_conflict_penalty"] = state.minutes_conflict_penalty
            enrichment["source_conflict"] = True

        return state


# ---------------------------------------------------------------------------
# Helper: is this row an NBA/WNBA composite prop?
# ---------------------------------------------------------------------------

def is_composite_prop_row(row: dict[str, Any]) -> bool:
    """Returns True for NBA/WNBA composite prop rows that need orchestration."""
    sport     = (row.get("sport") or "").lower()
    prop_type = (row.get("prop_type") or row.get("market_type") or "").lower().replace(" ", "")
    if sport not in _NBA_WNBA_SPORTS:
        return False
    from gate_engine.component_composite import STAT_FAMILY_ALIASES
    canonical = STAT_FAMILY_ALIASES.get(prop_type, prop_type)
    return canonical in _COMPOSITE_FAMILIES


# ---------------------------------------------------------------------------
# Merge helpers
# ---------------------------------------------------------------------------

def _first_non_none(packets: list[VendorPacket], attr: str) -> float | None:
    for p in packets:
        v = getattr(p, attr, None)
        if v is not None:
            return v
    return None


def _first_str(packets: list[VendorPacket], attr: str) -> str | None:
    for p in packets:
        v = getattr(p, attr, None)
        if v is not None:
            return str(v)
    return None


def _first_lineup(packets: list[VendorPacket]) -> LineupStatus:
    priority = [LineupStatus.CONFIRMED, LineupStatus.EXPECTED, LineupStatus.UNCONFIRMED]
    best = LineupStatus.UNKNOWN
    for p in packets:
        if p.lineup_status in priority:
            idx_new  = priority.index(p.lineup_status)
            try:
                idx_best = priority.index(best)
            except ValueError:
                idx_best = len(priority)
            if idx_new < idx_best:
                best = p.lineup_status
    return best


def _first_component_opp(packets: list[VendorPacket]) -> ComponentOpportunityRates | None:
    for p in packets:
        if p.component_opportunity is not None:
            return p.component_opportunity
    return None


def _merge_teammate_status(packets: list[VendorPacket]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for p in reversed(packets):  # later (lower priority) overwritten by earlier
        merged.update(p.teammate_status)
    return merged


def _safe_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Per-field status setters
# ---------------------------------------------------------------------------

def _set_status(
    state: OpportunityState,
    field_name: str,
    packets: list[VendorPacket],
    attr: str,
) -> None:
    if getattr(state, field_name, None) is not None:
        contributing = [p for p in packets if getattr(p, attr, None) is not None]
        if len(contributing) >= 2:
            state.per_field_statuses[field_name] = AcquisitionStatus.RETRIEVED
        elif len(contributing) == 1:
            state.per_field_statuses[field_name] = AcquisitionStatus.RECONSTRUCTED
        else:
            state.per_field_statuses[field_name] = AcquisitionStatus.DATA_UNOBTAINABLE
    else:
        state.per_field_statuses[field_name] = AcquisitionStatus.DATA_UNOBTAINABLE


def _set_lineup_status(state: OpportunityState, packets: list[VendorPacket]) -> None:
    known = [p for p in packets if p.lineup_status != LineupStatus.UNKNOWN]
    if state.lineup_status == LineupStatus.UNKNOWN:
        state.per_field_statuses["lineup_status"] = AcquisitionStatus.DATA_UNOBTAINABLE
    elif len(known) >= 2:
        state.per_field_statuses["lineup_status"] = AcquisitionStatus.RETRIEVED
    else:
        state.per_field_statuses["lineup_status"] = AcquisitionStatus.RECONSTRUCTED


def _set_component_status(state: OpportunityState, packets: list[VendorPacket]) -> None:
    has = [p for p in packets if p.component_opportunity is not None]
    if state.component_opportunity is not None:
        state.per_field_statuses["component_opportunity"] = (
            AcquisitionStatus.RETRIEVED if len(has) >= 2
            else AcquisitionStatus.RECONSTRUCTED
        )
    else:
        state.per_field_statuses["component_opportunity"] = AcquisitionStatus.DATA_UNOBTAINABLE


def _set_event_status(
    state: OpportunityState,
    packets: list[VendorPacket],
    event_status: str | None,
) -> None:
    has = [p for p in packets if p.event_status is not None]
    if event_status:
        state.per_field_statuses["event_status"] = (
            AcquisitionStatus.RETRIEVED if len(has) >= 2 else AcquisitionStatus.RECONSTRUCTED
        )
    else:
        state.per_field_statuses["event_status"] = AcquisitionStatus.DATA_UNOBTAINABLE


def _set_player_status(
    state: OpportunityState,
    packets: list[VendorPacket],
    player_status: str | None,
) -> None:
    has = [p for p in packets if p.player_status is not None]
    if player_status:
        state.per_field_statuses["player_status"] = (
            AcquisitionStatus.RETRIEVED if len(has) >= 2 else AcquisitionStatus.RECONSTRUCTED
        )
    else:
        state.per_field_statuses["player_status"] = AcquisitionStatus.DATA_UNOBTAINABLE


# ---------------------------------------------------------------------------
# Composite confidence computation
# ---------------------------------------------------------------------------

def _compute_composite_confidence(
    state: OpportunityState,
    packets: list[VendorPacket],
) -> float:
    """
    Composite confidence score (0.0–1.0) based on:
    - Number of successful sources
    - Quorum agreement
    - Lineup status certainty
    - Conflict penalty
    """
    n_success    = sum(1 for p in packets if p.request_status == "success")
    base         = min(1.0, n_success * 0.20)
    quorum_bonus = 0.20 if state.minutes_source_agreement else 0.0
    lineup_bonus = {
        LineupStatus.CONFIRMED:   0.25,
        LineupStatus.EXPECTED:    0.15,
        LineupStatus.UNCONFIRMED: 0.05,
        LineupStatus.UNKNOWN:     0.00,
    }.get(state.lineup_status, 0.0)
    conflict_pen = state.minutes_conflict_penalty * 0.30

    confidence = base + quorum_bonus + lineup_bonus - conflict_pen
    return round(max(0.0, min(1.0, confidence)), 3)
