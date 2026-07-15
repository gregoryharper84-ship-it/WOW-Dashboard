"""
opportunity_state.py — WOW-PATCH-2026-07-15 Section 5: Opportunity-State Consistency

Project team totals first, then allocate them across active players.
Every candidate must inherit the same player opportunity state.

If multiple player LESS selections remove opportunity and allocation does
not reconcile, return REJECT_OPPORTUNITY_SUM_MISMATCH.
"""
from __future__ import annotations

from typing import Any

from .labels import PropLabel


# Stat categories that represent player opportunity
OPPORTUNITY_STATS: frozenset[str] = frozenset({
    "points", "pts",
    "rebounds", "reb",
    "assists", "ast",
    "fga", "field goals attempted",
    "minutes", "min",
})


def _normalize_stat(raw: str) -> str:
    aliases = {
        "points": "points", "pts": "points",
        "rebounds": "rebounds", "reb": "rebounds",
        "assists": "assists", "ast": "assists",
        "fga": "fga", "field goals attempted": "fga",
        "minutes": "minutes", "min": "minutes",
    }
    return aliases.get(raw.lower().strip(), raw.lower().strip())


def run(
    rows: list[dict[str, Any]],
    team_totals: dict[str, float] | None = None,
) -> dict[str, Any]:
    """
    Validate opportunity-state consistency across a set of rows.

    team_totals: optional dict of {"points": 110.5, "rebounds": 42.0, ...}
                 representing the projected team totals for this game.

    If team_totals is provided, verify that the sum of player LESS
    projections does not create an irreconcilable deficit (i.e., more
    opportunity is removed than the team total allows).

    Returns a summary report. Conflicting rows are stamped with
    REJECT_OPPORTUNITY_SUM_MISMATCH.
    """
    if not rows:
        return {"passed": True, "checked": 0, "conflicts": []}

    # Group by (game/event, stat)
    # Count LESS-direction entries per stat per event
    event_stat_less: dict[tuple[str, str], list[dict]] = {}
    event_stat_more: dict[tuple[str, str], list[dict]] = {}

    for row in rows:
        direction = (row.get("direction") or "MORE").upper()
        stat      = _normalize_stat(row.get("prop_type") or "")
        event_id  = row.get("game") or row.get("event_id") or "UNKNOWN_EVENT"
        key       = (event_id, stat)

        if stat not in OPPORTUNITY_STATS:
            continue
        if direction in ("LESS", "UNDER"):
            event_stat_less.setdefault(key, []).append(row)
        else:
            event_stat_more.setdefault(key, []).append(row)

    conflicts: list[dict[str, Any]] = []

    # Rule: If 3+ LESS entries on the same stat in the same event,
    # and no team_totals reconciliation is available, flag mismatch.
    LESS_THRESHOLD = 3

    for (event_id, stat), less_rows in event_stat_less.items():
        if len(less_rows) < LESS_THRESHOLD:
            continue

        # Try to reconcile against team totals
        if team_totals and stat in team_totals:
            team_cap = team_totals[stat]
            # Sum of individual LESS lines — proxy for opportunity claimed
            total_cap = sum(
                (r.get("line") or 0) for r in less_rows
            )
            # If individual caps sum to less than the team total, there's
            # still opportunity somewhere — technically reconcilable.
            # If they sum to more than the team total, that's a mismatch.
            if total_cap <= team_cap:
                continue  # reconciled

        # Cannot reconcile — flag
        conflict = {
            "event_id":         event_id,
            "stat":             stat,
            "less_count":       len(less_rows),
            "team_total":       (team_totals or {}).get(stat),
            "individual_lines": [r.get("line") for r in less_rows],
            "detail": (
                f"{len(less_rows)} player LESS entries on {stat} in event {event_id} "
                "without reconciled team opportunity allocation"
            ),
        }
        conflicts.append(conflict)

        for row in less_rows:
            if row.get("terminal_label") is None:
                row["terminal_label"] = PropLabel.REJECT_OPPORTUNITY_SUM_MISMATCH.value
            row.setdefault("blockers", []).append(
                f"REJECT_OPPORTUNITY_SUM_MISMATCH:{stat}:{len(less_rows)}_unders_unreconciled"
            )
            row.setdefault("gates", {})["opportunity_state"] = {
                "passed":   False,
                "conflict": conflict,
            }

    # Stamp clean rows
    for row in rows:
        if "opportunity_state" not in row.get("gates", {}):
            row.setdefault("gates", {})["opportunity_state"] = {
                "passed":   True,
                "conflict": None,
            }

    return {
        "passed":    len(conflicts) == 0,
        "checked":   len(rows),
        "conflicts": conflicts,
    }
