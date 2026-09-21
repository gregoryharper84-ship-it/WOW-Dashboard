"""Route-scoped installer for the V17 durable pick-request job queue.

The queue changes only the public run-control endpoints. Base functions in
``pick_request_run_control`` remain independently callable/testable and are not
monkey-patched globally. This prevents import/startup order from changing core
unit semantics while production still gets durable enqueue + poll behavior.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any, Callable, Optional

from fastapi import Header, Query

from v17 import pick_request_durable_job_queue as queue
from v17 import pick_request_run_control as control
from v17.pick_request_durable_job_queue_hardening import (
    install_durable_job_queue_hardening,
)
from v17.pick_request_run_control_hardening import (
    install_pick_request_run_control_hardening,
)

CAN_EXECUTE = False
_STATE_KEY = "wow_pick_request_durable_job_queue_route_installer"


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


def _remove(app: Any, *routes: Any) -> None:
    doomed = {id(route) for route in routes if route is not None}
    app.router.routes[:] = [route for route in app.router.routes if id(route) not in doomed]


def install_durable_pick_job_queue_routes(
    app: Any,
    *,
    db_client_fn: Callable[[], Any],
) -> tuple[bool, Callable[..., dict[str, Any]] | None]:
    """Replace only run-control routes; return the canonical scorer for worker use."""
    install_pick_request_run_control_hardening()
    install_durable_job_queue_hardening()
    if getattr(app.state, _STATE_KEY, False):
        score = _route(app, "/score-pick-request", "POST")
        return True, getattr(score, "endpoint", None) if score is not None else None

    score_route = _route(app, "/score-pick-request", "POST")
    read_route = _route(app, "/v17/pick-request-runs/{request_id}", "GET")
    run_route = _route(app, "/v17/pick-request-runs/resumable", "POST")
    close_route = _route(app, "/v17/pick-request-runs/{request_id}/close", "POST")
    if any(route is None for route in (score_route, read_route, run_route, close_route)):
        return False, None

    score_fn = getattr(score_route, "endpoint", None)
    if not callable(score_fn):
        return False, None

    read_dependencies = list(getattr(read_route, "dependencies", None) or [])
    run_dependencies = list(getattr(run_route, "dependencies", None) or [])
    close_dependencies = list(getattr(close_route, "dependencies", None) or [])
    read_operation_id = str(getattr(read_route, "operation_id", None) or "getWowV17PickRequestRunState")
    run_operation_id = str(getattr(run_route, "operation_id", None) or "runWowV17ResumablePickRequest")
    close_operation_id = str(getattr(close_route, "operation_id", None) or "closeWowV17PickRequestRun")

    _remove(app, read_route, run_route, close_route)

    @app.get(
        "/v17/pick-request-runs/{request_id}",
        dependencies=read_dependencies,
        operation_id=read_operation_id,
    )
    def get_pick_request_run_state_durable(
        request_id: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=250),
        include_outcomes: bool = Query(default=False),
    ) -> dict[str, Any]:
        return queue.read_run_state_with_job(
            db_client_fn(),
            request_id,
            offset=offset,
            limit=limit,
            include_outcomes=include_outcomes,
        )

    @app.post(
        "/v17/pick-request-runs/resumable",
        dependencies=run_dependencies,
        operation_id=run_operation_id,
    )
    def enqueue_pick_request_run(
        request: control.ResumablePickRunRequest,
        x_wow_model_identity: Optional[str] = Header(default=None, alias="X-WOW-Model-Identity"),
    ) -> dict[str, Any]:
        return queue.enqueue_resumable(
            db_client_fn(),
            request,
            score_fn=score_fn,
            model_identity=x_wow_model_identity,
        )

    @app.post(
        "/v17/pick-request-runs/{request_id}/close",
        dependencies=close_dependencies,
        operation_id=close_operation_id,
    )
    def close_pick_request_run_durable(
        request_id: str,
        request: control.ClosePickRunRequest,
    ) -> dict[str, Any]:
        return queue.close_run_with_job(db_client_fn(), request_id, request)

    setattr(app.state, _STATE_KEY, True)
    return True, score_fn


def schedule_durable_pick_job_queue(
    app: Any,
    *,
    db_client_fn: Callable[[], Any],
) -> None:
    """Install route adapters and start a DB-leased worker after route startup."""
    scheduled_key = f"{_STATE_KEY}_scheduled"
    if getattr(app.state, scheduled_key, False):
        return

    @app.on_event("startup")
    async def _install_and_start() -> None:
        installed, score_fn = install_durable_pick_job_queue_routes(
            app, db_client_fn=db_client_fn
        )
        if not installed or not callable(score_fn):
            return
        worker_id = f"{os.getenv('RENDER_INSTANCE_ID') or 'local'}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        stop_event = asyncio.Event()
        task = asyncio.create_task(
            queue._worker_loop(
                db_client_fn=db_client_fn,
                score_fn=score_fn,
                worker_id=worker_id,
                stop_event=stop_event,
            )
        )
        setattr(app.state, f"{_STATE_KEY}_stop", stop_event)
        setattr(app.state, f"{_STATE_KEY}_task", task)

    @app.on_event("shutdown")
    async def _stop() -> None:
        stop_event = getattr(app.state, f"{_STATE_KEY}_stop", None)
        task = getattr(app.state, f"{_STATE_KEY}_task", None)
        if stop_event is not None:
            stop_event.set()
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    setattr(app.state, scheduled_key, True)


__all__ = [
    "CAN_EXECUTE",
    "install_durable_pick_job_queue_routes",
    "schedule_durable_pick_job_queue",
]
