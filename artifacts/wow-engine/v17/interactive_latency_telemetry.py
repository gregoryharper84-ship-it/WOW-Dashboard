"""Non-secret latency telemetry for the governed interactive Action surface.

This module is observability-only. It must not inspect request bodies, alter
sporting probabilities, change terminal semantics, or enable execution.
"""
from __future__ import annotations

import logging
import math
import threading
from collections import defaultdict
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

STAGE_NAMES = (
    "discovery",
    "hydration",
    "fitted_scoring",
    "calibration_bounds",
    "persistence",
    "final_refresh",
    "reconciliation",
)
_MAX_SAMPLES_PER_KEY = 1000


class InteractiveTimingRecord:
    """Mutable, non-secret per-request stage timings and bounded dimensions."""

    def __init__(self) -> None:
        self.stage_ms: dict[str, float] = {}
        self.sport = "unknown"
        self.row_count: int | None = None
        self.batch_size: int | None = None


_CURRENT: ContextVar[InteractiveTimingRecord | None] = ContextVar(
    "wow_v17_interactive_timing", default=None
)


@contextmanager
def stage_timer(stage: str) -> Iterator[None]:
    """Time one named stage on the active request; no-op outside one. Never raises."""
    record = _CURRENT.get()
    started = perf_counter()
    try:
        yield
    finally:
        if record is not None and stage in STAGE_NAMES:
            elapsed = (perf_counter() - started) * 1000.0
            record.stage_ms[stage] = record.stage_ms.get(stage, 0.0) + elapsed


def note_interactive_dimensions(
    *, sport: str | None = None, row_count: int | None = None, batch_size: int | None = None
) -> None:
    """Attach non-secret dimensions (never request bodies) to the active record."""
    record = _CURRENT.get()
    if record is None:
        return
    if isinstance(sport, str) and sport.strip():
        record.sport = sport.strip().upper()[:32]
    if isinstance(row_count, int) and not isinstance(row_count, bool) and row_count >= 0:
        record.row_count = row_count
    if isinstance(batch_size, int) and not isinstance(batch_size, bool) and batch_size >= 0:
        record.batch_size = batch_size


def _percentile(sorted_values: list[float], pct: float) -> float:
    rank = max(1, math.ceil(pct / 100.0 * len(sorted_values)))
    return sorted_values[rank - 1]


class LatencyAggregator:
    """In-process p50/p95 aggregation by route, sport, row_count and batch_size."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._samples: dict[tuple[str, str, int | None, int | None], list[float]] = defaultdict(list)

    def record(
        self, route: str, sport: str, row_count: int | None, batch_size: int | None, total_ms: float
    ) -> None:
        key = (route, sport, row_count, batch_size)
        with self._lock:
            samples = self._samples[key]
            samples.append(float(total_ms))
            if len(samples) > _MAX_SAMPLES_PER_KEY:
                del samples[0]

    def summary(self) -> list[dict[str, Any]]:
        with self._lock:
            items = [(key, sorted(values)) for key, values in self._samples.items() if values]
        return [
            {
                "route": route,
                "sport": sport,
                "row_count": row_count,
                "batch_size": batch_size,
                "count": len(values),
                "p50_ms": _percentile(values, 50),
                "p95_ms": _percentile(values, 95),
                "can_execute": False,
            }
            for (route, sport, row_count, batch_size), values in sorted(
                items, key=lambda item: tuple(str(part) for part in item[0])
            )
        ]

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()


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

        timing = InteractiveTimingRecord()
        token = _CURRENT.set(timing)
        started = perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = int(getattr(response, "status_code", 500))
            return response
        finally:
            total_ms = (perf_counter() - started) * 1000.0
            _CURRENT.reset(token)
            try:
                AGGREGATOR.record(path, timing.sport, timing.row_count, timing.batch_size, total_ms)
            except Exception:  # observability must never alter the response
                pass
            stages = " ".join(
                f"{name}_ms={timing.stage_ms[name]:.3f}" for name in STAGE_NAMES if name in timing.stage_ms
            )
            LOGGER.warning(
                "WOW_V17_INTERACTIVE_LATENCY route=%s method=%s status_code=%s total_ms=%.3f"
                " sport=%s row_count=%s batch_size=%s %s can_execute=false",
                path,
                str(getattr(request, "method", "UNKNOWN")),
                status_code,
                total_ms,
                timing.sport,
                timing.row_count,
                timing.batch_size,
                stages,
            )

    app.state.wow_interactive_latency_installed = True
