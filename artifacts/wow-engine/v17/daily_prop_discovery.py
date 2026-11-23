"""Multi-sport autonomous prop discovery for the V17 Daily props lane.

Daily previously owned exactly one autonomous candidate producer (MLB probable
pitchers). A healthy NFL board could therefore only appear on Daily as
"previously stored rows reused", and an empty NFL slate was indistinguishable
from a missing producer.

This dispatcher runs every certified autonomous producer under one bounded,
deterministic budget and returns the same acquisition receipt contract Daily
already reconciles. It preserves the existing hard row-attempt ceiling: the
per-sport share divides the caller's budget, and unused share rolls forward, so
adding sports cannot multiply a Daily or scheduler budget.

Accounting separates newly acquired candidates from reused canonical snapshots.
No probability, line invention, scoring, calibration, certification, promotion,
or publication authority is created here. ``can_execute`` is always false.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Callable

import httpx

from v17.daily_prop_acquisition import acquire_daily_prop_snapshots as _acquire_mlb
from v17.nfl_daily_prop_acquisition import (
    acquire_daily_nfl_prop_snapshots as _acquire_nfl,
)

CAN_EXECUTE = False
DISCOVERY_VERSION = "WOW_V17_DAILY_PROP_DISCOVERY_V1"

# Order is deliberate: the NFL adapter owns the coverage gap this dispatcher
# exists to close, so it receives the first share of a very small budget.
DISCOVERY_SPORTS: tuple[str, ...] = ("NFL", "MLB")

_PRODUCERS: dict[str, Callable[..., dict[str, Any]]] = {
    "NFL": _acquire_nfl,
    "MLB": _acquire_mlb,
}

_COUNT_KEYS = (
    "attempted",
    "hydrated",
    "persisted",
    "held",
    "snapshot_write_succeeded",
    "snapshot_write_failed",
    "explicit_prewrite_exclusions",
    "already_captured",
    "new_candidates_acquired",
    "reused_snapshot_rows",
    "discovered_candidates",
)


def _zero_new_candidates_reason(
    *,
    attempted: int,
    discovered: int,
    new_candidates: int,
    reused: int,
) -> str | None:
    """Name why nothing new arrived instead of reporting a bare zero."""
    if new_candidates > 0:
        return None
    if discovered == 0 and attempted == 0:
        return "NO_CANDIDATES_DISCOVERED"
    if attempted > 0 and reused > 0:
        return "ALL_DISCOVERED_CANDIDATES_ALREADY_CAPTURED"
    if attempted > 0:
        return "NO_CANDIDATE_COULD_BE_HYDRATED"
    return "PRODUCER_RETURNED_NO_ATTEMPTABLE_CANDIDATES"


def acquire_daily_prop_snapshots(
    *,
    db: Any,
    requested_date: str,
    requested_timezone: str,
    max_candidates: int,
    now: datetime | None = None,
    http_get: Callable[..., Any] = httpx.get,
    sports: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    """Run every certified autonomous producer under one bounded budget."""
    selected = tuple(
        sport for sport in (sports or DISCOVERY_SPORTS) if sport in _PRODUCERS
    ) or tuple(DISCOVERY_SPORTS)
    total_budget = max(0, int(max_candidates))

    result: dict[str, Any] = {
        "status": "COMPLETED",
        "discovery_version": DISCOVERY_VERSION,
        "requested_date": requested_date,
        "requested_timezone": requested_timezone,
        "max_candidates": total_budget,
        "sports_attempted": list(selected),
        "attempted": 0,
        "hydrated": 0,
        "persisted": 0,
        "held": 0,
        "snapshot_write_succeeded": 0,
        "snapshot_write_failed": 0,
        "explicit_prewrite_exclusions": 0,
        "already_captured": 0,
        "new_candidates_acquired": 0,
        "reused_snapshot_rows": 0,
        "discovered_candidates": 0,
        "receipts": [],
        "blockers": [],
        "candidate_sources": {},
        "line_sources": {},
        "stat_types": [],
        "sport_results": {},
        "zero_new_candidates_reason": None,
        "can_execute": False,
    }
    if total_budget <= 0:
        result["zero_new_candidates_reason"] = "REQUESTED_ROW_LIMIT_ZERO"
        return result

    remaining = total_budget
    remaining_sports = len(selected)
    for sport in selected:
        share = max(1, math.ceil(remaining / remaining_sports)) if remaining else 0
        share = min(share, remaining)
        remaining_sports -= 1
        sport_result: dict[str, Any]
        if share <= 0:
            sport_result = {
                "status": "BUDGET_EXHAUSTED",
                "attempted": 0,
                "blockers": [],
                "can_execute": False,
            }
        else:
            try:
                sport_result = _PRODUCERS[sport](
                    db=db,
                    requested_date=requested_date,
                    requested_timezone=requested_timezone,
                    max_candidates=share,
                    now=now,
                    http_get=http_get,
                )
            except Exception as exc:  # noqa: BLE001 - one sport cannot void the lane
                sport_result = {
                    "status": "DATA_UNOBTAINABLE",
                    "attempted": 0,
                    "blockers": [f"DAILY_PROP_DISCOVERY_FAILED:{type(exc).__name__}"],
                    "can_execute": False,
                }

        result["sport_results"][sport] = {
            key: sport_result.get(key)
            for key in ("status", "attempted", "persisted", "held", "blockers")
        }
        result["sport_results"][sport]["can_execute"] = False
        result["candidate_sources"][sport] = sport_result.get("candidate_source")
        result["line_sources"][sport] = sport_result.get("line_source")
        for stat_type in sport_result.get("stat_types") or []:
            if stat_type not in result["stat_types"]:
                result["stat_types"].append(stat_type)

        for key in _COUNT_KEYS:
            result[key] += int(sport_result.get(key) or 0)

        for receipt in sport_result.get("receipts") or []:
            if isinstance(receipt, dict):
                result["receipts"].append({**receipt, "sport": sport})
        for blocker in sport_result.get("blockers") or []:
            result["blockers"].append(f"{sport}:{blocker}")

        remaining = max(0, remaining - int(sport_result.get("attempted") or 0))
        if sport_result.get("status") == "RUN_INVALID_PROP_SNAPSHOT_WRITE_FAILURE":
            result["write_failure_sports"] = [
                *result.get("write_failure_sports", []),
                sport,
            ]

    # Older producers report ``persisted`` only; keep both counters truthful.
    if result["new_candidates_acquired"] == 0 and result["persisted"] > 0:
        result["new_candidates_acquired"] = result["persisted"]
    if result["reused_snapshot_rows"] == 0 and result["already_captured"] > 0:
        result["reused_snapshot_rows"] = result["already_captured"]

    result["blockers"] = list(dict.fromkeys(result["blockers"]))
    result["zero_new_candidates_reason"] = _zero_new_candidates_reason(
        attempted=result["attempted"],
        discovered=result["discovered_candidates"],
        new_candidates=result["new_candidates_acquired"],
        reused=result["reused_snapshot_rows"],
    )

    statuses = {
        str((result["sport_results"].get(sport) or {}).get("status") or "")
        for sport in selected
    }
    if statuses and statuses <= {"DATA_UNOBTAINABLE"}:
        result["status"] = "DATA_UNOBTAINABLE"
    elif result["snapshot_write_failed"] > 0 and result["snapshot_write_succeeded"] == 0:
        result["status"] = "RUN_INVALID_PROP_SNAPSHOT_WRITE_FAILURE"
    elif result["held"] > 0:
        result["status"] = "COMPLETED_WITH_ROW_BLOCKERS"
    return result


__all__ = [
    "CAN_EXECUTE",
    "DISCOVERY_SPORTS",
    "DISCOVERY_VERSION",
    "acquire_daily_prop_snapshots",
]
