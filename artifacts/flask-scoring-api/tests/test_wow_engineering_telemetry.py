from pathlib import Path

import pytest
import yaml

from services.wow_engineering_telemetry import (
    MAX_WAKE_ESCALATIONS_METRIC,
    RECORDED_COST_METRIC,
    WAKES_METRIC,
    build_opentelemetry_agentic_telemetry,
    build_opentelemetry_trace_sink,
    efficiency_ratios,
)


ROOT = Path(__file__).resolve().parents[3]
TELEMETRY_CONTRACT = ROOT / "artifacts" / "wow-engine" / "WOW_ENGINEERING_AGENTIC_TELEMETRY.yaml"


def test_opentelemetry_trace_sink_accepts_governed_engineering_event_without_exporter():
    sink = build_opentelemetry_trace_sink()
    sink(
        {
            "stage": "SANDBOX_TEST",
            "status": "COMPLETED",
            "issue_id": "WOW-ENG-OTEL-001",
            "attempt_count": 2,
            "detail": "synthetic regression trace",
        }
    )


def test_agentic_telemetry_accepts_trace_queue_and_explicit_usage_without_exporter():
    telemetry = build_opentelemetry_agentic_telemetry()
    telemetry.trace_sink(
        {
            "stage": "REPORTER_CLOSURE",
            "status": "FIXED_AND_VERIFIED",
            "issue_id": "WOW-ENG-OTEL-002",
            "attempt_count": 1,
            "detail": "verified",
        }
    )
    telemetry.queue_sink(
        {
            "event_name": "QUEUE_WAKE_RECHECK",
            "parked_reason": "awaiting_pr_review",
        }
    )
    telemetry.queue_sink(
        {
            "event_name": "QUEUE_MAX_WAKE_ESCALATED",
            "parked_reason": "upstream_dependency",
        }
    )
    telemetry.usage_sink(
        {
            "recorded_cost_usd": 0.42,
            "tool_calls": 7,
            "input_tokens": 1200,
            "output_tokens": 300,
        }
    )


def test_efficiency_ratios_define_cost_wake_and_escalation_kpis():
    ratios = efficiency_ratios(
        fixes=4,
        wakes=10,
        max_wake_escalations=1,
        recorded_cost_usd=12.0,
    )
    assert ratios == {
        "cost_per_fix_usd": 3.0,
        "wake_to_fix_ratio": 2.5,
        "max_wake_escalation_rate": 0.2,
    }


def test_efficiency_ratios_fail_closed_when_denominator_is_zero():
    ratios = efficiency_ratios(
        fixes=0,
        wakes=3,
        max_wake_escalations=0,
        recorded_cost_usd=1.25,
    )
    assert ratios["cost_per_fix_usd"] is None
    assert ratios["wake_to_fix_ratio"] is None
    assert ratios["max_wake_escalation_rate"] is None


def test_usage_sink_rejects_negative_or_invented_cost_values():
    telemetry = build_opentelemetry_agentic_telemetry()
    with pytest.raises(ValueError, match="recorded_cost_usd may not be negative"):
        telemetry.usage_sink({"recorded_cost_usd": -0.01})


def test_metric_names_are_stable_for_dashboard_queries():
    assert WAKES_METRIC == "wow.engineering.queue.wakes"
    assert MAX_WAKE_ESCALATIONS_METRIC == "wow.engineering.queue.max_wake_escalations"
    assert RECORDED_COST_METRIC == "wow.engineering.recorded_cost.usd"


def test_agentic_telemetry_contract_preserves_governance_and_exact_kpi_formulas():
    contract = yaml.safe_load(TELEMETRY_CONTRACT.read_text())
    assert contract["can_execute"] is False
    assert contract["probability_authority"] == "NONE"
    assert contract["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert contract["cost_governance"]["estimates_allowed"] is False
    assert contract["cost_governance"]["require_explicit_adapter_receipt"] is True
    assert contract["operator_kpis"] == {
        "cost_per_fix_usd": "recorded_cost_usd / fixes",
        "wake_to_fix_ratio": "wakes / fixes",
        "max_wake_escalation_rate": "max_wake_escalations / (fixes + max_wake_escalations)",
        "zero_denominator_behavior": None,
    }
