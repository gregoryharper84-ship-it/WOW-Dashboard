"""Non-secret latency telemetry for the governed interactive Action surface.

This module is observability-only. It must not inspect request bodies, alter
sporting probabilities, change terminal semantics, or enable execution.
"""
from __future__ import annotations

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
STAGE_LOGGER = logging.getLogger("wow.v17.interactive_stage")
MAX_SAMPLES_PER_KEY = 512
MAX_KEYS = 64


class LatencyRecorder:
    """Bounded, thread-safe in-process p50/p95 recorder (per-process stats only)."""

    def __init__(self, max_samples: int = MAX_SAMPLES_PER_KEY, max_keys: int = MAX_KEYS) -> None:
        self._max_samples = max(1, int(max_samples))
        self._max_keys = max(1, int(max_keys))
        self._lock = threading.Lock()
        self._samples: dict[str, deque[float]] = {}

    def record(self, key: str, value_ms: float) -> None:
        key = str(key)
        with self._lock:
            bucket = self._samples.get(key)
            if bucket is None:
                if len(self._samples) >= self._max_keys:
                    return  # bounded label cardinality; never grow
                bucket = self._samples[key] = deque(maxlen=self._max_samples)
            bucket.append(float(value_ms))

    def percentiles(self, key: str) -> dict[str, float | int] | None:
        with self._lock:
            data = sorted(self._samples.get(str(key), ()))
        if not data:
            return None

        def _nearest_rank(pct: int) -> float:
            rank = -(-pct * len(data) // 100)  # ceil(pct * n / 100)
            return data[max(1, min(rank, len(data))) - 1]

        return {"count": len(data), "p50_ms": _nearest_rank(50), "p95_ms": _nearest_rank(95)}

    def clear(self) -> None:
        with self._lock:
            self._samples.clear()


RECORDER = LatencyRecorder()


@contextmanager
def stage_timer(route: str, stage: str) -> Iterator[None]:
    """Emit one non-secret stage_ms line (also on exceptions); never alters results."""
    started = perf_counter()
    try:
        yield
    finally:
        stage_ms = (perf_counter() - started) * 1000.0
        RECORDER.record(f"{route}|{stage}", stage_ms)
        STAGE_LOGGER.warning(
            "WOW_V17_INTERACTIVE_STAGE route=%s stage=%s stage_ms=%.3f can_execute=false",
            route,
            stage,
            stage_ms,
        )


def install_interactive_latency_middleware(app: Any) -> None:
    """Install one idempotent total-wall-time probe for interactive V17 routes."""
    if getattr(app.state, "wow_interactive_latency_installed", False):
        return

    @app.middleware("http")
    async def _interactive_latency_probe(request: Any, call_next: Any):
        path = str(getattr(request.url, "path", ""))
        if path not in INTERACTIVE_PATHS:
            return await call_next(request)

        started = perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = int(getattr(response, "status_code", 500))
            return response
        finally:
            total_ms = (perf_counter() - started) * 1000.0
            RECORDER.record(f"{path}|total", total_ms)
            LOGGER.warning(
                "WOW_V17_INTERACTIVE_LATENCY route=%s method=%s status_code=%s total_ms=%.3f can_execute=false",
                path,
                str(getattr(request, "method", "UNKNOWN")),
                status_code,
                total_ms,
            )

    app.state.wow_interactive_latency_installed = True
