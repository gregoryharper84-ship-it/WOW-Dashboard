"""Feature-gated production installer for durable Scout specialist handoff.

The feature remains disabled unless WOW_SCOUT_ASYNC_HANDOFF_ENABLED=true.
That permits code/CI review while #1237 Supabase data-plane acceptance remains
open. Enabling it mounts authenticated enqueue/status routes and DB-leased
workers that call the already-installed canonical specialist endpoints.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any, Callable

from fastapi import Query

from v17 import scout_handoff_queue as queue

CAN_EXECUTE = False
_STATE_KEY = "wow_scout_durable_handoff_installed"


def _enabled() -> bool:
    return os.getenv("WOW_SCOUT_ASYNC_HANDOFF_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _worker_count() -> int:
    raw = os.getenv("WOW_SCOUT_HANDOFF_WORKERS", "2")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 2
    return max(1, min(value, 4))


def _route(app: Any, path: str, method: str) -> Any | None:
    return next(
        (
            route
            for route in app.router.routes
            if getattr(route, "path", None) == path
            and method in (getattr(route, "methods", set()) or set())
        ),
        None,
    )


def install_scout_handoff_routes(
    app: Any,
    *,
    db_client_fn: Callable[[], Any],
) -> tuple[bool, Callable[..., dict[str, Any]] | None, Callable[..., dict[str, Any]] | None]:
    if getattr(app.state, _STATE_KEY, False):
        prop = _route(app, "/score-pick-request", "POST")
        team = _route(app, "/score-team-event-request", "POST")
        return (
            True,
            getattr(prop, "endpoint", None) if prop is not None else None,
            getattr(team, "endpoint", None) if team is not None else None,
        )

    prop_route = _route(app, "/score-pick-request", "POST")
    team_route = _route(app, "/score-team-event-request", "POST")
    if prop_route is None or team_route is None:
        return False, None, None
    prop_score_fn = getattr(prop_route, "endpoint", None)
    team_score_fn = getattr(team_route, "endpoint", None)
    if not callable(prop_score_fn) or not callable(team_score_fn):
        return False, None, None

    # Reuse the canonical scorer route's authentication dependencies. This route
    # is server-to-server only and must never become an unauthenticated queue API.
    dependencies = list(getattr(prop_route, "dependencies", None) or [])

    @app.post(
        "/v17/scout-handoff-runs",
        dependencies=dependencies,
        operation_id="submitWowV17ScoutHandoffRun",
    )
    def submit_scout_handoff_run(handoff: dict[str, Any]) -> dict[str, Any]:
        # Callers submit raw Scout evidence. Rebuild the deterministic plan on
        # the governed server so RED_TEAM_PASSED cannot be self-asserted by a
        # transport caller.
        plan = queue.build_handoff_plan(handoff)
        if plan.can_execute is not False:
            raise ValueError("SCOUT_EXECUTION_GOVERNANCE_VIOLATION")
        return queue.enqueue_plan(db_client_fn(), plan)

    @app.get(
        "/v17/scout-handoff-runs/{source_run_id}",
        dependencies=dependencies,
        operation_id="getWowV17ScoutHandoffRun",
    )
    def get_scout_handoff_run(
        source_run_id: str,
        include_receipts: bool = Query(default=False),
    ) -> dict[str, Any]:
        return queue.read_run_summary(
            db_client_fn(),
            source_run_id,
            include_receipts=include_receipts,
        )

    setattr(app.state, _STATE_KEY, True)
    return True, prop_score_fn, team_score_fn


def schedule_scout_handoff_queue(
    app: Any,
    *,
    db_client_fn: Callable[[], Any],
) -> None:
    scheduled_key = f"{_STATE_KEY}_scheduled"
    if getattr(app.state, scheduled_key, False):
        return

    @app.on_event("startup")
    async def _install_and_start() -> None:
        if not _enabled():
            return
        installed, prop_score_fn, team_score_fn = install_scout_handoff_routes(
            app,
            db_client_fn=db_client_fn,
        )
        if not installed or not callable(prop_score_fn) or not callable(team_score_fn):
            return

        stop_event = asyncio.Event()
        tasks = []
        for index in range(_worker_count()):
            worker_id = (
                f"{os.getenv('RENDER_INSTANCE_ID') or 'local'}:"
                f"{os.getpid()}:{index}:{uuid.uuid4().hex[:8]}"
            )
            tasks.append(
                asyncio.create_task(
                    queue.worker_loop(
                        db_client_fn=db_client_fn,
                        prop_score_fn=prop_score_fn,
                        team_score_fn=team_score_fn,
                        worker_id=worker_id,
                        stop_event=stop_event,
                    )
                )
            )
        setattr(app.state, f"{_STATE_KEY}_stop", stop_event)
        setattr(app.state, f"{_STATE_KEY}_tasks", tasks)

    @app.on_event("shutdown")
    async def _stop() -> None:
        stop_event = getattr(app.state, f"{_STATE_KEY}_stop", None)
        tasks = list(getattr(app.state, f"{_STATE_KEY}_tasks", []) or [])
        if stop_event is not None:
            stop_event.set()
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

    setattr(app.state, scheduled_key, True)


__all__ = [
    "CAN_EXECUTE",
    "install_scout_handoff_routes",
    "schedule_scout_handoff_queue",
]
