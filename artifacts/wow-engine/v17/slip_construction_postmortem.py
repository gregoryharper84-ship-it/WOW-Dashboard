"""Independent slip-*construction* retrospective over existing settled postmortem rows.

This deliberately does not score any sporting forecast. It consumes already
normalized postmortem legs/positions and grades the *construction process*:
shared losing hinges, duplicated selections, same-game exposure, all-or-nothing
fragility, and missing prelock/joint-quote evidence. Observed hypothetical cards
are never represented as accepted tickets or profit.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from typing import Any, Mapping, Sequence

from v17.slip_portfolio_optimizer import component_composite_overlap

CAN_EXECUTE = False
SCHEMA_VERSION = "wow.v17.slip_construction_postmortem.v1"
_ALLOWED = {"WIN", "LOSS", "PUSH", "VOID"}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _selection_key(leg: Mapping[str, Any]) -> tuple[Any, ...]:
    # Identical to the postmortem engine's *observed-field* grouping, not a
    # replacement for authenticated official event/market/settlement identity.
    return (
        _norm(leg.get("sport")),
        _norm(leg.get("league")),
        _norm(leg.get("event_id")),
        _norm(leg.get("participant")),
        _norm(leg.get("market")),
        _norm(leg.get("side")),
        leg.get("exact_line"),
        _norm(leg.get("selection")),
    )


def _digest(key: tuple[Any, ...]) -> str:
    value = json.dumps(key, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_slip_construction_postmortem(
    legs: Sequence[Mapping[str, Any]], positions: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Create a separate, read-only slip process audit; never infer joint P/ROI.

    Inputs are existing settled, normalized postmortem records (or persisted
    equivalents), not a newly manufactured pregame model receipt. This
    retrospective is separate from the model's Brier/log-loss/prop calibration.
    """
    by_position: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    selection_positions: dict[tuple[Any, ...], set[str]] = defaultdict(set)
    selection_results: dict[tuple[Any, ...], set[str]] = defaultdict(set)
    player_event_positions: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    seen_row_ids: set[tuple[str, str]] = set()
    position_map: dict[str, Mapping[str, Any]] = {}

    for position in positions:
        if not isinstance(position, Mapping):
            raise ValueError("SLIP_POSTMORTEM_POSITION_INVALID")
        ref = str(position.get("position_reference") or "").strip()
        if not ref or ref in position_map:
            raise ValueError("SLIP_POSTMORTEM_POSITION_ID_INVALID_OR_DUPLICATE")
        position_map[ref] = position

    for leg in legs:
        if not isinstance(leg, Mapping):
            raise ValueError("SLIP_POSTMORTEM_LEG_INVALID")
        ref = str(leg.get("position_reference") or "").strip()
        if ref not in position_map:
            raise ValueError("SLIP_POSTMORTEM_UNMATCHED_POSITION")
        row_key = str(leg.get("row_key") or "").strip()
        if not row_key or (ref, row_key) in seen_row_ids:
            raise ValueError("SLIP_POSTMORTEM_ROW_KEY_INVALID_OR_DUPLICATE")
        seen_row_ids.add((ref, row_key))
        result = str(leg.get("settled_result") or "").strip().upper()
        if result not in _ALLOWED:
            raise ValueError("SLIP_POSTMORTEM_RESULT_INVALID")
        by_position[ref].append(leg)
        key = _selection_key(leg)
        selection_positions[key].add(ref)
        selection_results[key].add(result)
        if all((_norm(leg.get("sport")), _norm(leg.get("event_id")),
                _norm(leg.get("participant")))):
            player_event_positions[(
                _norm(leg.get("sport")), _norm(leg.get("event_id")),
                _norm(leg.get("participant"))
            )].add(ref)

    if any(len(results) != 1 for results in selection_results.values()):
        raise ValueError("CONFLICTING_EXACT_SELECTION_SETTLEMENT")

    repeated = {key: refs for key, refs in selection_positions.items() if len(refs) > 1}
    shared_losses = [
        {
            "observed_selection_hash": _digest(key),
            "position_references": sorted(refs),
            "all_or_nothing_positions": sorted(
                ref for ref in refs
                if position_map[ref].get("all_or_nothing") is True
            ),
            "position_count": len(refs),
        }
        for key, refs in repeated.items() if selection_results[key] == {"LOSS"}
    ]
    shared_losses.sort(key=lambda x: x["observed_selection_hash"])
    affected = set().union(
        *(set(item["all_or_nothing_positions"]) for item in shared_losses)
    ) if shared_losses else set()

    cards = []
    for ref, position in position_map.items():
        items = by_position.get(ref, [])
        if not items or len(items) != position.get("underlying_market_count"):
            raise ValueError("SLIP_POSTMORTEM_LEG_COUNT_RECONCILIATION_FAILED")
        counts = Counter(str(x.get("settled_result") or "").upper() for x in items)
        events = Counter(
            (_norm(x.get("sport")), _norm(x.get("event_id")))
            for x in items if _norm(x.get("sport")) and _norm(x.get("event_id"))
        )
        same_event_count = sum(v - 1 for v in events.values() if v > 1)
        player_counts = Counter(
            (_norm(x.get("sport")), _norm(x.get("event_id")),
             _norm(x.get("participant")))
            for x in items if all((
                _norm(x.get("sport")), _norm(x.get("event_id")),
                _norm(x.get("participant"))
            ))
        )
        same_player_count = sum(v - 1 for v in player_counts.values() if v > 1)
        composite_pairs = [
            sorted((str(left.get("row_key")), str(right.get("row_key"))))
            for index, left in enumerate(items)
            for right in items[index + 1:]
            if component_composite_overlap(dict(left), dict(right))
        ]
        reused = sorted({
            _digest(_selection_key(item))
            for item in items if _selection_key(item) in repeated
        })
        flags = ["PREGAME_BUILD_RECEIPT_UNVERIFIED", "ACCEPTED_PAYOUT_QUOTE_UNVERIFIED",
                 "AUTHENTICATED_SUBMISSION_UNVERIFIED", "JOINT_SCENARIO_RECEIPT_UNVERIFIED"]
        if reused:
            flags.append("CROSS_SLIP_SHARED_THESIS_EXPOSURE")
        if same_event_count:
            flags.append("SAME_EVENT_DEPENDENCY_UNVERIFIED")
        if same_player_count:
            flags.append("SAME_PLAYER_MULTI_MARKET_DEPENDENCE_UNVERIFIED")
        if composite_pairs:
            flags.append("COMPONENT_COMPOSITE_OVERLAP_REQUIRES_JOINT_REVIEW")
        if len(items) >= 4:
            flags.append("FOUR_PLUS_LEGS_FRAGILITY")
        if any(not _norm(x.get("event_id")) for x in items):
            flags.append("OFFICIAL_EVENT_ID_MISSING")
        if counts["LOSS"]:
            observed = "HAS_RECORDED_LOSS"
        elif counts["PUSH"] or counts["VOID"]:
            observed = "PUSH_OR_VOID_REQUIRES_PLATFORM_SETTLEMENT"
        else:
            observed = "ALL_RECORDED_LEGS_WIN"
        cards.append({
            "position_reference": ref,
            "position_structure": str(position.get("position_structure") or ""),
            "all_or_nothing": position.get("all_or_nothing") is True,
            "leg_count": len(items),
            "distinct_sport_count": len({_norm(x.get("sport")) for x in items if _norm(x.get("sport"))}),
            "same_event_extra_legs": same_event_count,
            "same_player_event_extra_legs": same_player_count,
            "component_composite_overlap_row_pairs": sorted(composite_pairs),
            "reused_selection_hashes": reused,
            "observed_result": observed,
            "observed_leg_wins": counts["WIN"],
            "observed_leg_losses": counts["LOSS"],
            "observed_leg_pushes": counts["PUSH"],
            "observed_leg_voids": counts["VOID"],
            "observed_all_legs_clear": observed == "ALL_RECORDED_LEGS_WIN",
            "shared_loss_hinge_exposure": ref in affected,
            "construction_flags": sorted(flags),
            "accepted_ticket_settlement": None,
            "realized_profit_verified": False,
            "can_execute": False,
        })

    unique_counts = {result: 0 for result in sorted(_ALLOWED)}
    for results in selection_results.values():
        unique_counts[next(iter(results))] += 1

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "RETROSPECTIVE_CONSTRUCTION_RESEARCH_ONLY",
        "scope": "SLIP_BUILD_PROCESS_SEPARATE_FROM_SPORTING_FORECAST_GRADING",
        "position_count": len(cards),
        "distinct_selection_count": len(selection_results),
        "unique_selection_result_counts": unique_counts,
        "reused_selection_group_count": len(repeated),
        "cross_slip_same_player_event_groups": sum(
            len(refs) > 1 for refs in player_event_positions.values()
        ),
        "shared_losing_hinge_count": len(shared_losses),
        "shared_losing_hinges": shared_losses,
        "affected_all_or_nothing_position_count": len(affected),
        "cards": cards,
        "source_identity_basis": "EXACT_OBSERVED_FIELDS_NOT_OFFICIAL_IDENTITY_ATTESTATION",
        "independent_joint_probability": None,
        "modeled_expected_value": None,
        "authenticated_submission_verified": False,
        "accepted_quote_verified": False,
        "realized_roi_verified": False,
        "auto_promote_model": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }


__all__ = ["CAN_EXECUTE", "SCHEMA_VERSION", "build_slip_construction_postmortem"]
