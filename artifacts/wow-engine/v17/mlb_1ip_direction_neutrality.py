"""Direction-neutral MLB 1IP comparison audit for canonical V17 prop runs.

This module never scores a sporting probability and never changes a model result.
It verifies that, whenever the source board exposes both MORE and LESS, both
directions reached the canonical scorer and compares the returned governed
calibrated lower bounds without applying any directional preference or haircut.

can_execute remains false.
"""
from __future__ import annotations

from typing import Any, Iterable

MLB_1IP_STAT_TYPE = "1ST_INNING_PITCHES_THROWN"
CAN_EXECUTE = False


def _normalized_stat_type(value: object) -> str:
    return str(value or "").strip().upper().replace(" ", "_")


def _directions(line: dict[str, Any]) -> list[str]:
    values = [str(v).strip().upper() for v in (line.get("available_directions") or [])]
    return list(dict.fromkeys(v for v in values if v in {"MORE", "LESS"}))


def _lower_bound(row: dict[str, Any] | None) -> float | None:
    if not row:
        return None
    for key in (
        "calibrated_lower_bound",
        "calibrated_probability_lower_bound",
        "calibrated_prob_lower_bound",
    ):
        value = row.get(key)
        if value is not None:
            return float(value)
    return None


def _row_snapshot(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {
            "present": False,
            "model_evaluated": False,
            "calibrated_lower_bound": None,
            "terminal_status": None,
            "code": None,
            "terminal_label": None,
            "rank_eligible": False,
            "probability_publishable": False,
        }
    return {
        "present": True,
        "model_evaluated": row.get("model_evaluated") is True,
        "calibrated_lower_bound": _lower_bound(row),
        "terminal_status": row.get("terminal_status"),
        "code": row.get("code"),
        "terminal_label": row.get("terminal_label"),
        "rank_eligible": row.get("rank_eligible") is True,
        "probability_publishable": row.get("probability_publishable") is True,
    }


def build_direction_neutral_1ip_audit(
    source_lines: Iterable[dict[str, Any]],
    compact_rows: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Audit 1IP direction coverage and compare sides without mutating probabilities."""

    rows = [dict(row) for row in compact_rows]
    rows_by_key = {
        str(row.get("row_key")): row
        for row in rows
        if row.get("row_key") is not None
    }

    comparisons: list[dict[str, Any]] = []
    comparison_winners: list[dict[str, Any]] = []
    source_count = 0
    bidirectional_expected = 0
    bidirectional_complete = 0
    single_direction_offers = 0
    more_preferred = 0
    less_preferred = 0
    ties = 0
    incomplete_pairs = 0

    for source in source_lines:
        if _normalized_stat_type(source.get("stat_type")) != MLB_1IP_STAT_TYPE:
            continue

        source_count += 1
        line_key = str(source.get("row_key") or "")
        offered = _directions(source)
        direction_rows = {
            direction: rows_by_key.get(f"{line_key}-{direction}")
            for direction in offered
        }
        snapshots = {
            direction: _row_snapshot(direction_rows.get(direction))
            for direction in offered
        }

        preferred_direction: str | None = None
        preferred_lower_bound: float | None = None
        comparison_status = "NO_VALID_DIRECTION"

        if set(offered) == {"MORE", "LESS"}:
            bidirectional_expected += 1
            more_lb = snapshots["MORE"]["calibrated_lower_bound"]
            less_lb = snapshots["LESS"]["calibrated_lower_bound"]
            more_complete = snapshots["MORE"]["model_evaluated"] and more_lb is not None
            less_complete = snapshots["LESS"]["model_evaluated"] and less_lb is not None

            if more_complete and less_complete:
                bidirectional_complete += 1
                comparison_status = "BIDIRECTIONAL_COMPLETE"
                if abs(float(more_lb) - float(less_lb)) <= 1e-12:
                    ties += 1
                    comparison_status = "BIDIRECTIONAL_TIE"
                elif float(more_lb) > float(less_lb):
                    preferred_direction = "MORE"
                    preferred_lower_bound = float(more_lb)
                    more_preferred += 1
                else:
                    preferred_direction = "LESS"
                    preferred_lower_bound = float(less_lb)
                    less_preferred += 1
            else:
                incomplete_pairs += 1
                comparison_status = "BIDIRECTIONAL_INCOMPLETE"
        elif len(offered) == 1:
            single_direction_offers += 1
            direction = offered[0]
            lower = snapshots[direction]["calibrated_lower_bound"]
            if snapshots[direction]["model_evaluated"] and lower is not None:
                preferred_direction = direction
                preferred_lower_bound = float(lower)
                comparison_status = "SINGLE_DIRECTION_OFFER_COMPLETE"
            else:
                comparison_status = "SINGLE_DIRECTION_OFFER_INCOMPLETE"
        else:
            comparison_status = "DIRECTION_SET_INVALID"

        comparison = {
            "line_key": line_key,
            "player": source.get("player"),
            "team": source.get("team"),
            "opponent": source.get("opponent"),
            "stat_type": source.get("stat_type"),
            "line": source.get("line"),
            "offered_directions": offered,
            "directions": snapshots,
            "comparison_status": comparison_status,
            "preferred_direction": preferred_direction,
            "preferred_calibrated_lower_bound": preferred_lower_bound,
            "selection_basis": (
                "GOVERNED_CALIBRATED_LOWER_BOUND_ONLY"
                if preferred_direction is not None
                else None
            ),
            "probability_mutated": False,
            "can_execute": False,
        }
        comparisons.append(comparison)

        if preferred_direction is not None:
            chosen_row = direction_rows.get(preferred_direction) or {}
            comparison_winners.append(
                {
                    "line_key": line_key,
                    "player": source.get("player"),
                    "opponent": source.get("opponent"),
                    "line": source.get("line"),
                    "direction": preferred_direction,
                    "calibrated_lower_bound": preferred_lower_bound,
                    "terminal_status": chosen_row.get("terminal_status"),
                    "terminal_label": chosen_row.get("terminal_label"),
                    "rank_eligible": chosen_row.get("rank_eligible") is True,
                    "probability_publishable": chosen_row.get("probability_publishable") is True,
                    "comparison_only": True,
                    "can_execute": False,
                }
            )

    comparison_winners.sort(
        key=lambda row: (
            row["calibrated_lower_bound"]
            if row["calibrated_lower_bound"] is not None
            else float("-inf")
        ),
        reverse=True,
    )

    return {
        "contract_version": "V17_MLB_1IP_DIRECTION_NEUTRAL_V1",
        "rule": (
            "When both MORE and LESS are offered, score both through the same governed "
            "path and compare only returned calibrated lower bounds; no direction gets "
            "a default preference or assistant-applied haircut."
        ),
        "source_1ip_lines": source_count,
        "bidirectional_expected": bidirectional_expected,
        "bidirectional_complete": bidirectional_complete,
        "single_direction_offers": single_direction_offers,
        "more_preferred": more_preferred,
        "less_preferred": less_preferred,
        "ties": ties,
        "incomplete_pairs": incomplete_pairs,
        "comparisons": comparisons,
        "comparison_winners_by_lower_bound": comparison_winners,
        "probabilities_mutated": False,
        "can_execute": False,
    }
