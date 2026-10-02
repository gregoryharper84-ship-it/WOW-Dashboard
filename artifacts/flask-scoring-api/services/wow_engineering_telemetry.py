"""Vendor-neutral OpenTelemetry telemetry for the WOW engineering control plane.

This module observes engineering work only. It does not originate, modify,
publish, or execute sporting probabilities, wagers, or market orders.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


TraceSink = Callable[[Mapping[str, Any]], None]
EventSink = Callable[[Mapping[str, Any]], None]

FIXES_METRIC = "wow.engineering.fixes"
WAKES_METRIC = "wow.engineering.queue.wakes"
REPARKS_METRIC = "wow.engineering.queue.reparks"
UNBLOCKS_METRIC = "wow.engineering.queue.unblocks"
MAX_WAKE_ESCALATIONS_METRIC = "wow.engineering.queue.max_wake_escalations"
RECORDED_COST_METRIC = "wow.engineering.recorded_cost.usd"
TOOL_CALLS_METRIC = "wow.engineering.agent.tool_calls"
INPUT_TOKENS_METRIC = "wow.engineering.agent.input_tokens"
OUTPUT_TOKENS_METRIC = "wow.engineering.agent.output_tokens"


@dataclass(frozen=True)
class AgenticTelemetrySinks:
    trace_sink: TraceSink
    queue_sink: EventSink
    usage_sink: EventSink


def _nonnegative_number(event: Mapping[str, Any], key: str) -> float | None:
    raw = event.get(key)
    if raw is None:
        return None
    value = float(raw)
    if value < 0:
        raise ValueError(f"{key} may not be negative")
    return value


def efficiency_ratios(
    *,
    fixes: int,
    wakes: int,
    unblocks: int,
    max_wake_escalations: int,
    recorded_cost_usd: float,
) -> dict[str, float | None]:
    """Derive operator-facing efficiency ratios from monotonic counters.

    `recorded_cost_usd` must be actual provider/tool cost supplied by an adapter;
    this module never estimates cost from token counts or model names.

    Max-wake escalation rate is intentionally scoped to parked-ticket terminal
    outcomes (`unblocks + max_wake_escalations`), not all engineering fixes.
    Otherwise fixes that never parked would make wake reliability look better
    than it actually is.
    """
    for name, value in {
        "fixes": fixes,
        "wakes": wakes,
        "unblocks": unblocks,
        "max_wake_escalations": max_wake_escalations,
        "recorded_cost_usd": recorded_cost_usd,
    }.items():
        if value < 0:
            raise ValueError(f"{name} may not be negative")

    terminal_parked_outcomes = unblocks + max_wake_escalations
    return {
        "cost_per_fix_usd": (recorded_cost_usd / fixes) if fixes else None,
        "wake_to_fix_ratio": (wakes / fixes) if fixes else None,
        "max_wake_escalation_rate": (
            max_wake_escalations / terminal_parked_outcomes
            if terminal_parked_outcomes
            else None
        ),
    }


def build_opentelemetry_agentic_telemetry(
    instrumentation_name: str = "wow.engineering.control_plane",
) -> AgenticTelemetrySinks:
    """Build trace, queue, and explicit-usage sinks over OpenTelemetry APIs.

    Export/provider configuration remains runtime-owned. With no SDK provider,
    OpenTelemetry safely behaves as a no-op. Metric attributes deliberately stay
    low-cardinality; ticket IDs remain in traces rather than metric labels.
    """
    from opentelemetry import metrics, trace

    tracer = trace.get_tracer(instrumentation_name)
    meter = metrics.get_meter(instrumentation_name)

    fixes = meter.create_counter(FIXES_METRIC, unit="{fix}")
    wakes = meter.create_counter(WAKES_METRIC, unit="{wake}")
    reparks = meter.create_counter(REPARKS_METRIC, unit="{repark}")
    unblocks = meter.create_counter(UNBLOCKS_METRIC, unit="{unblock}")
    max_wake_escalations = meter.create_counter(
        MAX_WAKE_ESCALATIONS_METRIC, unit="{escalation}"
    )
    recorded_cost = meter.create_counter(RECORDED_COST_METRIC, unit="USD")
    tool_calls = meter.create_counter(TOOL_CALLS_METRIC, unit="{call}")
    input_tokens = meter.create_counter(INPUT_TOKENS_METRIC, unit="{token}")
    output_tokens = meter.create_counter(OUTPUT_TOKENS_METRIC, unit="{token}")

    def trace_sink(event: Mapping[str, Any]) -> None:
        stage = str(event.get("stage", "unknown")).strip() or "unknown"
        status = str(event.get("status", "UNKNOWN"))
        span_name = f"wow.engineering.{stage.lower()}"
        with tracer.start_as_current_span(span_name) as span:
            span.set_attribute("wow.engineering.issue_id", str(event.get("issue_id", "UNKNOWN")))
            span.set_attribute("wow.engineering.stage", stage)
            span.set_attribute("wow.engineering.status", status)
            span.set_attribute(
                "wow.engineering.attempt_count",
                int(event.get("attempt_count", 0)),
            )
            detail = str(event.get("detail", ""))
            if detail:
                span.set_attribute("wow.engineering.detail", detail[:2048])
        if stage == "REPORTER_CLOSURE" and status == "FIXED_AND_VERIFIED":
            fixes.add(1)

    def queue_sink(event: Mapping[str, Any]) -> None:
        event_name = str(event.get("event_name", "")).strip().upper()
        reason = str(event.get("parked_reason") or "NONE")[:128]
        attributes = {"wow.engineering.parked_reason": reason}
        if event_name == "QUEUE_WAKE_RECHECK":
            wakes.add(1, attributes=attributes)
        elif event_name == "QUEUE_REPARKED":
            reparks.add(1, attributes=attributes)
        elif event_name == "QUEUE_UNBLOCKED":
            unblocks.add(1, attributes=attributes)
        elif event_name == "QUEUE_MAX_WAKE_ESCALATED":
            max_wake_escalations.add(1, attributes=attributes)

    def usage_sink(event: Mapping[str, Any]) -> None:
        """Record only explicit adapter usage receipts; never infer monetary cost."""
        cost = _nonnegative_number(event, "recorded_cost_usd")
        calls = _nonnegative_number(event, "tool_calls")
        in_tokens = _nonnegative_number(event, "input_tokens")
        out_tokens = _nonnegative_number(event, "output_tokens")
        if cost is not None:
            recorded_cost.add(cost)
        if calls is not None:
            tool_calls.add(int(calls))
        if in_tokens is not None:
            input_tokens.add(int(in_tokens))
        if out_tokens is not None:
            output_tokens.add(int(out_tokens))

    return AgenticTelemetrySinks(
        trace_sink=trace_sink,
        queue_sink=queue_sink,
        usage_sink=usage_sink,
    )


def build_opentelemetry_trace_sink(
    instrumentation_name: str = "wow.engineering.control_plane",
) -> TraceSink:
    """Backward-compatible trace-only adapter."""
    return build_opentelemetry_agentic_telemetry(instrumentation_name).trace_sink
