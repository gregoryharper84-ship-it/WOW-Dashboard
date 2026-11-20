"""Dispatcher slot recovery (Render cron). Wake-only; no routing, admission, or execution authority.

GitHub's scheduler drops most hourly slots in this repository. Between
2026-10-23 and 2026-11-02 the provider dispatcher's ``17 * * * *`` cron fired
4-5 times a day instead of 24, and its same-minute siblings fire about 3 times
a day. The dispatcher is the only automatic engineering entrypoint, so dropped
slots silently stall autonomous repair (#1021).

This job runs outside GitHub's scheduler and replays a missed slot of the
dispatcher's own hourly cadence: when no default-lane (``lease=GLOBAL``) run
has started within ``SLOT_WINDOW`` and none is queued or running, it calls
``workflow_dispatch`` on ``main`` with ``force_provider=auto`` and
``lease_group=GLOBAL`` -- the routing a scheduled run resolves to, in the same
concurrency group. The existing dispatcher remains the sole routing and
admission control plane; this job never chooses a provider, incident, or lease.

It never enables, disables, cancels, or re-runs a workflow, so a dispatcher
that is not active (for example an owner stop) stays stopped. Unreadable or
malformed GitHub evidence fails closed without dispatching.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

REPOSITORY = "gregoryharper84-ship-it/WOW-Dashboard"
DISPATCHER_WORKFLOW = "wow-v17-engineering-provider-dispatcher.yml"
DISPATCH_REF = "main"
RECOVERY_REASON = "SCHEDULE_SLOT_RECOVERY"
DISPATCH_INPUTS = {
    "force_provider": "auto",
    "lease_group": "GLOBAL",
    "reason": RECOVERY_REASON,
}
# Shorter than the dispatcher's hourly cadence so one slot is replayed at most once.
SLOT_WINDOW = timedelta(minutes=55)
DEFAULT_LANE = "GLOBAL"
ACTIVE_STATUSES = frozenset({"queued", "in_progress", "waiting", "pending", "requested"})
TOKEN_ENV = "WOW_DISPATCH_GITHUB_TOKEN"
API_ROOT = "https://api.github.com"

_WORKFLOW_PATH = f"/repos/{REPOSITORY}/actions/workflows/{DISPATCHER_WORKFLOW}"
_RUNS_PATH = f"{_WORKFLOW_PATH}/runs?per_page=100"
_DISPATCH_PATH = f"{_WORKFLOW_PATH}/dispatches"
_LEASE = re.compile(r"(?:^|\s)lease=(\S+)")

Http = Callable[..., "tuple[int, Any]"]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def github_http(token: str, *, timeout_seconds: int = 15) -> Http:
    """Return a minimal GitHub REST caller; the token is sent only as a header, never logged."""

    def call(method: str, path: str, body: Optional[dict] = None) -> "tuple[int, Any]":
        request = Request(
            API_ROOT + path,
            data=None if body is None else json.dumps(body).encode("utf-8"),
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": "Bearer " + token,
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "wow-v17-dispatcher-slot-recovery",
                **({"Content-Type": "application/json"} if body is not None else {}),
            },
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else None
        except HTTPError as error:
            return error.code, None

    return call


def _own_lease(run: dict) -> str:
    # The dispatcher's run-name ends with its own lease; a source title may embed another.
    leases = _LEASE.findall(str(run.get("display_title") or ""))
    return leases[-1] if leases else ""


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("created_at missing")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _result(status: str, now: datetime, **extra: Any) -> dict[str, Any]:
    return {
        "job": "wow-v17-dispatcher-slot-recovery",
        "status": status,
        "checked_at": now.isoformat(),
        "workflow": DISPATCHER_WORKFLOW,
        "can_execute": False,
        **extra,
    }


def run_once(token: str, *, now: Optional[datetime] = None, http: Optional[Http] = None) -> dict[str, Any]:
    """Assess the dispatcher's default lane once and replay a missed hourly slot if due."""
    now = now or _utcnow()
    call = http or github_http(token)

    try:
        status, workflow = call("GET", _WORKFLOW_PATH)
        if status != 200 or not isinstance(workflow, dict):
            return _result("GITHUB_UNAVAILABLE", now, http_status=status)
        if workflow.get("state") != "active":
            return _result("DISPATCHER_NOT_ACTIVE", now, workflow_state=workflow.get("state"))

        status, listing = call("GET", _RUNS_PATH)
        if status != 200 or not isinstance(listing, dict):
            return _result("GITHUB_UNAVAILABLE", now, http_status=status)
    except OSError as error:
        return _result("GITHUB_UNAVAILABLE", now, error=type(error).__name__)

    runs = listing.get("workflow_runs")
    if not isinstance(runs, list):
        return _result("GITHUB_EVIDENCE_INVALID", now)

    last_default_lane_at: Optional[datetime] = None
    try:
        for run in runs:
            if not isinstance(run, dict) or _own_lease(run) != DEFAULT_LANE:
                continue
            if run.get("status") in ACTIVE_STATUSES:
                return _result("DISPATCHER_BUSY", now, run_status=run.get("status"))
            created = _parse_time(run.get("created_at"))
            if last_default_lane_at is None or created > last_default_lane_at:
                last_default_lane_at = created
    except ValueError:
        return _result("GITHUB_EVIDENCE_INVALID", now)

    last_seen = last_default_lane_at.isoformat() if last_default_lane_at else None
    if last_default_lane_at is not None and now - last_default_lane_at < SLOT_WINDOW:
        return _result("SLOT_NOT_DUE", now, last_default_lane_run_at=last_seen)

    try:
        status, _ = call("POST", _DISPATCH_PATH, {"ref": DISPATCH_REF, "inputs": dict(DISPATCH_INPUTS)})
    except OSError as error:
        return _result("GITHUB_UNAVAILABLE", now, error=type(error).__name__)
    if status != 204:
        return _result("DISPATCH_REJECTED", now, http_status=status, last_default_lane_run_at=last_seen)
    return _result("DISPATCHED", now, last_default_lane_run_at=last_seen, inputs=dict(DISPATCH_INPUTS))


_OK = frozenset({"DISPATCHED", "SLOT_NOT_DUE", "DISPATCHER_BUSY", "DISPATCHER_NOT_ACTIVE"})


def main(*, http: Optional[Http] = None, now: Optional[datetime] = None) -> int:
    now = now or _utcnow()
    if os.environ.get("WOW_CAN_EXECUTE", "false").strip().lower() != "false":
        print(json.dumps(_result("EXECUTION_AUTHORITY_REFUSED", now)))
        return 2
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        print(json.dumps(_result("CONFIGURATION_MISSING", now, missing=TOKEN_ENV)))
        return 2
    result = run_once(token, now=now, http=http)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] in _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
