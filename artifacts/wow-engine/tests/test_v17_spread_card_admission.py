from __future__ import annotations

from v17.spread_card_admission import (
    GOVERNED_SPREAD,
    RESEARCH_ONLY_SPREAD,
    spread_card_admission,
)


def _certified_receipt(**overrides):
    row = {
        "status": "COMPLETED",
        "evaluation_state": "CERTIFIED_PRODUCTION",
        "sport": "NFL",
        "event_id": "2026_04_BAL_DAL",
        "exact_line_side": "HOME",
        "exact_signed_spread": -3.5,
        "settlement_basis": "FULL_GAME_INCLUDING_OVERTIME",
        "controlling_spread_specialist": "NFL_SPREAD_MARGIN_DISTRIBUTION_V17",
        "model_artifact_version": "NFL_SPREAD_MARGIN_RIDGE_EMPIRICAL_V1",
        "model_timestamp": "2026-09-27T18:00:00+00:00",
        "p_cover": 0.56,
        "p_push": 0.0,
        "p_not_cover": 0.44,
        "probability_publishable": True,
        "rank_eligible": True,
        "can_execute": False,
    }
    row.update(overrides)
    return row


def test_research_only_sf_minus_7_5_cannot_enter_governed_card():
    row = _certified_receipt(
        event_id="SF_ARI_2026_09_27",
        exact_signed_spread=-7.5,
        evaluation_state="FORWARD_SHADOW_UNCERTIFIED",
        probability_publishable=False,
        rank_eligible=False,
    )
    result = spread_card_admission(
        row,
        requested_event_id="SF_ARI_2026_09_27",
        requested_side="HOME",
        requested_signed_spread=-7.5,
    )
    assert result["card_admission_eligible"] is False
    assert result["classification"] == RESEARCH_ONLY_SPREAD
    assert "SPREAD_ADMISSION:PROBABILITY_NOT_PUBLISHABLE" in result["blockers"]
    assert "SPREAD_ADMISSION:RANK_INELIGIBLE" in result["blockers"]
    assert "SPREAD_ADMISSION:FORWARD_SHADOW_UNCERTIFIED" in result["blockers"]
    assert result["can_execute"] is False


def test_research_only_lar_minus_1_5_missing_governed_receipt_fails_closed():
    row = {
        "sport": "NFL",
        "event_id": "LAR_DEN_2026_09_27",
        "home_spread": -1.5,
        "p_cover": 0.584,
        "p_push": 0.0,
        "p_not_cover": 0.416,
        "probability_publishable": False,
        "rank_eligible": False,
        "evaluation_state": "RESEARCH_ONLY",
        "can_execute": False,
    }
    result = spread_card_admission(
        row,
        requested_event_id="LAR_DEN_2026_09_27",
        requested_side="HOME",
        requested_signed_spread=-1.5,
    )
    assert result["card_admission_eligible"] is False
    assert result["classification"] == RESEARCH_ONLY_SPREAD
    assert "SPREAD_ADMISSION:SETTLEMENT_BASIS_REQUIRED" in result["blockers"]
    assert "SPREAD_ADMISSION:CONTROLLING_SPREAD_SPECIALIST_REQUIRED" in result["blockers"]
    assert "SPREAD_ADMISSION:MODEL_ARTIFACT_REQUIRED" in result["blockers"]
    assert "SPREAD_ADMISSION:MODEL_TIMESTAMP_REQUIRED" in result["blockers"]


def test_moneyline_winner_package_cannot_substitute_for_spread_receipt():
    row = {
        "sport": "NFL",
        "event_id": "SF_ARI_2026_09_27",
        "model_probability": 0.72,
        "calibrated_probability": 0.69,
        "calibrated_lower_bound": 0.63,
        "probability_publishable": True,
        "rank_eligible": True,
        "model_timestamp": "2026-09-27T18:00:00+00:00",
        "can_execute": False,
    }
    result = spread_card_admission(
        row,
        requested_event_id="SF_ARI_2026_09_27",
        requested_side="HOME",
        requested_signed_spread=-7.5,
    )
    assert result["card_admission_eligible"] is False
    assert "SPREAD_ADMISSION:RECEIPT_SIDE_REQUIRED" in result["blockers"]
    assert "SPREAD_ADMISSION:EXACT_SIGNED_SPREAD_REQUIRED" in result["blockers"]
    assert "SPREAD_ADMISSION:PROBABILITY_DISTRIBUTION_REQUIRED" in result["blockers"]
    assert result["moneyline_probability_substitution_allowed"] is False


def test_adjacent_line_is_not_the_same_spread_thesis():
    result = spread_card_admission(
        _certified_receipt(exact_signed_spread=-3.5),
        requested_event_id="2026_04_BAL_DAL",
        requested_side="HOME",
        requested_signed_spread=-4.5,
    )
    assert result["card_admission_eligible"] is False
    assert result["blockers"] == ["SPREAD_ADMISSION:EXACT_LINE_MISMATCH"]


def test_valid_exact_certified_spread_receipt_can_cross_card_boundary():
    result = spread_card_admission(
        _certified_receipt(),
        requested_event_id="2026_04_BAL_DAL",
        requested_side="HOME",
        requested_signed_spread=-3.5,
    )
    assert result["status"] == "PASS"
    assert result["classification"] == GOVERNED_SPREAD
    assert result["card_admission_eligible"] is True
    assert result["blockers"] == []
    assert result["sporting_probability_mutated"] is False
    assert result["can_execute"] is False


def test_started_or_final_row_fails_closed_even_if_other_fields_are_valid():
    for state in ("LIVE", "FINAL"):
        result = spread_card_admission(
            _certified_receipt(event_state=state),
            requested_event_id="2026_04_BAL_DAL",
            requested_side="HOME",
            requested_signed_spread=-3.5,
        )
        assert result["card_admission_eligible"] is False
        assert "SPREAD_ADMISSION:EVENT_ALREADY_STARTED_OR_FINAL" in result["blockers"]


def test_can_execute_must_remain_false():
    result = spread_card_admission(
        _certified_receipt(can_execute=True),
        requested_event_id="2026_04_BAL_DAL",
        requested_side="HOME",
        requested_signed_spread=-3.5,
    )
    assert result["card_admission_eligible"] is False
    assert "SPREAD_ADMISSION:CAN_EXECUTE_INVARIANT_VIOLATION" in result["blockers"]
    assert result["can_execute"] is False
