"""Non-secret latency telemetry for the governed interactive Action surface.

This module is observability-only. It must not inspect request bodies, alter
sporting probabilities, change terminal semantics, or enable execution.
"""
from __future__ import annotations

import logging
import math
import threading
from collections import deque
from contextlib import contextmanager
from time import perf_counter
from typing import Any, Callable, Dict, Iterator, Optional, Tuple


LOGGER = logging.getLogger("wow.v17.interactive_latency")
INTERACTIVE_PATHS = frozenset({
    "/score-prop",
    "/score-pick-request",
    "/score-team-event-request",
    "/score-team-event",
})
INTERACTIVE_STAGES = (
    "discovery",
    "hydration",
    "fitted_scoring",
    "calibration_bounds",
    "persistence",
    "final_refresh",
    "reconciliation",
    "total",
)
MAX_SAMPLES_PER_SERIES = 512
MAX_SERIES = 256


def row_count_bucket(count: Optional[int]) -> str:
    """Bounded-cardinality bucket so row/batch counts are never raw labels."""
    if count is None or count < 0:
        return "unknown"
    for bound in (1, 5, 10, 25, 50, 100):
        if count <= bound:
            return "le_%d" % bound
    return "gt_100"


class InteractiveStageTimer:
    """Per-request stage timer. Observability only: never alters scoring."""

    def __init__(self, route: str, clock: Callable[[], float] = perf_counter) -> None:
        self.route = route
        self._clock = clock
        self._started = clock()
        self.stages_ms: Dict[str, float] = {}

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        started = self._clock()
        try:
            yield
        finally:
            elapsed = (self._clock() - started) * 1000.0
            self.stages_ms[name] = self.stages_ms.get(name, 0.0) + elapsed

    def finish(self) -> Dict[str, float]:
        self.stages_ms["total"] = (self._clock() - self._started) * 1000.0
        return dict(self.stages_ms)

    def log(self, sport: str = "unknown", row_count: Optional[int] = None,
            batch_size: Optional[int] = None) -> None:
        stages = self.finish()
        parts = " ".join(
            "%s_ms=%.3f" % (name, stages[name]) for name in INTERACTIVE_STAGES if name in stages
        )
        LOGGER.warning(
            "WOW_V17_INTERACTIVE_STAGE route=%s sport=%s row_count=%s batch_size=%s %s can_execute=false",
            self.route,
            sport,
            row_count_bucket(row_count),
            row_count_bucket(batch_size),
            parts,
        )


class LatencyAggregator:
    """Thread-safe bounded p50/p95 aggregation by (route, sport, row_count, batch_size)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._series: Dict[Tuple[str, str, str, str], deque] = {}

    def record(self, route: str, total_ms: float, sport: str = "unknown",
               row_count: Optional[int] = None, batch_size: Optional[int] = None) -> None:
        key = (str(route), str(sport), row_count_bucket(row_count), row_count_bucket(batch_size))
        with self._lock:
            series = self._series.get(key)
            if series is None:
                if len(self._series) >= MAX_SERIES:
                    return
                series = self._series[key] = deque(maxlen=MAX_SAMPLES_PER_SERIES)
            series.append(float(total_ms))

    @staticmethod
    def _percentile(ordered: list, q: float) -> float:
        rank = max(1, math.ceil(q * len(ordered)))
        return ordered[rank - 1]

    def snapshot(self) -> Dict[Tuple[str, str, str, str], Dict[str, float]]:
        with self._lock:
            copies = {key: sorted(values) for key, values in self._series.items() if values}
        return {
            key: {
                "count": len(ordered),
                "p50_ms": self._percentile(ordered, 0.50),
                "p95_ms": self._percentile(ordered, 0.95),
            }
            for key, ordered in copies.items()
        }


LATENCY_AGGREGATOR = LatencyAggregator()


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
            LATENCY_AGGREGATOR.record(path, total_ms)
            LOGGER.warning(
                "WOW_V17_INTERACTIVE_LATENCY route=%s method=%s status_code=%s total_ms=%.3f can_execute=false",
                path,
                str(getattr(request, "method", "UNKNOWN")),
                status_code,
                total_ms,
            )

    app.state.wow_interactive_latency_installed = True
