"""Non-secret latency telemetry for the governed interactive Action surface.

This module is observability-only. It must not inspect request bodies, alter
sporting probabilities, change terminal semantics, or enable execution.
"""
from __future__ import annotations

import logging
import threading
from collections import OrderedDict, deque
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter
from typing import Any, Iterator


LOGGER = logging.getLogger("wow.v17.interactive_latency")
INTERACTIVE_PATHS = frozenset({
    "/score-prop",
    "/score-pick-request",
    "/score-team-event-request",
    "/score-team-event",
})

STAGE_NAMES = frozenset({
    "discovery",
    "hydration",
    "fitted_scoring",
    "calibration_bounds",
    "persistence",
    "final_refresh",
    "reconciliation",
})
_MAX_SAMPLES_PER_KEY = 256
_MAX_KEYS = 128
_STAGE_TIMINGS: ContextVar[dict[str, float] | None] = ContextVar(
    "wow_interactive_stage_timings", default=None
)


@contextmanager
def stage_timer(stage: str) -> Iterator[None]:
    """Record wall time for a known stage into the active request, if any.

    Wrapper only: it never alters the wrapped work and is a no-op outside an
    instrumented request or for unknown stage names.
    """
    timings = _STAGE_TIMINGS.get()
    if timings is None or stage not in STAGE_NAMES:
        yield
        return
    started = perf_counter()
    try:
        yield
    finally:
        timings[stage] = timings.get(stage, 0.0) + (perf_counter() - started) * 1000.0


class LatencyAggregator:
    """Bounded in-process p50/p95 aggregator (per-process only)."""

    def __init__(self, max_keys: int = _MAX_KEYS, max_samples: int = _MAX_SAMPLES_PER_KEY) -> None:
        self._max_keys = max_keys
        self._max_samples = max_samples
        self._samples: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def record(self, key: str, total_ms: float) -> None:
        with self._lock:
            samples = self._samples.pop(key, None)
            if samples is None:
                samples = deque(maxlen=self._max_samples)
            samples.append(float(total_ms))
            self._samples[key] = samples
            while len(self._samples) > self._max_keys:
                self._samples.popitem(last=False)

    @staticmethod
    def _percentile(ordered: list[float], pct: int) -> float:
        rank = -(-len(ordered) * pct // 100)
        return ordered[max(0, min(len(ordered) - 1, rank - 1))]

    def snapshot(self) -> dict[str, dict[str, float]]:
        with self._lock:
            copied = {key: sorted(values) for key, values in self._samples.items()}
        return {
            key: {
                "count": float(len(values)),
                "p50_ms": self._percentile(values, 50),
                "p95_ms": self._percentile(values, 95),
            }
            for key, values in copied.items()
            if values
        }


AGGREGATOR = LatencyAggregator()


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
        timings: dict[str, float] = {}
        token = _STAGE_TIMINGS.set(timings)
        try:
            response = await call_next(request)
            status_code = int(getattr(response, "status_code", 500))
            return response
        finally:
            _STAGE_TIMINGS.reset(token)
            total_ms = (perf_counter() - started) * 1000.0
            AGGREGATOR.record(path, total_ms)
            stages = " ".join(f"stage_{name}_ms={ms:.3f}" for name, ms in sorted(timings.items()))
            LOGGER.warning(
                "WOW_V17_INTERACTIVE_LATENCY route=%s method=%s status_code=%s total_ms=%.3f %scan_execute=false",
                path,
                str(getattr(request, "method", "UNKNOWN")),
                status_code,
                total_ms,
                f"{stages} " if stages else "",
            )

    app.state.wow_interactive_latency_installed = True
