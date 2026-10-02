"""Non-secret latency telemetry for the governed interactive Action surface.

This module is observability-only. It must not inspect request bodies, alter
sporting probabilities, change terminal semantics, or enable execution.

Stage timers and percentile aggregation are per-process and best-effort: they
are diagnostic, never authoritative, and every telemetry failure is swallowed so
it cannot change a response.
"""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
import logging
import threading
from time import perf_counter
from typing import Any, Iterator, Optional


LOGGER = logging.getLogger("wow.v17.interactive_latency")
INTERACTIVE_PATHS = frozenset({
    "/score-prop",
    "/score-pick-request",
    "/score-team-event-request",
    "/score-team-event",
})
STAGES = (
    "discovery",
    "hydration",
    "fitted_scoring",
    "calibration_bounds",
    "persistence",
    "final_refresh",
    "reconciliation",
)
_SAMPLE_WINDOW = 512
_MAX_SERIES = 256
_MAX_LABEL_LEN = 48

_CURRENT: ContextVar[Optional["RequestTimings"]] = ContextVar("wow_interactive_timings", default=None)
_LOCK = threading.Lock()
_SAMPLES: dict[tuple[str, str, str, str, str], deque[float]] = {}


def _label(value: Any) -> str:
    text = "".join(ch for ch in str(value if value is not None else "unknown") if ch.isalnum() or ch in "_-.")
    return (text or "unknown")[:_MAX_LABEL_LEN]


class RequestTimings:
    """Mutable per-request stage timings plus non-secret tags."""

    def __init__(self) -> None:
        self.stages: dict[str, float] = {}
        self.sport = "unknown"
        self.row_count = 0
        self.batch_size = 0
        self._lock = threading.Lock()

    def add(self, stage: str, elapsed_ms: float) -> None:
        with self._lock:
            self.stages[stage] = self.stages.get(stage, 0.0) + elapsed_ms


def begin_request_timings() -> tuple[RequestTimings, Any]:
    timings = RequestTimings()
    return timings, _CURRENT.set(timings)


def end_request_timings(token: Any) -> None:
    try:
        _CURRENT.reset(token)
    except Exception:  # pragma: no cover - defensive; telemetry must never raise
        pass


def tag_request(*, sport: Any = None, row_count: Any = None, batch_size: Any = None) -> None:
    """Attach non-secret request shape tags to the active request, if any."""
    try:
        timings = _CURRENT.get()
        if timings is None:
            return
        if sport is not None:
            timings.sport = _label(sport)
        if row_count is not None:
            timings.row_count = max(0, int(row_count))
        if batch_size is not None:
            timings.batch_size = max(0, int(batch_size))
    except Exception:
        pass


@contextmanager
def stage_timer(stage: str) -> Iterator[None]:
    """Time one named stage against the active request; no-op outside a request."""
    started = perf_counter()
    try:
        yield
    finally:
        try:
            timings = _CURRENT.get()
            if timings is not None:
                timings.add(stage, (perf_counter() - started) * 1000.0)
        except Exception:
            pass


def _record(route: str, timings: RequestTimings, total_ms: float) -> None:
    base = (route, timings.sport, str(timings.row_count), str(timings.batch_size))
    with _LOCK:
        for name, value in (("total", total_ms), *timings.stages.items()):
            key = (*base, name)
            series = _SAMPLES.get(key)
            if series is None:
                if len(_SAMPLES) >= _MAX_SERIES:
                    continue
                series = _SAMPLES[key] = deque(maxlen=_SAMPLE_WINDOW)
            series.append(value)


def _percentile(sorted_values: list[float], fraction: float) -> float:
    index = min(len(sorted_values) - 1, max(0, int(round(fraction * (len(sorted_values) - 1)))))
    return sorted_values[index]


def latency_snapshot() -> list[dict[str, Any]]:
    """Inspectable p50/p95 by route, sport, row_count, batch_size and stage."""
    with _LOCK:
        items = [(key, sorted(values)) for key, values in _SAMPLES.items() if values]
    return [
        {
            "route": key[0],
            "sport": key[1],
            "row_count": int(key[2]),
            "batch_size": int(key[3]),
            "stage": key[4],
            "samples": len(values),
            "p50_ms": round(_percentile(values, 0.5), 3),
            "p95_ms": round(_percentile(values, 0.95), 3),
            "can_execute": False,
        }
        for key, values in sorted(items)
    ]


def reset_latency_samples() -> None:
    with _LOCK:
        _SAMPLES.clear()


def install_interactive_latency_middleware(app: Any) -> None:
    """Install one idempotent total-wall-time probe for interactive V17 routes."""
    if getattr(app.state, "wow_interactive_latency_installed", False):
        return

    @app.middleware("http")
    async def _interactive_latency_probe(request: Any, call_next: Any):
        path = str(getattr(request.url, "path", ""))
        if path not in INTERACTIVE_PATHS:
            return await call_next(request)

        timings, token = begin_request_timings()
        started = perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = int(getattr(response, "status_code", 500))
            return response
        finally:
            total_ms = (perf_counter() - started) * 1000.0
            stage_text = ""
            try:
                _record(path, timings, total_ms)
                stage_text = " ".join(f"stage_{k}_ms={v:.3f}" for k, v in sorted(timings.stages.items()))
            except Exception:
                pass
            end_request_timings(token)
            LOGGER.warning(
                "WOW_V17_INTERACTIVE_LATENCY route=%s method=%s status_code=%s total_ms=%.3f sport=%s row_count=%s batch_size=%s %s can_execute=false",
                path,
                str(getattr(request, "method", "UNKNOWN")),
                status_code,
                total_ms,
                timings.sport,
                timings.row_count,
                timings.batch_size,
                stage_text,
            )

    app.state.wow_interactive_latency_installed = True
