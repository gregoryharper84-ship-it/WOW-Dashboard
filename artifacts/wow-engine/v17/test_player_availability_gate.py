"""Pregame participation evidence must outrank model judgment.

The November 9 V17 Daily run scored players the club had already ruled out and
reported them as NO_LOW_PROBABILITY. These tests pin the corrected behavior:
availability is evaluated separately from roster identity, a ruled-out
participant keeps a typed participation terminal, and no calibration state or
threshold can promote the row back to rank eligibility.
"""
from types import SimpleNamespace

import pytest

import nfl_prop_auto_hydration as hydration
from prop_terminal_reducer_v2 import reduce_prop_terminal
from qualification_policy_v2 import classify_prop_probability
from v17.daily_terminal_reduction import classify_stage_status
from v17.player_availability_gate import (
    AVAILABILITY_AVAILABLE,
    AVAILABILITY_UNAVAILABLE,
    AVAILABILITY_UNVERIFIED,
    PLAYER_AVAILABILITY_UNVERIFIED,
    PLAYER_RULED_OUT,
    evaluate_player_availability,
    normalize_designation,
)


def _availability(status: str, designation: str | None, *, evidence_status: str = "RETRIEVED") -> dict:
    """Build complete availability evidence, including its provenance."""
    return {
        "status": status,
        "designation": designation,
        "source": "ESPN_NFL_ATHLETE_INJURY_REPORT",
        "captured_at": "2026-11-09T16:00:00+00:00",
        "evidence_status": evidence_status,
        "fresh": True,
        "athlete_id": "123",
        "event_id": "401-test",
        "team": "TEST",
    }


def test_official_out_designation_blocks_the_row():
    decision = evaluate_player_availability({"status": "ACTIVE_CURRENT_ESPN_ROSTER", "availability": _availability("OUT", "OUT")})
    assert decision.status == AVAILABILITY_UNAVAILABLE
    assert decision.available is False
    assert decision.blocker == PLAYER_RULED_OUT
    assert decision.designation == "OUT"


@pytest.mark.parametrize(
    "raw",
    ["Out", "INJURED RESERVE", "Injured-Reserve", "PUP", "Suspended", "Inactive", "NFI"],
)
def test_roster_removing_designations_are_all_recognized(raw):
    decision = evaluate_player_availability({"availability": _availability(raw, raw)})
    assert decision.status == AVAILABILITY_UNAVAILABLE
    assert decision.blocker == PLAYER_RULED_OUT


def test_active_roster_alone_is_not_participation_evidence():
    decision = evaluate_player_availability(
        {"status": "ACTIVE_CURRENT_ESPN_ROSTER", "identity_binding_status": "PASS"}
    )
    assert decision.status == AVAILABILITY_UNVERIFIED
    assert decision.available is None
    assert decision.blocker == PLAYER_AVAILABILITY_UNVERIFIED


def test_roster_status_is_never_read_as_a_designation():
    # ``status`` is roster identity on every hydration provider. Only explicit
    # availability keys may decide participation.
    decision = evaluate_player_availability({"status": "OUT", "availability": None})
    assert decision.status == AVAILABILITY_UNVERIFIED


def test_legacy_flat_designation_key_is_still_enforced():
    decision = evaluate_player_availability({"injury_status": "OUT"})
    assert decision.status == AVAILABILITY_UNAVAILABLE
    assert decision.designation == "OUT"


def test_failed_retrieval_is_unverified_not_available():
    decision = evaluate_player_availability(
        {"availability": _availability("UNVERIFIED", None, evidence_status="RETRIEVAL_FAILED")}
    )
    assert decision.status == AVAILABILITY_UNVERIFIED
    assert decision.blocker == PLAYER_AVAILABILITY_UNVERIFIED


def test_unrecognized_designation_is_not_clearance():
    decision = evaluate_player_availability({"availability": _availability("MAYBE", "MAYBE")})
    assert decision.status == AVAILABILITY_UNVERIFIED


def test_clean_report_and_playable_designations_clear_the_gate():
    clean = evaluate_player_availability(
        {"availability": _availability("AVAILABLE", None)}
    )
    assert clean.status == AVAILABILITY_AVAILABLE
    assert clean.blocker is None
    questionable = evaluate_player_availability({"availability": _availability("QUESTIONABLE", "QUESTIONABLE")})
    assert questionable.status == AVAILABILITY_AVAILABLE
    assert questionable.risk is True


def test_normalize_designation_handles_structured_designations():
    assert normalize_designation({"description": "Injured Reserve"}) == "INJURED_RESERVE"
    assert normalize_designation("out") == "OUT"


def test_participation_terminal_cannot_be_downgraded_to_a_model_rejection():
    result = reduce_prop_terminal(
        proposed_label="NO_LOW_PROBABILITY",
        blockers=["PLAYER_RULED_OUT"],
        model_evaluated=True,
    )
    assert result.terminal_label == PLAYER_RULED_OUT
    assert result.verdict_class == "PARTICIPATION_INVALIDATED"
    assert result.pick_rejected is True
    assert result.infrastructure_blocked is False


def test_participation_terminal_outranks_a_qualified_hold():
    result = reduce_prop_terminal(
        proposed_label="MODEL_QUALIFIED_HOLD",
        blockers=["PLAYER_RULED_OUT", "MARKET_DATA_UNAVAILABLE"],
        model_evaluated=True,
    )
    assert result.terminal_label == PLAYER_RULED_OUT


def test_high_probability_cannot_survive_a_ruled_out_participant():
    decision = classify_prop_probability(
        calibrated_probability=0.99,
        calibrated_lower_bound=0.95,
        calibrated_upper_bound=0.999,
        calibration_status="CALIBRATED_FORWARD",
        blockers=["PLAYER_RULED_OUT"],
        probability_publishable=True,
    )
    assert decision.terminal_label == PLAYER_RULED_OUT
    assert decision.model_qualified is False
    assert decision.rank_eligible is False
    assert decision.downstream_money_evaluation_allowed is False


def test_daily_purges_a_ruled_out_stage_instead_of_holding_it():
    assert classify_stage_status({"terminal_label": PLAYER_RULED_OUT}) == "PURGED"


def test_unverified_availability_blocks_rank_eligibility_at_any_probability():
    decision = classify_prop_probability(
        calibrated_probability=0.99,
        calibrated_lower_bound=0.95,
        calibrated_upper_bound=0.999,
        calibration_status="CALIBRATED_FORWARD",
        blockers=["PLAYER_AVAILABILITY_UNVERIFIED"],
        probability_publishable=True,
    )
    assert decision.terminal_label == "PLAYER_AVAILABILITY_UNVERIFIED"
    assert decision.confidence_tier == "PARTICIPATION_UNVERIFIED"
    assert decision.rank_eligible is False
    assert decision.model_supported is True
    assert decision.model_qualified is False
    assert decision.downstream_money_evaluation_allowed is False
    assert decision.final_approved_allowed is False


def test_unverified_availability_outranks_a_qualified_hold():
    result = reduce_prop_terminal(
        proposed_label="MODEL_QUALIFIED_HOLD",
        blockers=["PLAYER_AVAILABILITY_UNVERIFIED"],
        model_evaluated=True,
    )
    assert result.terminal_label == "PLAYER_AVAILABILITY_UNVERIFIED"
    assert result.verdict_class == "PARTICIPATION_UNVERIFIED"
    assert result.pick_rejected is False
    assert result.infrastructure_blocked is False


def test_unverified_availability_does_not_erase_a_model_rejection():
    result = reduce_prop_terminal(
        proposed_label="NO_LOW_PROBABILITY",
        blockers=["PLAYER_AVAILABILITY_UNVERIFIED"],
        model_evaluated=True,
    )
    assert result.terminal_label == "NO_LOW_PROBABILITY"


def test_unverified_availability_does_not_erase_a_capability_terminal():
    result = reduce_prop_terminal(
        proposed_label="MODEL_QUALIFIED_HOLD",
        blockers=["PLAYER_AVAILABILITY_UNVERIFIED", "MODEL_UNAVAILABLE"],
        model_evaluated=False,
    )
    assert result.terminal_label == "MODEL_UNAVAILABLE"


def test_daily_holds_an_unverified_row_out_of_the_verified_pool():
    assert classify_stage_status(
        {
            "terminal_label": "PLAYER_AVAILABILITY_UNVERIFIED",
            "probability_publishable": True,
            "rank_eligible": False,
        }
    ) == "HELD"


def _fake_get(payload=None, *, status_code: int = 200, raises: Exception | None = None):
    def http_get(*_args, **_kwargs):
        if raises is not None:
            raise raises
        return SimpleNamespace(status_code=status_code, json=lambda: payload)

    return http_get


def test_injury_report_designation_is_captured_into_role_evidence():
    payload = {
        "athlete": {"id": "123", "displayName": "Test Player"},
        "injuries": [
            {"status": "Questionable", "date": "2026-11-05"},
            {"status": "Out", "date": "2026-11-07"},
        ],
    }
    from datetime import datetime, timezone

    availability = hydration._athlete_availability(
        "123",
        http_get=_fake_get(payload),
        captured=datetime(2026, 11, 9, 16, 0, tzinfo=timezone.utc),
    )
    assert availability["designation"] == "OUT"
    assert availability["evidence_status"] == "RETRIEVED"
    assert availability["source"] == "ESPN_NFL_ATHLETE_INJURY_REPORT"
    assert evaluate_player_availability({"availability": availability}).status == AVAILABILITY_UNAVAILABLE


def test_athlete_profile_without_report_cannot_clear_even_after_event_binding():
    from datetime import datetime, timezone

    availability = hydration._athlete_availability(
        "123",
        http_get=_fake_get({"athlete": {"id": "123"}}),
        captured=datetime(2026, 11, 9, 16, 0, tzinfo=timezone.utc),
    )
    assert availability["designation"] is None
    # The athlete record alone does not name an event or team. Binding it to the
    # resolved target is what completes the provenance contract.
    assert evaluate_player_availability({"availability": availability}).status == AVAILABILITY_UNVERIFIED
    bound = hydration.bind_availability_to_event(availability, team="TEST", event_id="401-test")
    assert evaluate_player_availability({"availability": bound}).status == AVAILABILITY_UNVERIFIED
    assert bound["evidence_status"] == "OFFICIAL_REPORT_COMPLETENESS_UNVERIFIED"
    assert bound["report_complete"] is False


def test_injury_report_retrieval_failure_is_unverified_evidence():
    from datetime import datetime, timezone

    availability = hydration._athlete_availability(
        "123",
        http_get=_fake_get(raises=RuntimeError("HTTP_503")),
        captured=datetime(2026, 11, 9, 16, 0, tzinfo=timezone.utc),
    )
    assert availability["evidence_status"] == "RETRIEVAL_FAILED"
    assert evaluate_player_availability({"availability": availability}).status == AVAILABILITY_UNVERIFIED
