"""Bridge full governed NFL source rows into the weekly Pick'em equity challenger.

The normal MAX_EXPECTED_CORRECT board intentionally transports only the selected
side's calibration bounds. A weekly-equity card may select the opposite side, so
this bridge reads the already-produced full two-sided calibration package from the
governed source rows, enriches the downstream board, and then invokes the contest
optimizer. It never derives missing bounds and never alters sporting probability.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from v17.nfl_pickem_pool_optimizer import DECISION_OBJECTIVE, build_pickem_board
from v17.nfl_pickem_weekly_equity import optimize_weekly_win_equity

CAN_EXECUTE = False


def _event_id(row: Mapping[str, Any]) -> str:
    envelope = row.get("candidate_envelope")
    envelope = envelope if isinstance(envelope, Mapping) else {}
    return str(
        envelope.get("official_event_id")
        or row.get("canonical_event_id")
        or row.get("official_event_id")
        or ""
    ).strip()


def _enrich_two_sided_bounds(
    board: dict[str, Any],
    source_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    by_event: dict[str, dict[str, Any]] = {}
    duplicate_ids: set[str] = set()
    for row in source_rows:
        event_id = _event_id(row)
        if not event_id:
            continue
        if event_id in by_event:
            duplicate_ids.add(event_id)
        else:
            by_event[event_id] = row

    enriched = dict(board)
    picks: list[dict[str, Any]] = []
    for raw_pick in board.get("picks") or []:
        pick = dict(raw_pick)
        event_id = str(pick.get("official_event_id") or "")
        source = by_event.get(event_id)
        if source is not None and event_id not in duplicate_ids:
            for field in (
                "calibrated_home_lower_bound",
                "calibrated_home_upper_bound",
                "calibrated_away_lower_bound",
                "calibrated_away_upper_bound",
            ):
                pick[field] = source.get(field)
        picks.append(pick)
    enriched["picks"] = picks
    enriched["weekly_equity_two_sided_bounds_from_governed_source"] = True
    enriched["can_execute"] = False
    return enriched


def optimize_weekly_win_equity_from_governed_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    expected_game_count: int,
    pool_entries: int,
    opponent_pick_shares: Iterable[Mapping[str, Any]],
    max_candidate_flips: int = 3,
) -> dict[str, Any]:
    """Build the baseline board and optimize weekly equity from the same source rows."""
    source_rows = [dict(row) for row in rows]
    board = build_pickem_board(
        source_rows,
        expected_game_count=expected_game_count,
        strategy_mode=DECISION_OBJECTIVE,
    )
    enriched = _enrich_two_sided_bounds(board, source_rows)
    result = optimize_weekly_win_equity(
        enriched,
        pool_entries=pool_entries,
        opponent_pick_shares=opponent_pick_shares,
        max_candidate_flips=max_candidate_flips,
    )
    result["baseline_board_status"] = board.get("status")
    result["baseline_ready_pick_count"] = board.get("ready_pick_count")
    result["baseline_blocked_event_count"] = board.get("blocked_event_count")
    result["weekly_equity_two_sided_bounds_from_governed_source"] = True
    result["can_execute"] = False
    return result


__all__ = [
    "CAN_EXECUTE",
    "optimize_weekly_win_equity_from_governed_rows",
]
