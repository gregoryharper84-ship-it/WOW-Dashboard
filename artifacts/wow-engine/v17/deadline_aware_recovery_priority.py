"""Dependency- and deadline-aware ordering for the V17 recovery queue.

An hourly recovery cadence that treats every task alike spends its capacity on
work that can no longer reach a sporting event.  This module ranks supplied
recovery tasks by:

1. whether repair can still finish before the earliest blocked event starts;
2. how many eligible candidates the task unblocks;
3. whether the component is a shared producer behind several lanes.

Priority is a lexicographic ordering of attested evidence, not a synthetic
score, so a ranking is always explainable.  Nothing here marks a task repaired,
closes a task, changes an owner, dispatches work or touches a probability.  A
task that can no longer be timely is reported as missed, not silently dropped.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"

TIMELY = "TIMELY"
AT_RISK = "AT_RISK"
UNKNOWN_WINDOW = "UNKNOWN_WINDOW"
MISSED_DEADLINE = "MISSED_DEADLINE"
BLOCKED_EVIDENCE = "BLOCKED_EVIDENCE"

_STATUS_ORDER = {
    TIMELY: 0,
    AT_RISK: 1,
    UNKNOWN_WINDOW: 2,
    MISSED_DEADLINE: 3,
}


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _timeliness(
    earliest_event_start: datetime | None, estimated_fix_minutes: int, now: datetime
) -> str:
    if earliest_event_start is None:
        return UNKNOWN_WINDOW
    if earliest_event_start <= now:
        return MISSED_DEADLINE
    if now + timedelta(minutes=estimated_fix_minutes) >= earliest_event_start:
        return AT_RISK
    return TIMELY


def _rationale(
    status: str, blocked: int, shared: bool, earliest: datetime | None
) -> str:
    impact = f"unblocks {blocked} eligible candidate(s)"
    if shared:
        impact += " behind a shared producer"
    if status == TIMELY:
        return f"{impact}; repair fits before {earliest.isoformat() if earliest else 'event start'}"
    if status == AT_RISK:
        return f"{impact}; estimated repair may finish after the earliest event start"
    if status == UNKNOWN_WINDOW:
        return f"{impact}; no attested earliest event start, so timeliness is unproven"
    return f"{impact}; all blocked events have already started"


def prioritize_recovery_tasks(
    tasks: Sequence[Mapping[str, Any]], *, now: datetime
) -> dict[str, Any]:
    """Rank recovery tasks.  Never dispatches, closes or reassigns anything."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("NOW_MUST_BE_TIMEZONE_AWARE")
    if not isinstance(tasks, Sequence) or isinstance(tasks, (str, bytes)):
        return {
            "can_execute": False,
            "terminal_authority": TERMINAL_AUTHORITY,
            "ranked": [],
            "blocked": [],
            "summary": {},
            "expired_tasks": [],
        }

    ranked: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    seen: set[str] = set()

    for index, task in enumerate(tasks):
        if not isinstance(task, Mapping):
            blocked.append({"index": index, "blockers": [f"TASK_{index}_NOT_A_MAPPING"]})
            continue
        task_id = task.get("task_id")
        blockers: list[str] = []
        if not _text(task_id):
            blockers.append(f"TASK_{index}_ID_MISSING")
        elif task_id in seen:
            blockers.append(f"TASK_{index}_DUPLICATE_TASK_ID")
        else:
            seen.add(str(task_id))
        if not _text(task.get("component")):
            blockers.append(f"TASK_{index}_COMPONENT_MISSING")
        if not _count(task.get("blocked_candidates")):
            blockers.append(f"TASK_{index}_BLOCKED_CANDIDATES_UNATTESTED")
        if not _count(task.get("estimated_fix_minutes")):
            blockers.append(f"TASK_{index}_ESTIMATED_FIX_UNATTESTED")
        shared = task.get("shared_dependency", False)
        if not isinstance(shared, bool):
            blockers.append(f"TASK_{index}_SHARED_DEPENDENCY_INVALID")
        if blockers:
            blocked.append({"index": index, "task_id": task.get("task_id"), "blockers": blockers})
            continue

        earliest = _parse_ts(task.get("earliest_event_start_at"))
        status = _timeliness(earliest, task["estimated_fix_minutes"], now)
        ranked.append(
            {
                "task_id": str(task_id),
                "component": str(task["component"]),
                "owner": task.get("owner"),
                "blocked_candidates": task["blocked_candidates"],
                "shared_dependency": shared,
                "earliest_event_start_at": task.get("earliest_event_start_at"),
                "estimated_fix_minutes": task["estimated_fix_minutes"],
                "timeliness": status,
                "rationale": _rationale(status, task["blocked_candidates"], shared, earliest),
                "_earliest": earliest,
            }
        )

    # Lexicographic: only timely work first, then largest unblocked blast radius,
    # then shared producers, then the nearest deadline, then a stable identity.
    ranked.sort(
        key=lambda item: (
            _STATUS_ORDER[item["timeliness"]],
            -item["blocked_candidates"],
            -int(item["shared_dependency"]),
            item["_earliest"].timestamp() if item["_earliest"] else float("inf"),
            item["task_id"],
        )
    )
    for position, item in enumerate(ranked, start=1):
        item["rank"] = position
        item.pop("_earliest", None)

    summary = {status: 0 for status in (TIMELY, AT_RISK, UNKNOWN_WINDOW, MISSED_DEADLINE)}
    for item in ranked:
        summary[item["timeliness"]] += 1

    return {
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
        "ranked": ranked,
        "blocked": blocked,
        "summary": summary,
        "expired_tasks": [
            item["task_id"] for item in ranked if item["timeliness"] == MISSED_DEADLINE
        ],
        "ranked_at": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


__all__ = [
    "AT_RISK",
    "BLOCKED_EVIDENCE",
    "CAN_EXECUTE",
    "MISSED_DEADLINE",
    "TERMINAL_AUTHORITY",
    "TIMELY",
    "UNKNOWN_WINDOW",
    "prioritize_recovery_tasks",
]
