from __future__ import annotations

from v17.engineering_agent_team import (
    AGENT_ROLES,
    reliability_blocks_frontier,
    select_priority_incident,
    validate_frontier_candidate,
)


def test_only_implementation_agent_can_write_code() -> None:
    writers = {name for name, role in AGENT_ROLES.items() if role["may_write_code"]}
    assert writers == {"ENGINEERING_AGENT"}
    assert all(role["may_change_probability_behavior"] is False for role in AGENT_ROLES.values())


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
