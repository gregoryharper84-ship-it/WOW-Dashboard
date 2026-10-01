from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

import pytest
import yaml
from langgraph.checkpoint.memory import InMemorySaver

from services.wow_engineering_control_plane import (
    EngineeringAdapters,
    MAX_REPAIR_ATTEMPTS,
    PROBABILITY_AUTHORITY,
    TERMINAL_AUTHORITY,
    build_engineering_graph,
    invoke_issue,
)


def _base_issue(**overrides):
    issue = {
        "issue_id": "WOW-ENG-TEST-001",
        "severity": "P1",
        "change_class": "B",
        "component": "event_identity",
        "sport": "NFL",
        "promotion_authorized": False,
    }
    issue.update(overrides)
    return issue


def _adapters(*, sandbox_results=(True,), promotion_release=True, root_cause_value="canonical identity mismatch"):
    calls = Counter()
    results = iter(sandbox_results)

    def intake(state):
        calls["intake"] += 1
        return {"reproduction_evidence": ["synthetic failing fixture reproduced"]}

    def triage(state):
        calls["triage"] += 1
        return {}

    def diagnostics(state):
        calls["diagnostics"] += 1
        return {"diagnostics": {"first_bad_stage": "identity_resolution", "exception": "mismatch"}}

    def data_audit(state):
        calls["data_audit"] += 1
        return {"data_audit": {"event_alias_consistent": False, "stale": False}}

    def root_cause(state):
        calls["root_cause"] += 1
        assert "diagnostics" in state
        assert "data_audit" in state
        return {"root_cause": root_cause_value}

    def engineer(state):
        calls["engineer"] += 1
        return {"changed_files": ["services/event_identity.py"]}

    def sandbox_test(state):
        calls["sandbox_test"] += 1
        passed = next(results)
        return {
            "tests_passed": passed,
            "tests_run": ["pytest tests/test_event_identity.py -q"],
            "regression_results": {"adjacent_lanes": "PASS" if passed else "FAIL"},
        }

    def independent_review(state):
        calls["independent_review"] += 1
        return {"review_status": "APPROVED"}

    def release(state):
        calls["release"] += 1
        return {"deployment_status": "DEPLOYED" if promotion_release else "FAILED"}

    def production_qa(state):
        calls["production_qa"] += 1
        return {"qa_status": "PASSED", "production_verification": "VERIFIED"}

    return (
        EngineeringAdapters(
            intake=intake,
            triage=triage,
            diagnostics=diagnostics,
            data_audit=data_audit,
            root_cause=root_cause,
            engineer=engineer,
            sandbox_test=sandbox_test,
            independent_review=independent_review,
            release=release,
            production_qa=production_qa,
        ),
        calls,
    )


def _graph(adapters):
    return build_engineering_graph(adapters, checkpointer=InMemorySaver())


def test_class_b_stops_at_pr_without_governed_promotion_authority():
    adapters, calls = _adapters()
    result = invoke_issue(_graph(adapters), _base_issue())

    assert result["terminal_status"] == "PR_CREATED"
    assert result["can_execute"] is False
    assert result["terminal_authority"] == TERMINAL_AUTHORITY
    assert result["probability_authority"] == PROBABILITY_AUTHORITY
    assert calls["diagnostics"] == 1
    assert calls["data_audit"] == 1
    assert calls["release"] == 0
    assert calls["production_qa"] == 0


def test_diagnostics_and_data_audit_both_complete_before_root_cause():
    adapters, calls = _adapters()
    result = invoke_issue(_graph(adapters), _base_issue(issue_id="WOW-ENG-TEST-PARALLEL"))

    assert result["root_cause"] == "canonical identity mismatch"
    assert calls["diagnostics"] == 1
    assert calls["data_audit"] == 1
    stages = [event["stage"] for event in result["trace_events"]]
    assert "DIAGNOSTICS" in stages
    assert "DATA_AUDIT" in stages
    assert stages.index("ROOT_CAUSE") > stages.index("DIAGNOSTICS")
    assert stages.index("ROOT_CAUSE") > stages.index("DATA_AUDIT")


def test_sandbox_failure_retries_exactly_three_times_then_blocks():
    adapters, calls = _adapters(sandbox_results=(False, False, False))
    result = invoke_issue(_graph(adapters), _base_issue(issue_id="WOW-ENG-TEST-RETRY"))

    assert MAX_REPAIR_ATTEMPTS == 3
    assert calls["engineer"] == 3
    assert calls["sandbox_test"] == 3
    assert result["attempt_count"] == 3
    assert result["terminal_status"] == "BLOCKED_WITH_EXACT_REASON"
    assert "3 governed repair attempts" in result["blocker"]
    assert result["smallest_remaining_action"]


def test_successful_retry_continues_without_repeating_diagnostics():
    adapters, calls = _adapters(sandbox_results=(False, True))
    result = invoke_issue(_graph(adapters), _base_issue(issue_id="WOW-ENG-TEST-REPAIR"))

    assert result["terminal_status"] == "PR_CREATED"
    assert calls["engineer"] == 2
    assert calls["sandbox_test"] == 2
    assert calls["diagnostics"] == 1
    assert calls["data_audit"] == 1


def test_class_c_can_only_terminate_as_experiment_before_release():
    adapters, calls = _adapters()
    result = invoke_issue(
        _graph(adapters),
        _base_issue(issue_id="WOW-ENG-TEST-CLASS-C", change_class="C", promotion_authorized=True),
    )

    assert result["terminal_status"] == "EXPERIMENT_CREATED"
    assert calls["release"] == 0
    assert calls["production_qa"] == 0


def test_governed_authorized_release_requires_post_deploy_qa_to_close_fixed():
    adapters, calls = _adapters()
    result = invoke_issue(
        _graph(adapters),
        _base_issue(issue_id="WOW-ENG-TEST-RELEASE", change_class="A", promotion_authorized=True),
    )

    assert result["terminal_status"] == "FIXED_AND_VERIFIED"
    assert result["qa_status"] == "PASSED"
    assert result["production_verification"] == "VERIFIED"
    assert calls["release"] == 1
    assert calls["production_qa"] == 1


def test_missing_root_cause_fails_closed_with_exact_terminal():
    adapters, calls = _adapters(root_cause_value=None)
    result = invoke_issue(
        _graph(adapters), _base_issue(issue_id="WOW-ENG-TEST-NO-ROOT-CAUSE")
    )

    assert result["terminal_status"] == "BLOCKED_WITH_EXACT_REASON"
    assert "root cause was not established" in result["blocker"]
    assert calls["engineer"] == 0


def test_governance_input_cannot_enable_execution_or_disable_dry_run():
    adapters, _ = _adapters()
    graph = _graph(adapters)

    with pytest.raises(ValueError, match="GOVERNANCE_INPUT_REJECTED"):
        invoke_issue(graph, _base_issue(issue_id="WOW-ENG-TEST-EXEC", can_execute=True))

    with pytest.raises(ValueError, match="GOVERNANCE_INPUT_REJECTED"):
        invoke_issue(
            graph,
            _base_issue(
                issue_id="WOW-ENG-TEST-LIVE",
                dry_run_only_no_live_trading_no_market_orders=False,
            ),
        )


def test_probability_payload_is_rejected_before_graph_execution():
    adapters, calls = _adapters()
    with pytest.raises(ValueError, match="PROBABILITY_BOUNDARY_VIOLATION"):
        invoke_issue(
            _graph(adapters),
            _base_issue(issue_id="WOW-ENG-TEST-PROB", model_probability=0.61),
        )
    assert not calls


def test_synthetic_regression_fixture_pack_covers_initial_failure_classes():
    fixture_path = Path(__file__).parent / "fixtures" / "wow_engineering_synthetic_incidents.json"
    fixtures = json.loads(fixture_path.read_text())
    fixture_ids = {row["fixture_id"] for row in fixtures}
    assert fixture_ids == {
        "event-identity-conflict",
        "provider-timeout",
        "malformed-american-odds",
        "future-information-leakage",
        "missing-calibration-artifact",
    }
    assert all(row["expected_typed_failure"] for row in fixtures)
    assert all("model_probability" not in row["payload"] for row in fixtures)


def test_machine_readable_dots_contract_activates_langgraph_without_new_probability_authority():
    root = Path(__file__).resolve().parents[3]
    contract_path = root / "artifacts" / "wow-engine" / "WOW_DOTS_ENGINEERING_ORCHESTRATOR.yaml"
    contract = yaml.safe_load(contract_path.read_text())
    control = contract["control_plane"]
    assert control["framework"] == "LANGGRAPH"
    assert control["change_class"] == "B"
    assert control["max_repair_attempts"] == 3
    assert control["parallel_evidence_lanes"] == ["DIAGNOSTICS", "DATA_AUDIT"]
    assert control["production_checkpointer"] == "PostgresSaver"
    assert control["thread_identity"] == "issue_id"
    assert control["probability_authority"] == "NONE"
    assert control["class_c_release_allowed"] is False
    assert contract["can_execute"] is False
    assert contract["terminal_authority"] == "V17_TERMINAL_REDUCER"
