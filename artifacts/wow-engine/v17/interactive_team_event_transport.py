"""Bound the V17 interactive TEAM_EVENT transport path without changing scoring.

The Action boundary has a shorter useful lifetime than batch/background jobs.
This module therefore gives only the V17 team/event event-api client a cached,
bounded PostgREST client and places a hard wall-clock ceiling around the public
``/score-team-event`` response. Background/Daily clients keep their existing
runtime behavior.

A transport-budget expiry is infrastructure state, not MODEL_UNAVAILABLE and not
a sporting-probability result. The terminal reducer and ``can_execute=false`` are
unchanged.
"""
from __future__ import annotations

import asyncio
import logging
import os
from threading import Lock
from typing import Any

from fastapi.responses import JSONResponse


CAN_EXECUTE = False
LOGGER = logging.getLogger("wow.v17.team_event.transport")
_DEFAULT_POSTGREST_TIMEOUT_SECONDS = 3.0
_DEFAULT_ACTION_BUDGET_SECONDS = 18.0
_MIN_TIMEOUT_SECONDS = 0.5
_MAX_TIMEOUT_SECONDS = 8.0
_MIN_BUDGET_SECONDS = 5.0
_MAX_BUDGET_SECONDS = 20.0


def _bounded_env_float(name: str, default: float, lower: float, upper: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(max(value, lower), upper)


def _new_bounded_client() -> Any:
    from supabase import create_client
    from supabase.client import ClientOptions

    timeout = _bounded_env_float(
        "WOW_V17_TEAM_EVENT_POSTGREST_TIMEOUT_SECONDS",
        _DEFAULT_POSTGREST_TIMEOUT_SECONDS,
        _MIN_TIMEOUT_SECONDS,
        _MAX_TIMEOUT_SECONDS,
    )
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_KEY"],
        options=ClientOptions(
            postgrest_client_timeout=timeout,
            storage_client_timeout=timeout,
            schema="public",
        ),
    )


def install_interactive_team_event_transport(app: Any, *, event_api: Any) -> bool:
    """Install cached bounded DB transport plus a fail-fast Action wall budget."""
    if getattr(app.state, "wow_v17_team_event_transport_installed", False):
        return True

    original_get_client = getattr(event_api, "get_client", None)
    if not callable(original_get_client):
        return False

    client_lock = Lock()
    cached: dict[str, Any] = {"client": None}

    def bounded_get_client() -> Any:
        client = cached["client"]
        if client is not None:
            return client
        with client_lock:
            client = cached["client"]
            if client is not None:
                return client
            try:
                client = _new_bounded_client()
            except Exception as exc:
                # A client-construction compatibility problem must not make the
                # governed API unavailable. Fall back to the accepted factory;
                # the wall-clock Action budget still prevents a 40s+ response.
                LOGGER.warning(
                    "WOW_V17_TEAM_EVENT_BOUNDED_CLIENT_FALLBACK error=%s can_execute=false",
                    type(exc).__name__,
                )
                client = original_get_client()
            cached["client"] = client
            return client

    # The active V17 TEAM_EVENT route receives this exact event_api module. Only
    # that event-api dependency is replaced; the general Supabase factory used
    # by batch/background workflows remains untouched.
    event_api.get_client = bounded_get_client

    @app.middleware("http")
    async def _team_event_action_budget(request: Any, call_next: Any):
        if str(getattr(request.url, "path", "")) != "/score-team-event":
            return await call_next(request)

        budget = _bounded_env_float(
            "WOW_V17_TEAM_EVENT_ACTION_BUDGET_SECONDS",
            _DEFAULT_ACTION_BUDGET_SECONDS,
            _MIN_BUDGET_SECONDS,
            _MAX_BUDGET_SECONDS,
        )
        try:
            return await asyncio.wait_for(call_next(request), timeout=budget)
        except asyncio.TimeoutError:
            LOGGER.error(
                "WOW_V17_TEAM_EVENT_ACTION_BUDGET_EXCEEDED budget_seconds=%.3f can_execute=false",
                budget,
            )
            return JSONResponse(
                status_code=504,
                content={
                    "detail": {
                        "code": "ACTION_TRANSPORT_TIMEOUT",
                        "failure_class": "ACTION_TRANSPORT_TIMEOUT",
                        "backend_runtime_status": (
                            "V17_ACTIVE" if os.getenv("WOW_V17_ACTIVE", "0") == "1" else "V17_INACTIVE"
                        ),
                        "model_capability_status": "NOT_YET_DETERMINED",
                        "probability_publishable": False,
                        "rank_eligible": False,
                        "global_terminal_authority": "V17_TERMINAL_REDUCER",
                        "can_execute": False,
                    }
                },
            )

    app.state.wow_v17_team_event_transport_installed = True
    app.state.wow_v17_team_event_transport = {
        "postgrest_timeout_seconds": _bounded_env_float(
            "WOW_V17_TEAM_EVENT_POSTGREST_TIMEOUT_SECONDS",
            _DEFAULT_POSTGREST_TIMEOUT_SECONDS,
            _MIN_TIMEOUT_SECONDS,
            _MAX_TIMEOUT_SECONDS,
        ),
        "action_budget_seconds": _bounded_env_float(
            "WOW_V17_TEAM_EVENT_ACTION_BUDGET_SECONDS",
            _DEFAULT_ACTION_BUDGET_SECONDS,
            _MIN_BUDGET_SECONDS,
            _MAX_BUDGET_SECONDS,
        ),
        "can_execute": False,
    }
    return True


__all__ = ["CAN_EXECUTE", "install_interactive_team_event_transport"]
