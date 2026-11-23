"""Replay missed hourly and daily slots of product lanes (wake-only; runs inside the #2744 Render cron).

GitHub sheds most sub-daily schedules in this repository. Between 2026-11-19
18:00 and 2026-11-20 18:00 UTC, 84 of 1,331 expected scheduled runs fired
across active workflows (6.3%); every hourly product lane ran about 3 times a
day instead of 24. The provider dispatcher already has an off-GitHub slot
replay (``engineering_dispatch_slot_recovery``); this module applies the same
rule to the hourly evidence, refresh, closure and daily basketball
maintenance lanes, from the same cron and token.

For each lane's most recent native slot (hourly minute or daily UTC hour/minute),
once the slot is
``SLOT_GRACE`` overdue and before ``SLOT_RECOVERY_DEADLINE``: if no run of that
workflow on ``main`` was created since the slot and none is queued or running,
dispatch it with no inputs. Lanes that share a GitHub concurrency group are
handled together: nothing is dispatched while any member is queued or running,
and at most one member is dispatched per tick, because a second pending run in
a group cancels the first.

It never enables, disables, cancels or re-runs a workflow, never passes
inputs, and fails closed on unreadable evidence. can_execute is always false.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from v17.engineering_dispatch_slot_recovery import (
    ACTIVE_STATUSES,
    DISPATCH_REF,
    REPOSITORY,
    SLOT_GRACE,
    SLOT_RECOVERY_DEADLINE,
    _parse_time,
)

Http = Callable[..., tuple[int, Any]]

# (workflow file, native cron minute, GitHub concurrency group). A drift-guard
# test pins each minute and group to the workflow file.
LANE_TARGETS: tuple[tuple[str, int, str], ...] = (
    ("wow-v17-priority-prop-lifecycle.yml", 7, "wow-v17-production-source-heavy-prop-evidence"),
    ("wow-v17-mlb-prop-forward-evidence.yml", 23, "wow-v17-production-source-heavy-prop-evidence"),
    ("wow-v17-nfl-prop-forward-evidence.yml", 37, "wow-v17-production-source-heavy-prop-evidence"),
    ("wow-v17-team-event-recoverable-refresh.yml", 17, "wow-v17-team-event-recoverable-refresh"),
    ("wow-v17-terminal-closure-controller.yml", 17, "wow-v17-terminal-closure-controller"),
)
# Daily recovery reuses the off-GitHub cron. Its 15-minute ticks at 13:30
# and 13:45 UTC fall within the existing 25-45 minute recovery window.
# (workflow file, UTC hour, cron minute, concurrency group namespace)
DAILY_TARGETS: tuple[tuple[str, int, int, str], ...] = (
    ("wow-v17-basketball-model-maintenance.yml", 13, 5, "wow-v17-basketball-model-maintenance"),
)
OK_STATUSES = frozenset({"DISPATCHED", "SLOT_NOT_DUE", "LANE_GROUP_BUSY", "LANE_NOT_ACTIVE", "GROUP_DISPATCHED_THIS_TICK"})


def lane_slot(now: datetime, minute: int) -> datetime:
    slot = now.astimezone(timezone.utc).replace(minute=minute, second=0, microsecond=0)
    return slot if slot <= now else slot - timedelta(hours=1)


def daily_slot(now: datetime, hour: int, minute: int) -> datetime:
    """Most recent UTC occurrence of a daily scheduled slot."""
    slot = now.astimezone(timezone.utc).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return slot if slot <= now else slot - timedelta(days=1)


def _path(workflow: str) -> str:
    return f"/repos/{REPOSITORY}/actions/workflows/{workflow}"


def _result(workflow: str, status: str, now: datetime, **extra: Any) -> dict[str, Any]:
    return {"job": "wow-v17-schedule-lane-recovery", "workflow": workflow, "status": status,
            "checked_at": now.isoformat(), "can_execute": False, **extra}


def _read_lane(workflow: str, now: datetime, call: Http) -> dict[str, Any]:
    """Return {state, active, last_created} or {error} for one lane."""
    try:
        status, info = call("GET", _path(workflow))
        if status != 200 or not isinstance(info, dict):
            return {"error": "GITHUB_UNAVAILABLE", "http_status": status}
        if info.get("state") != "active":
            return {"state": info.get("state")}
        status, listing = call("GET", f"{_path(workflow)}/runs?branch={DISPATCH_REF}&per_page=30")
        if status != 200 or not isinstance(listing, dict) or not isinstance(listing.get("workflow_runs"), list):
            return {"error": "GITHUB_UNAVAILABLE" if status != 200 else "GITHUB_EVIDENCE_INVALID", "http_status": status}
    except OSError as error:
        return {"error": "GITHUB_UNAVAILABLE", "detail": type(error).__name__}
    active, last = False, None
    try:
        for run in listing["workflow_runs"]:
            if not isinstance(run, dict):
                raise ValueError("run not an object")
            if run.get("status") in ACTIVE_STATUSES:
                active = True
            created = _parse_time(run.get("created_at"))
            last = created if last is None or created > last else last
    except ValueError:
        return {"error": "GITHUB_EVIDENCE_INVALID"}
    return {"state": "active", "active": active, "last_created": last}


def recover(token: str, *, now: datetime, http: Http) -> list[dict[str, Any]]:
    del token  # the caller's http already carries it
    groups: "OrderedDict[str, list[tuple[str, int]]]" = OrderedDict()
    for workflow, minute, group in LANE_TARGETS:
        groups.setdefault(group, []).append((workflow, minute))
    daily_slots = {}
    for workflow, hour, minute, group in DAILY_TARGETS:
        groups.setdefault(group, []).append((workflow, minute))
        daily_slots[workflow] = daily_slot(now, hour, minute)

    results: list[dict[str, Any]] = []
    for group, members in groups.items():
        states = {workflow: _read_lane(workflow, now, http) for workflow, _ in members}
        failed = [w for w, s in states.items() if "error" in s]
        if failed:
            results.extend(_result(w, states[w]["error"], now, group=group) for w in failed)
            results.extend(_result(w, "LANE_GROUP_EVIDENCE_INCOMPLETE", now, group=group)
                           for w, _ in members if w not in failed)
            continue
        if any(s.get("active") for s in states.values()):
            results.extend(_result(w, "LANE_GROUP_BUSY", now, group=group) for w, _ in members)
            continue
        dispatched = False
        for workflow, minute in members:
            state, slot = states[workflow], daily_slots.get(workflow) or lane_slot(now, minute)
            extra = {"group": group, "slot": slot.isoformat()}
            if state.get("state") != "active":
                results.append(_result(workflow, "LANE_NOT_ACTIVE", now, workflow_state=state.get("state"), **extra))
                continue
            if now - slot < SLOT_GRACE:
                results.append(_result(workflow, "SLOT_NOT_DUE", now, reason="WAITING_FOR_NATIVE_SCHEDULE", **extra))
                continue
            if now - slot > SLOT_RECOVERY_DEADLINE:
                results.append(_result(workflow, "SLOT_NOT_DUE", now, reason="NEXT_NATIVE_SLOT_TOO_CLOSE", **extra))
                continue
            last = state.get("last_created")
            if last is not None and last >= slot:
                results.append(_result(workflow, "SLOT_NOT_DUE", now, reason="SLOT_ALREADY_SERVED",
                                       last_run_at=last.isoformat(), **extra))
                continue
            if dispatched:
                results.append(_result(workflow, "GROUP_DISPATCHED_THIS_TICK", now, **extra))
                continue
            try:
                status, _ = http("POST", f"{_path(workflow)}/dispatches", {"ref": DISPATCH_REF})
            except OSError as error:
                results.append(_result(workflow, "GITHUB_UNAVAILABLE", now, detail=type(error).__name__, **extra))
                continue
            if status != 204:
                results.append(_result(workflow, "DISPATCH_REJECTED", now, http_status=status, **extra))
                continue
            dispatched = True
            results.append(_result(workflow, "DISPATCHED", now,
                                   last_run_at=last.isoformat() if last else None, **extra))
    return results
