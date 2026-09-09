from __future__ import annotations

import pytest

from v17.nightly_engineering_team_contract import (
    architect_required,
    initial_team_fields,
    route_subsystem,
    transition_record,
    validate_team_record,
)


def test_routes_all_primary_wow_model_families() -> None:
    assert route_subsystem("LLP team/event moneyline scorer") == "LLP_TEAM_EVENT_ENGINE"
    assert route_subsystem("Kalshi weather station bracket bug") == "KALSHI_WEATHER_ENGINE"
    assert route_subsystem("MLB 1IP prop adapter") == "WOW_PROP_ENGINE"
    assert route_subsystem("Action receipt host orchestration") == "WOW_HOST_ORCHESTRATION"
    assert route_subsystem("terminal reducer precedence") == "V17_TERMINAL_REDUCER"


def test_kalshi_portfolio_routes_before_generic_market_words() -> None:
    assert route_subsystem("Kalshi recovery combo concentration") == "KALSHI_PORTFOLIO"


def test_stage_ownership_and_legal_handoffs_are_machine_enforced() -> None:
    row = initial_team_fields(domain="deployment runtime", reported_by="nightly_scan")
    row = transition_record(row, to_stage="RESEARCH_TRIAGE", evidence="reporter packet")
    assert row["current_owner"] == "RESEARCH_TRIAGE_AGENT"
    row = transition_record(row, to_stage="ENGINEERING", evidence="root cause packet")
    assert row["current_owner"] == "ENGINEERING_AGENT"

    with pytest.raises(ValueError, match="illegal workflow transition"):
        transition_record(row, to_stage="QA_VERIFICATION", evidence="skip review")


def test_protected_contract_requires_system_architect() -> None:
    assert architect_required(subsystem="V17_TERMINAL_REDUCER") is True
    assert architect_required(
        subsystem="WOW_PROP_ENGINE",
        protected_contracts=["typed_failure_semantics"],
    ) is True
    assert architect_required(subsystem="WOW_PROP_ENGINE", protected_contracts=[]) is False


def test_verified_closed_cannot_skip_review_qa_and_release() -> None:
    row = initial_team_fields(domain="deployment runtime", reported_by="operator")
    row.update(
        {
            "postmortem_id": "PM-2026-09-09-999",
            "state": "VERIFIED_CLOSED",
            "engineering_fix_ids": ["FIX-2026-09-09-999"],
            "root_cause_status": "CONFIRMED",
            "reproduction_status": "REPRODUCED",
            "acceptance_criteria": ["original failure no longer reproduces"],
        }
    )
    errors = validate_team_record(row)
    assert "VERIFIED_CLOSED requires REPORTER_CLOSURE stage" in errors
    assert "VERIFIED_CLOSED requires independent review PASS" in errors
    assert "VERIFIED_CLOSED requires QA PASS" in errors


def test_full_team_lifecycle_can_close_with_verified_evidence() -> None:
    row = initial_team_fields(domain="deployment runtime", reported_by="nightly_scan")
    row.update(
        {
            "postmortem_id": "PM-2026-09-09-998",
            "state": "OPEN",
            "engineering_fix_ids": ["FIX-2026-09-09-998"],
        }
    )

    row = transition_record(row, to_stage="RESEARCH_TRIAGE", evidence="intake complete")
    row["reproduction_status"] = "REPRODUCED"
    row["root_cause_status"] = "CONFIRMED"
    row["acceptance_criteria"] = ["targeted regression passes"]
    row = transition_record(row, to_stage="ENGINEERING", evidence="root cause confirmed")
    row = transition_record(row, to_stage="INDEPENDENT_REVIEW", evidence="patch ready")
    row["review"] = {"status": "PASS", "system_architect_status": "NOT_APPLICABLE"}
    row = transition_record(row, to_stage="QA_VERIFICATION", evidence="review passed")
    row["qa"] = {"status": "PASS"}
    row = transition_record(row, to_stage="RELEASE_OBSERVABILITY", evidence="QA passed")
    row["release"] = {"status": "PRODUCTION_VERIFIED", "production_verified": True}
    row = transition_record(row, to_stage="REPORTER_CLOSURE", evidence="production verified")
    row["reporter_closure"] = {"status": "FIXED_VERIFIED"}
    row["state"] = "VERIFIED_CLOSED"

    assert validate_team_record(row) == []
