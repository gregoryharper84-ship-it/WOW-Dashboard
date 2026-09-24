from pathlib import Path

from v17.engineering_agent_team import AGENT_ROLES
from v17.engineering_effectiveness import (
    BASE_REGRESSION,
    DATABASE_MIGRATION_VERIFICATION,
    EXACT_SHA_RENDER_VERIFICATION,
    FULL_SLATE_PRODUCTION_ACCEPTANCE,
    GOLDEN_BACKEND_ACCEPTANCE,
    GOLDEN_LIVE_HOST_ACCEPTANCE,
    GPT_EDITOR_SYNC_ACCEPTANCE,
    PROBABILITY_BEHAVIOR_REVIEW,
    WORKFLOW_HANDOFF_ACCEPTANCE,
    classify_change_impact,
    declared_user_journey_status,
    evaluate_golden_user_journey,
    product_health_allows_model_improvement,
)


def test_change_impact_runtime_requires_adjacent_production_gates() -> None:
    impact = classify_change_impact(
        [
            "artifacts/wow-engine/v17/team_event_bridge_runtime.py",
            ".github/workflows/wow-v17-nightly-multiscout.yml",
        ]
    )
    gates = set(impact["required_gates"])
    assert BASE_REGRESSION in gates
    assert WORKFLOW_HANDOFF_ACCEPTANCE in gates
    assert FULL_SLATE_PRODUCTION_ACCEPTANCE in gates
    assert EXACT_SHA_RENDER_VERIFICATION in gates
    assert GOLDEN_BACKEND_ACCEPTANCE in gates
    assert impact["can_execute"] is False


def test_change_impact_editor_change_requires_live_host_reacceptance() -> None:
    impact = classify_change_impact(["artifacts/wow-engine/WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt"])
    gates = set(impact["required_gates"])
    assert GPT_EDITOR_SYNC_ACCEPTANCE in gates
    assert GOLDEN_LIVE_HOST_ACCEPTANCE in gates


def test_change_impact_database_and_probability_adjacent_paths_fail_safe() -> None:
    impact = classify_change_impact(
        [
            "migrations/20260924_example.sql",
            "artifacts/wow-engine/v17/example_calibrator.py",
        ]
    )
    gates = set(impact["required_gates"])
    assert DATABASE_MIGRATION_VERIFICATION in gates
    assert FULL_SLATE_PRODUCTION_ACCEPTANCE in gates
    assert PROBABILITY_BEHAVIOR_REVIEW in gates


def test_golden_journey_rejects_false_green_component_health() -> None:
    result = evaluate_golden_user_journey(
        {
            "ci_success": True,
            "backend_health_success": True,
            "workflow_conclusion": "success",
            "can_execute": False,
            "terminal_authority": "V17_TERMINAL_REDUCER",
        }
    )
    assert result["status"] != "PASS"
    assert "FAIL_ACTION_INVOCATION:HOST_IDENTITY_NOT_PROVEN" in result["blockers"]
    assert "FAIL_GOVERNED_SCORING:NO_VALID_GOVERNED_ROW_OR_PROVEN_EMPTY_SLATE" in result["blockers"]


def test_golden_journey_pass_requires_live_host_and_real_governed_result() -> None:
    result = evaluate_golden_user_journey(
        {
            "host_identity": "WOW_BETTING_ENGINE",
            "live_editor_verified": True,
            "action_invoked": True,
            "inventory_accounted": True,
            "source_ingestion_complete": True,
            "all_rows_reconciled": True,
            "governed_scoring_accounted": True,
            "response_handoff_complete": True,
            "terminal_authority": "V17_TERMINAL_REDUCER",
            "can_execute": False,
            "valid_governed_row_count": 1,
            "empty_slate_proven": False,
        }
    )
    assert result["status"] == "PASS"
    assert result["blockers"] == []


def test_golden_journey_accepts_only_explicitly_proven_empty_slate() -> None:
    base = {
        "host_identity": "WOW_BETTING_ENGINE",
        "live_editor_verified": True,
        "action_invoked": True,
        "inventory_accounted": True,
        "source_ingestion_complete": True,
        "all_rows_reconciled": True,
        "governed_scoring_accounted": True,
        "response_handoff_complete": True,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
        "valid_governed_row_count": 0,
    }
    assert evaluate_golden_user_journey({**base, "empty_slate_proven": True})["status"] == "PASS"
    assert evaluate_golden_user_journey({**base, "empty_slate_proven": False})["status"] != "PASS"


def test_golden_journey_malformed_row_count_fails_closed_without_throwing() -> None:
    result = evaluate_golden_user_journey(
        {
            "host_identity": "WOW_BETTING_ENGINE",
            "live_editor_verified": True,
            "action_invoked": True,
            "inventory_accounted": True,
            "source_ingestion_complete": True,
            "all_rows_reconciled": True,
            "governed_scoring_accounted": True,
            "response_handoff_complete": True,
            "terminal_authority": "V17_TERMINAL_REDUCER",
            "can_execute": False,
            "valid_governed_row_count": "not-a-number",
            "empty_slate_proven": False,
        }
    )
    assert result["status"] != "PASS"
    assert "FAIL_GOVERNED_SCORING:VALID_GOVERNED_ROW_COUNT_INVALID" in result["blockers"]
    assert result["valid_governed_row_count"] == 0


def test_product_acceptance_agent_is_independent_and_non_mutating() -> None:
    role = AGENT_ROLES["PRODUCT_ACCEPTANCE_AGENT"]
    assert role["may_write_code"] is False
    assert role["may_approve_own_work"] is False
    assert role["may_change_probability_behavior"] is False


def test_repository_user_journey_health_fail_closed_and_blocks_model_research() -> None:
    root = Path(__file__).resolve().parents[2]
    text = (root / "artifacts/wow-engine/V17_USER_JOURNEY_HEALTH.md").read_text(encoding="utf-8")
    status = declared_user_journey_status(text)
    assert status == "FAIL"
    assert product_health_allows_model_improvement(status) is False
    assert product_health_allows_model_improvement("UNKNOWN") is False
    assert product_health_allows_model_improvement("PASS") is True
