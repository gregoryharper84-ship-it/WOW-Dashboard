"""Vendor-neutral OpenTelemetry sink for WOW engineering control-plane events."""
from __future__ import annotations

from typing import Any, Callable, Mapping


TraceSink = Callable[[Mapping[str, Any]], None]


def build_opentelemetry_trace_sink(
    instrumentation_name: str = "wow.engineering.control_plane",
) -> TraceSink:
    """Return a sink that records one child span per engineering stage.

    Export/provider configuration remains owned by the runtime. With no SDK
    provider installed, the OpenTelemetry API safely behaves as a no-op.
    No sporting probability, wager, order, secret, or provider credential is
    accepted or emitted by this sink.
    """
    from opentelemetry import trace

    tracer = trace.get_tracer(instrumentation_name)

    def sink(event: Mapping[str, Any]) -> None:
        stage = str(event.get("stage", "unknown")).strip() or "unknown"
        span_name = f"wow.engineering.{stage.lower()}"
        with tracer.start_as_current_span(span_name) as span:
            span.set_attribute("wow.engineering.issue_id", str(event.get("issue_id", "UNKNOWN")))
            span.set_attribute("wow.engineering.stage", stage)
            span.set_attribute("wow.engineering.status", str(event.get("status", "UNKNOWN")))
            span.set_attribute(
                "wow.engineering.attempt_count",
                int(event.get("attempt_count", 0)),
            )
            detail = str(event.get("detail", ""))
            if detail:
                span.set_attribute("wow.engineering.detail", detail[:2048])

    return sink
