from __future__ import annotations

from v17.engineering_agent_team import (
    AGENT_ROLES,
    SPECIALIST_SUBAGENTS,
    reliability_blocks_frontier,
    select_priority_incident,
    select_support_subagent,
    validate_frontier_candidate,
)


def test_only_implementation_agent_can_write_code() -> None:
    writers = {name for name, role in AGENT_ROLES.items() if role["may_write_code"]}
    assert writers == {"ENGINEERING_AGENT"}
    assert all(role["may_change_probability_behavior"] is False for role in AGENT_ROLES.values())


def test_specialist_subagents_are_support_only_and_read_only() -> None:
    assert SPECIALIST_SUBAGENTS
    assert all(role["support_only"] is True for role in SPECIALIST_SUBAGENTS.values())
    assert all(role["may_write_code"] is False for role in SPECIALIST_SUBAGENTS.values())
    assert all(role["may_approve_own_work"] is False for role in SPECIALIST_SUBAGENTS.values())
    assert all(role["may_change_probability_behavior"] is False for role in SPECIALIST_SUBAGENTS.values())


def test_p0_p1_reliability_blocks_frontier_work() -> None:
    assert reliability_blocks_frontier([{"severity": "P1", "state": "OPEN"}]) is True
    assert reliability_blocks_frontier([{"severity": "P2", "state": "OPEN"}]) is False
    assert reliability_blocks_frontier([{"severity": "P0", "state": "VERIFIED_CLOSED"}]) is False


def test_priority_prefers_release_verification_within_same_severity() -> None:
    records = [
        {
            "postmortem_id": "PM-2",
            "severity": "P1",
            "state": "OPEN",
            "updated_utc": "2026-09-23T00:00:00Z",
        },
        {
            "postmortem_id": "PM-1",
            "severity": "P1",
            "state": "DEPLOYED_PENDING_VERIFY",
            "updated_utc": "2026-09-23T01:00:00Z",
        },
    ]
    decision = select_priority_incident(records)
    assert decision.incident_id == "PM-1"
    assert decision.frontier_allowed is False


def test_priority_prefers_p0_over_p1() -> None:
    decision = select_priority_incident(
        [
            {"postmortem_id": "PM-P1", "severity": "P1", "state": "OPEN"},
            {"postmortem_id": "PM-P0", "severity": "P0", "state": "OPEN"},
        ]
    )
    assert decision.incident_id == "PM-P0"


def test_specialist_routing_preserves_exact_failure_ownership() -> None:
    assert select_support_subagent(
        {"typed_failure": "ACTION_TRANSPORT_FAILURE"}
    ).subagent == "RUNTIME_TRANSPORT_SUBAGENT"
    assert select_support_subagent(
        {"typed_failure": "PERSISTENCE_FAILURE"}
    ).subagent == "DATA_PERSISTENCE_SUBAGENT"
    assert select_support_subagent(
        {"typed_failure": "CANONICAL_IDENTITY_FAILURE"}
    ).subagent == "ACQUISITION_IDENTITY_SUBAGENT"
    assert select_support_subagent(
        {"typed_failure": "MODEL_UNAVAILABLE"}
    ).subagent == "MODEL_CAPABILITY_GOVERNANCE_SUBAGENT"


def test_waiting_stage_gets_non_conflicting_support_instead_of_idle() -> None:
    ci = select_support_subagent({"wait_state": "CI_PENDING"})
    assert ci.subagent == "CI_REPOSITORY_SUBAGENT"
    assert ci.implementation_lease is False
    assert ci.support_only is True

    deploy = select_support_subagent({"wait_state": "DEPLOYMENT_PENDING"})
    assert deploy.subagent == "RUNTIME_TRANSPORT_SUBAGENT"
    assert deploy.implementation_lease is False


def test_unknown_support_route_fails_safe_to_regression_lane() -> None:
    decision = select_support_subagent({"primary_subsystem": "UNKNOWN"})
    assert decision.subagent == "REGRESSION_SAFETY_SUBAGENT"
    assert decision.implementation_lease is False
    assert decision.support_only is True


def test_frontier_candidate_cannot_directly_change_production() -> None:
    candidate = {
        "fingerprint": "ai:new-eval-method",
        "title": "New eval method",
        "radar_status": "TRIAL",
        "production_change": False,
        "evidence": ["paper"],
        "validation_plan": ["offline challenger"],
    }
    assert validate_frontier_candidate(candidate) == []
    candidate["production_change"] = True
    assert "frontier candidate must set production_change=false" in validate_frontier_candidate(candidate)
