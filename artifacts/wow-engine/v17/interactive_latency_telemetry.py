"""Non-secret latency telemetry for the governed interactive Action surface.

This module is observability-only. It must not inspect request bodies, alter
sporting probabilities, change terminal semantics, or enable execution.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
from collections import deque
from contextlib import contextmanager
from time import perf_counter
from typing import Any, Iterator


LOGGER = logging.getLogger("wow.v17.interactive_latency")
INTERACTIVE_PATHS = frozenset({
    "/score-prop",
    "/score-pick-request",
    "/score-team-event-request",
    "/score-team-event",
})
STAGES = frozenset({
    "discovery",
    "hydration",
    "fitted_scoring",
    "calibration_bounds",
    "persistence",
    "final_refresh",
    "reconciliation",
})
_MAX_SAMPLES_PER_KEY = 512
_MAX_KEYS = 256
_OVERFLOW_KEY = ("other", "other", "other", "other")
_MAX_LABEL_LEN = 32

# Transport/overload outcomes are labelled for observability only. They are
# never a model-failure state and must not be reported as MODEL_UNAVAILABLE.
_TIMEOUT_STATUS = frozenset({408, 504})
_OVERLOAD_STATUS = frozenset({429, 503})

_CTX: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "wow_interactive_latency_ctx", default=None
)
_LOCK = threading.Lock()
_SAMPLES: dict[tuple[str, str, str, str], deque[float]] = {}


def _label(value: Any) -> str:
    text = "".join(c for c in str(value or "unknown").lower() if c.isalnum() or c in "_-")
    return (text or "unknown")[:_MAX_LABEL_LEN]


def _bucket(count: Any) -> str:
    try:
        n = int(count)
    except (TypeError, ValueError):
        return "unknown"
    if n < 0:
        return "unknown"
    for bound in (1, 2, 4, 8, 16, 32, 64, 128):
        if n <= bound:
            return f"le{bound}"
    return "gt128"


def annotate_request(
    *, sport: Any = None, row_count: Any = None, batch_size: Any = None
) -> None:
    """Handler-level hook supplying non-secret labels; never reads request bodies."""
    ctx = _CTX.get()
    if ctx is None:
        return
    if sport is not None:
        ctx["sport"] = _label(sport)
    if row_count is not None:
        ctx["row_count"] = _bucket(row_count)
    if batch_size is not None:
        ctx["batch_size"] = _bucket(batch_size)


@contextmanager
def stage_timer(stage: str) -> Iterator[None]:
    """Accumulate wall time for a named stage of the current interactive request.

    No-op outside an instrumented request. Observability only: never alters or
    suppresses the wrapped work, including exceptions.
    """
    ctx = _CTX.get()
    if ctx is None or stage not in STAGES:
        yield
        return
    started = perf_counter()
    try:
        yield
    finally:
        elapsed = (perf_counter() - started) * 1000.0
        with _LOCK:
            stages = ctx["stages"]
            stages[stage] = stages.get(stage, 0.0) + elapsed


def _record(route: str, ctx: dict[str, Any], total_ms: float) -> None:
    key = (route, ctx["sport"], ctx["row_count"], ctx["batch_size"])
    with _LOCK:
        if key not in _SAMPLES and len(_SAMPLES) >= _MAX_KEYS:
            key = _OVERFLOW_KEY
        _SAMPLES.setdefault(key, deque(maxlen=_MAX_SAMPLES_PER_KEY)).append(total_ms)


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    rank = -(-len(sorted_values) * q // 1)
    return sorted_values[max(1, int(rank)) - 1]


def latency_percentiles() -> list[dict[str, Any]]:
    """p50/p95 total wall time by route, sport, row-count bucket and batch bucket."""
    with _LOCK:
        snapshot = {key: sorted(values) for key, values in _SAMPLES.items()}
    return [
        {
            "route": route,
            "sport": sport,
            "row_count": rows,
            "batch_size": batch,
            "samples": len(values),
            "p50_ms": _percentile(values, 0.50),
            "p95_ms": _percentile(values, 0.95),
            "can_execute": False,
        }
        for (route, sport, rows, batch), values in sorted(snapshot.items())
    ]


def reset_latency_samples() -> None:
    with _LOCK:
        _SAMPLES.clear()


def _outcome(status_code: int, error: BaseException | None) -> str:
    if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    if status_code in _TIMEOUT_STATUS:
        return "timeout"
    if status_code in _OVERLOAD_STATUS:
        return "overload"
    if error is not None or status_code >= 500:
        return "error"
    return "ok"


def install_interactive_latency_middleware(app: Any) -> None:
    """Install one idempotent latency probe for interactive V17 routes."""
    if getattr(app.state, "wow_interactive_latency_installed", False):
        return

    @app.middleware("http")
    async def _interactive_latency_probe(request: Any, call_next: Any):
        path = str(getattr(request.url, "path", ""))
        if path not in INTERACTIVE_PATHS:
            return await call_next(request)

        ctx: dict[str, Any] = {
            "stages": {},
            "sport": "unknown",
            "row_count": "unknown",
            "batch_size": "unknown",
        }
        token = _CTX.set(ctx)
        started = perf_counter()
        status_code = 500
        error: BaseException | None = None
        try:
            response = await call_next(request)
            status_code = int(getattr(response, "status_code", 500))
            return response
        except BaseException as exc:
            error = exc
            raise
        finally:
            _CTX.reset(token)
            total_ms = (perf_counter() - started) * 1000.0
            with _LOCK:
                stages = dict(ctx["stages"])
            _record(path, ctx, total_ms)
            stage_text = ",".join(
                f"{name}={ms:.3f}" for name, ms in sorted(stages.items())
            ) or "none"
            LOGGER.warning(
                "WOW_V17_INTERACTIVE_LATENCY route=%s method=%s status_code=%s total_ms=%.3f "
                "outcome=%s sport=%s row_count=%s batch_size=%s stages_ms=%s can_execute=false",
                path,
                str(getattr(request, "method", "UNKNOWN")),
                status_code,
                total_ms,
                _outcome(status_code, error),
                ctx["sport"],
                ctx["row_count"],
                ctx["batch_size"],
                stage_text,
            )

    app.state.wow_interactive_latency_installed = True


def latency_diagnostics_snapshot() -> dict[str, Any]:
    """Return bounded aggregate latency diagnostics with no request payload data."""
    rows = latency_percentiles()
    return {
        "status": "INTERACTIVE_LATENCY_SNAPSHOT",
        "route_sport_buckets": rows,
        "bucket_count": len(rows),
        "sample_count": sum(int(row.get("samples") or 0) for row in rows),
        "max_samples_per_bucket": _MAX_SAMPLES_PER_KEY,
        "max_buckets": _MAX_KEYS + 1,
        "probability_publishable": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }


def install_interactive_latency_diagnostics_route(
    app: Any,
    *,
    existing_auth_dependency: Any,
) -> bool:
    """Install an authenticated read-only percentile snapshot route."""
    if getattr(app.state, "wow_interactive_latency_diagnostics_installed", False):
        return True

    from github_actions_oidc import scout_route_auth_dependency

    combined_auth = scout_route_auth_dependency(existing_auth_dependency)

    @app.get(
        "/internal/v17/interactive-latency",
        operation_id="getWowV17InteractiveLatencyInternal",
        dependencies=[combined_auth],
    )
    def interactive_latency_diagnostics():
        return latency_diagnostics_snapshot()

    app.state.wow_interactive_latency_diagnostics_installed = True
    return True
