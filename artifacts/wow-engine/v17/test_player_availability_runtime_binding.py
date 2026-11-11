"""The availability gate must bind to the real request boundary, not only exist.

These tests exercise ``pick_request_runtime_core`` itself so a ruled-out
participant is stopped during pre-score validation and keeps a participation
terminal instead of an acquisition or model terminal.
"""
from datetime import datetime, timedelta, timezone

import pytest

import pick_request_runtime_core as runtime
from v17.player_availability_gate import PLAYER_RULED_OUT

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _evidence(*, availability: dict | None) -> dict:
    captured = datetime.now(timezone.utc) - timedelta(minutes=5)
    role_status = {
        "status": "ACTIVE_CURRENT_ESPN_ROSTER",
        "identity_binding_status": "PASS",
        "canonical_event_id": "WOW:NFL:EVENT-123",
        "verified_canonical_event_id": "WOW:NFL:EVENT-123",
    }
    if availability is not None:
        role_status["availability"] = availability
    return {
        "captured_at": captured.isoformat(),
        "game_log": [17.0 + i for i in range(10)],
        "box_score_log": [{"game": i + 1} for i in range(10)],
        "role_status": role_status,
        "role_timestamp": captured.isoformat(),
        "opportunity_ledger": {"status": "PASS"},
        "source_timestamps": {"TEST_SOURCE": captured.isoformat()},
        "evidence_version": "PROP_EVIDENCE_V1",
        "rate_provenance": "TEST",
    }


def _row(evidence: dict):
    return runtime.PickRequestRow(
        event_id="WOW:NFL:EVENT-123",
        event_start_time=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        sport="NFL",
        player="Jauan Jennings",
        stat_type="RECEIVING_YARDS",
        line=17.5,
        direction="LESS",
        evidence=runtime.RawPropEvidence.model_validate(evidence),
    )


def test_ruled_out_player_is_rejected_before_scoring():
    designation = {
        "status": "OUT",
        "designation": "OUT",
        "evidence_status": "RETRIEVED",
        "source": "ESPN_NFL_ATHLETE_INJURY_REPORT",
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    with pytest.raises(ValueError, match=f"{PLAYER_RULED_OUT}:OUT"):
        runtime._validate_evidence(_row(_evidence(availability=designation)), "RECEIVING_YARDS")


def test_available_player_still_validates():
    designation = {
        "status": "AVAILABLE",
        "designation": None,
        "evidence_status": "RETRIEVED",
        "source": "ESPN_NFL_ATHLETE_INJURY_REPORT",
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    normalized = runtime._validate_evidence(_row(_evidence(availability=designation)), "RECEIVING_YARDS")
    assert normalized["player"] == "Jauan Jennings"


def test_validation_reports_unverified_availability_instead_of_clearing_it():
    normalized = runtime._validate_evidence(_row(_evidence(availability=None)), "RECEIVING_YARDS")
    assert normalized["availability_status"] == "UNVERIFIED"
    assert normalized["availability"]["blocker"] == "PLAYER_AVAILABILITY_UNVERIFIED"


def _qualified_scored_payload() -> dict:
    return {
        "ok": True,
        "probability_publishable": True,
        "prediction": {
            "calibrated_probability": 0.70,
            "calibrated_probability_lower_bound": 0.66,
            "calibrated_probability_upper_bound": 0.74,
            "calibration_status": "CALIBRATED_FORWARD",
        },
        "probability_qualification": {
            "terminal_label": "MODEL_QUALIFIED_HOLD",
            "confidence_tier": "HIGH",
            "rank_eligible": True,
            "model_supported": True,
            "downstream_money_evaluation_allowed": True,
            "blockers": [],
        },
        "objective_lanes": {"MARKET": {"status": "PASS"}, "MONEY": {"status": "PASS"}},
    }


def test_unverified_availability_forces_a_held_non_rankable_outcome():
    out = runtime._completed_scored_outcome(
        row_key="r1",
        scored=_qualified_scored_payload(),
        snapshot_id="snapshot",
        fingerprint="fingerprint",
        acquisition={"mode": "TEST"},
        premodel_blockers=["PLAYER_AVAILABILITY_UNVERIFIED"],
    )
    assert out["terminal_label"] == "PLAYER_AVAILABILITY_UNVERIFIED"
    assert out["terminal_status"] == "HELD"
    assert out["rank_eligible"] is False
    assert out["downstream_money_evaluation_allowed"] is False
    assert out["pick_rejected"] is False
    assert out["code"] == "PLAYER_AVAILABILITY_UNVERIFIED"


def test_same_scored_payload_completes_without_the_availability_blocker():
    out = runtime._completed_scored_outcome(
        row_key="r1",
        scored=_qualified_scored_payload(),
        snapshot_id="snapshot",
        fingerprint="fingerprint",
        acquisition={"mode": "TEST"},
    )
    assert out["terminal_label"] == "MODEL_QUALIFIED_HOLD"
    assert out["terminal_status"] == "COMPLETED"
    assert out["rank_eligible"] is True


def test_participation_terminal_is_rejected_not_held():
    outcome = runtime._terminal(
        "row-1",
        "REJECTED",
        PLAYER_RULED_OUT,
        detail={
            "blocker": PLAYER_RULED_OUT,
            "terminal_label": PLAYER_RULED_OUT,
            "designation": "OUT",
            "model_evaluated": False,
            "specialist_invoked": False,
        },
    )
    assert outcome["terminal_label"] == PLAYER_RULED_OUT
    assert outcome["terminal_status"] == "REJECTED"
    assert outcome["verdict_class"] == "PARTICIPATION_INVALIDATED"
    assert outcome["model_evaluated"] is False
    assert outcome["probability_publishable"] is False
    assert outcome["can_execute"] is False


def test_facade_reports_no_scorer_was_attempted_for_a_ruled_out_row():
    import pick_request_runtime as facade

    outcome = facade._terminal(
        "row-1",
        "REJECTED",
        PLAYER_RULED_OUT,
        detail={
            "blocker": PLAYER_RULED_OUT,
            "terminal_label": PLAYER_RULED_OUT,
            "designation": "OUT",
            "model_evaluated": False,
            "specialist_invoked": False,
        },
    )
    assert outcome["terminal_label"] == PLAYER_RULED_OUT
    assert outcome["terminal_status"] == "REJECTED"
    assert outcome["specialist_scoring_attempted"] is False
    assert outcome["scoring_attempted"] is False
