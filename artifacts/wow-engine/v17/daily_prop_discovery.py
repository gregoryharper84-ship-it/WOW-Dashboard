"""One globally bounded NFL + MLB V17 Daily prop evidence discovery dispatcher.

Both sport producers use existing verified event/player hydration. Neither
discovery half-point threshold is a sportsbook line or publication authority.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from v17.daily_prop_acquisition import acquire_daily_prop_snapshots as acquire_mlb
from v17.nfl_daily_prop_acquisition import acquire_daily_nfl_prop_snapshots as acquire_nfl

CAN_EXECUTE = False
COUNTER_FIELDS = (
    "attempted", "hydrated", "persisted", "held",
    "snapshot_write_succeeded", "snapshot_write_failed",
    "explicit_prewrite_exclusions",
)


def acquire_daily_prop_snapshots(
    *,
    db: Any,
    requested_date: str,
    requested_timezone: str,
    max_candidates: int,
    now: datetime | None = None,
    rotating_window_slot: int | None = None,
    nfl_only: bool = False,
) -> dict[str, Any]:
    """Preserve the old Daily acquisition contract while adding NFL.

    NFL gets at most half the budget initially; MLB receives any unused NFL
    share. An unused MLB share can then be applied to the next NFL offset.
    The sum of every attempt remains <= the original global bound.
    """
    ceiling = max(0, min(int(max_candidates), 200))
    outcomes: list[dict[str, Any]] = []
    total_attempted = 0

    def run(sport: str, limit: int, *, offset: int = 0) -> dict[str, Any]:
        nonlocal total_attempted
        limit = min(int(limit), ceiling - total_attempted, 64 if sport == "NFL" else 200)
        if limit <= 0:
            return {"status": "COMPLETED", "sport": sport, "attempted": 0,
                    "persisted": 0, "receipts": [], "blockers": [], "next_offset": None,
                    "can_execute": False}
        try:
            shared = {
                "db": db,
                "requested_date": requested_date,
                "requested_timezone": requested_timezone,
                "max_candidates": limit,
                "now": now,
            }
            outcome = (
                acquire_nfl(**shared, candidate_offset=offset, **(
                    {"rotating_window_slot": rotating_window_slot}
                    if offset == 0 and rotating_window_slot is not None else {}
                ))
                if sport == "NFL"
                else acquire_mlb(**shared, **(
                    {"rotating_window_slot": rotating_window_slot}
                    if rotating_window_slot is not None else {}
                ))
            )
        except Exception as exc:
            outcome = {
                "status": "DATA_UNOBTAINABLE", "sport": sport, "attempted": 0,
                "persisted": 0, "receipts": [], "next_offset": None,
                "blockers": [f"{sport}_DAILY_DISCOVERY_FAILED:{type(exc).__name__}"],
                "can_execute": False,
            }
        # A producer cannot overclaim attempts or publication authority.
        attempted = int(outcome.get("attempted") or 0)
        if attempted < 0 or attempted > limit:
            # Never trust a producer that violates the attempt budget; preserve
            # a typed per-sport error rather than crashing the Daily route.
            outcome = {
                "status": "DATA_UNOBTAINABLE", "sport": sport, "attempted": 0,
                "persisted": 0, "receipts": [], "next_offset": None,
                "blockers": ["PROP_DAILY_DISCOVERY_BUDGET_CONTRACT_INVALID"],
                "can_execute": False,
            }
            attempted = 0
        total_attempted += attempted
        outcome["probability_publishable"] = False
        outcome["rank_eligible"] = False
        outcome["can_execute"] = False
        outcomes.append(outcome)
        return outcome

    if nfl_only:
        # An already-populated MLB canonical slate does not require rehydrating
        # its rows. Reserve the entire bounded fill attempt budget for the
        # absent NFL family, advancing source offsets in the same invocation.
        offset = 0
        for _ in range(4):  # ceiling <= 200, NFL window <= 64
            if total_attempted >= ceiling:
                break
            packet = run("NFL", ceiling - total_attempted, offset=offset)
            next_offset = packet.get("next_offset")
            if next_offset is None or int(next_offset) <= offset:
                break
            offset = int(next_offset)
    else:
        first_nfl_limit = min(64, (ceiling + 1) // 2)
        first_nfl = run("NFL", first_nfl_limit)
        run("MLB", ceiling - total_attempted)
        # Continue where NFL left off if MLB did not consume its share.
        if total_attempted < ceiling and first_nfl.get("next_offset") is not None:
            run("NFL", ceiling - total_attempted, offset=int(first_nfl["next_offset"]))

    result: dict[str, Any] = {
        "status": "COMPLETED",
        **{name: sum(int(item.get(name) or 0) for item in outcomes) for name in COUNTER_FIELDS},
        "receipts": [dict(r) for item in outcomes for r in item.get("receipts", []) if isinstance(r, dict)],
        "blockers": list(dict.fromkeys(str(b) for item in outcomes for b in item.get("blockers", []))),
        "per_sport": outcomes,
        "candidate_source": (
            "NFL_ESPN_PREGAME_ROSTER"
            if nfl_only else "NFL_ESPN_PREGAME_AND_MLB_OFFICIAL_PROBABLE_PITCHERS"
        ),
        "line_source": "PRIOR10_MEDIAN_HALF_POINT_DISCOVERY_ONLY",
        "can_execute": False,
    }
    if result["attempted"] > ceiling:
        raise RuntimeError("PROP_DAILY_DISCOVERY_BUDGET_CONTRACT_INVALID")
    if result["snapshot_write_failed"] and not result["snapshot_write_succeeded"]:
        result["status"] = "RUN_INVALID_PROP_SNAPSHOT_WRITE_FAILURE"
    elif result["blockers"]:
        result["status"] = "COMPLETED_WITH_ROW_BLOCKERS" if any(
            item.get("status") != "DATA_UNOBTAINABLE" for item in outcomes
        ) else "DATA_UNOBTAINABLE"
    return result
