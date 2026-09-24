from types import SimpleNamespace

from v17.sep16_evidence_handoff_rank_fix import _annotate_schema_mismatch
from v17.sep17_team_event_publication_chain_repair import _reconcile_governance_stage_state


def _req(**kwargs):
    values = {
        "official_event_id": "823410",
        "sport_specific_evidence": {},
    }
    values.update(kwargs)
    return SimpleNamespace(**values)


def test_expected_hydration_hold_is_not_reclassified_as_schema_mismatch():
    result = {
        "code": "LLP_EVENT_GOVERNANCE_NOT_PROVEN",
        "blockers": ["V17_EVENT_EVIDENCE_HANDOFF_REPAIR_NOT_PASS"],
        "evidence_handoff_repair": {
            "status": "HOLD",
            "blockers": ["OFFICIAL_LINEUP_NOT_CONFIRMED"],
            "can_execute": False,
        },
        "llp_governance": {
            "status": "HOLD",
            "probability_audit": {
                "reasons": ["OFFICIAL_EVENT_ID_EVIDENCE_MISSING"],
            },
        },
        "rank_eligible": False,
        "probability_publishable": False,
        "can_execute": False,
    }

    out = _annotate_schema_mismatch(
        _req(),
        {"official_event_id": "823410"},
        result,
    )

    assert out == result
    assert "evidence_handoff_schema_mismatch" not in out
    assert "RUN_INVALID_EVIDENCE_BINDING" not in out["blockers"]
    assert out["evidence_handoff_repair"]["blockers"] == ["OFFICIAL_LINEUP_NOT_CONFIRMED"]
    assert out["rank_eligible"] is False
    assert out["probability_publishable"] is False
    assert out["can_execute"] is False


def test_projected_lineup_timing_hold_does_not_invent_identity_schema_mismatch_without_receipt():
    result = {
        "code": "LINEUP_PROJECTED_PROBABILITY_AVAILABLE",
        "blockers": ["LINEUP_CONFIRMATION_PENDING"],
        "llp_governance": {
            "status": "HOLD",
            "probability_audit": {
                "reasons": ["OFFICIAL_EVENT_ID_EVIDENCE_MISSING"],
            },
        },
        "rank_eligible": False,
        "probability_publishable": False,
        "can_execute": False,
    }

    out = _annotate_schema_mismatch(
        _req(),
        {"official_event_id": "823410"},
        result,
    )

    assert out == result
    assert "evidence_handoff_schema_mismatch" not in out
    assert "RUN_INVALID_EVIDENCE_BINDING" not in out["blockers"]
    assert out["blockers"] == ["LINEUP_CONFIRMATION_PENDING"]
    assert out["rank_eligible"] is False
    assert out["probability_publishable"] is False
    assert out["can_execute"] is False


def test_projected_lineup_timing_hold_does_not_hide_unrelated_real_schema_mismatch():
    result = {
        "code": "LINEUP_PROJECTED_PROBABILITY_AVAILABLE",
        "blockers": ["LINEUP_CONFIRMATION_PENDING"],
        "llp_governance": {
            "status": "HOLD",
            "probability_audit": {
                "reasons": [
                    "OFFICIAL_EVENT_ID_EVIDENCE_MISSING",
                    "INDEPENDENT_PROBABILITY_MISSING",
                ],
            },
        },
        "rank_eligible": False,
        "probability_publishable": False,
        "can_execute": False,
    }

    out = _annotate_schema_mismatch(
        _req(),
        {
            "official_event_id": "823410",
            "independent_home_probability": 0.54,
            "independent_away_probability": 0.46,
        },
        result,
    )

    assert out["run_validity_status"] == "RUN_INVALID_EVIDENCE_BINDING"
    assert out["evidence_handoff_schema_mismatch"]["contradictions"] == [
        "INDEPENDENT_PROBABILITY_MISSING"
    ]
    assert "V17_HANDOFF_SCHEMA_MISMATCH:OFFICIAL_EVENT_ID_EVIDENCE_MISSING" not in out["blockers"]
    assert out["rank_eligible"] is False
    assert out["probability_publishable"] is False
    assert out["can_execute"] is False


def test_completed_handoff_still_detects_real_identity_binding_contradiction():
    result = {
        "blockers": [],
        "evidence_handoff_repair": {
            "status": "PASS",
            "can_execute": False,
        },
        "llp_governance": {
            "status": "HOLD",
            "probability_audit": {
                "reasons": ["OFFICIAL_EVENT_ID_EVIDENCE_MISSING"],
            },
        },
        "rank_eligible": False,
        "probability_publishable": False,
        "can_execute": False,
    }

    out = _annotate_schema_mismatch(
        _req(),
        {"official_event_id": "823410"},
        result,
    )

    assert out["run_validity_status"] == "RUN_INVALID_EVIDENCE_BINDING"
    assert out["evidence_handoff_schema_mismatch"]["contradictions"] == [
        "OFFICIAL_EVENT_ID_EVIDENCE_MISSING"
    ]
    assert out["rank_eligible"] is False
    assert out["probability_publishable"] is False
    assert out["can_execute"] is False


def test_nested_pass_stage_state_is_mirrored_without_rank_upgrade():
    output = {
        "llp_governance": {
            "status": "HOLD",
            "probability_audit_result": "PASS_PROBABILITY_AUDIT",
            "event_decision": "SELECTED",
            "event_mutex_status": "PASS",
        },
        "llp_probability_audit_result": "NOT_PROVEN",
        "llp_event_decision": "NOT_PROVEN",
        "event_mutex_status": "NOT_PROVEN",
        "probability_audit_passed": False,
        "event_governor_complete": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }

    out = _reconcile_governance_stage_state(output)

    assert out["llp_probability_audit_result"] == "PASS_PROBABILITY_AUDIT"
    assert out["llp_event_decision"] == "SELECTED"
    assert out["event_mutex_status"] == "PASS"
    assert out["probability_audit_passed"] is True
    assert out["event_governor_complete"] is True
    assert out["probability_publishable"] is False
    assert out["rank_eligible"] is False
    assert out["can_execute"] is False


def test_failed_probability_audit_remains_failed_while_completed_governor_is_reported():
    output = {
        "llp_governance": {
            "status": "HOLD",
            "probability_audit_result": "PROBABILITY_AUDIT_FAILURE",
            "event_decision": "NO_PICK_UNCALIBRATED",
            "event_mutex_status": "PASS",
        },
        "probability_audit_passed": True,
        "event_governor_complete": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }

    out = _reconcile_governance_stage_state(output)

    assert out["llp_probability_audit_result"] == "PROBABILITY_AUDIT_FAILURE"
    assert out["probability_audit_passed"] is False
    assert out["llp_event_decision"] == "NO_PICK_UNCALIBRATED"
    assert out["event_mutex_status"] == "PASS"
    assert out["event_governor_complete"] is True
    assert out["probability_publishable"] is False
    assert out["rank_eligible"] is False
    assert out["can_execute"] is False
