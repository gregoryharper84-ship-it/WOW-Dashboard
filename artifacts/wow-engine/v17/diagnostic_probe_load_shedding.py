"""Bound concurrent V17 diagnostic probes without weakening fail-closed state.

The production health/readiness/governance routes are diagnostic surfaces. Their
backing functions may touch Supabase, Redis, calibration state, and capability
registries. A burst of identical probes must not multiply those dependency calls
until the web process starves its own liveness route.

This module preserves the original route functions and their exact responses. It
only coalesces *concurrent* calls so one leader performs the expensive probe and
waiters consume that same completed result. There is deliberately no success TTL:
a later sequential readiness/governance call performs a fresh probe, preserving
fail-closed dependency semantics. Waiters fail closed if the leader cannot finish
inside the bounded wait. /health/live is re-exposed as an async dependency-free
route so a saturated sync thread pool cannot hide a live process from Render.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from threading import Event, Lock
from typing import Any, Callable

from fastapi import HTTPException

CAN_EXECUTE = False
GLOBAL_TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
WAIT_TIMEOUT_SECONDS = 2.0


class ProbeBusy(RuntimeError):
    """Another identical diagnostic probe did not complete inside the bound."""


@dataclass
class _Flight:
    event: Event = field(default_factory=Event)
    result: Any = None
    error: Exception | None = None


class SingleFlightProbe:
    """Coalesce only overlapping calls; never reuse a completed probe later."""

    def __init__(self, *, wait_timeout_seconds: float = WAIT_TIMEOUT_SECONDS):
        self.wait_timeout_seconds = float(wait_timeout_seconds)
        self._lock = Lock()
        self._inflight: _Flight | None = None

    def run(self, loader: Callable[[], Any]) -> Any:
        with self._lock:
            flight = self._inflight
            if flight is None:
                flight = _Flight()
                self._inflight = flight
                leader = True
            else:
                leader = False

        if leader:
            try:
                flight.result = loader()
            except Exception as exc:  # preserve exact original application failure
                flight.error = exc
            finally:
                # Signal completion while the in-flight marker is still protected
                # by the same lock. A new caller therefore cannot start a second
                # backing probe in the completion handoff window.
                with self._lock:
                    flight.event.set()
                    if self._inflight is flight:
                        self._inflight = None

            if flight.error is not None:
                raise flight.error
            return deepcopy(flight.result)

        if not flight.event.wait(timeout=self.wait_timeout_seconds):
            raise ProbeBusy("diagnostic probe still in progress")
        if flight.error is not None:
            raise flight.error
        return deepcopy(flight.result)


def _get_route(app: Any, path: str):
    matches = [
        route
        for route in list(app.router.routes)
        if getattr(route, "path", None) == path
        and "GET" in (getattr(route, "methods", set()) or set())
    ]
    return matches[-1] if matches else None


def _busy_http(probe: str) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "DIAGNOSTIC_PROBE_BUSY",
            "probe": probe,
            "retryable": True,
            "global_terminal_authority": GLOBAL_TERMINAL_AUTHORITY,
            "probability_publishable": False,
            "can_execute": False,
        },
    )


def install_diagnostic_probe_load_shedding(app: Any) -> bool:
    """Replace diagnostic GET routes with concurrency-safe wrappers.

    Route business logic is captured from the already-installed authoritative
    endpoints. No capability, model, calibration, terminal, or execution logic is
    reimplemented here.
    """
    if getattr(app.state, "v17_diagnostic_probe_load_shedding_installed", False):
        return True

    live_route = _get_route(app, "/health/live")
    ready_route = _get_route(app, "/health/ready")
    governance_route = _get_route(app, "/governance")
    if live_route is None or ready_route is None or governance_route is None:
        return False

    live_endpoint = live_route.endpoint
    ready_endpoint = ready_route.endpoint
    governance_endpoint = governance_route.endpoint
    live_operation_id = getattr(live_route, "operation_id", None)
    ready_operation_id = getattr(ready_route, "operation_id", None)
    governance_operation_id = getattr(governance_route, "operation_id", None)

    for route in (live_route, ready_route, governance_route):
        app.router.routes.remove(route)

    ready_probe = SingleFlightProbe()
    governance_probe = SingleFlightProbe()

    @app.get("/health/live", operation_id=live_operation_id)
    async def health_live_load_shed():
        # The captured liveness function is intentionally dependency-free and
        # non-blocking. Calling it on the event loop avoids sync-thread starvation.
        return live_endpoint()

    @app.get("/health/ready", operation_id=ready_operation_id)
    def health_ready_load_shed():
        try:
            return ready_probe.run(ready_endpoint)
        except ProbeBusy as exc:
            raise _busy_http("READINESS") from exc

    @app.get("/governance", operation_id=governance_operation_id)
    def governance_load_shed():
        try:
            return governance_probe.run(governance_endpoint)
        except ProbeBusy as exc:
            raise _busy_http("GOVERNANCE") from exc

    app.state.v17_diagnostic_probe_load_shedding_installed = True
    app.state.v17_diagnostic_probe_wait_timeout_seconds = WAIT_TIMEOUT_SECONDS
    return True


__all__ = [
    "CAN_EXECUTE",
    "GLOBAL_TERMINAL_AUTHORITY",
    "ProbeBusy",
    "SingleFlightProbe",
    "WAIT_TIMEOUT_SECONDS",
    "install_diagnostic_probe_load_shedding",
]
