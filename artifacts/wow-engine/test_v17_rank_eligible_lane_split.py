"""Regression: objective-card admission cannot rewrite fitted sporting rank.

This module uses direct row-level functions to avoid introducing new model
or provider fixtures. Card admission remains non-executable.
"""

from types import SimpleNamespace

import team_event_request_runtime as runtime


def _row(objective_lane="OUTRIGHT_WIN_PROBABILITY", price_required=False):
    return SimpleNamespace(
        research_run_id="lane-split-replay",
        event_key="NFL:test-game",
        objective_lane=objective_lane,
        price_required_for_objective=price_required,
        home_team="Home Team",
        away_team="Away Team",
    )


def _outcome(**updates):
    value = {
        "terminal_status": "COMPLETED",
        "probability_publishable": True,
        "probability_rank_eligible": True,
        "objective_rank_eligible": True,
        "rank_eligible": True,
        "selected_team": "Home Team",
        "event_prediction_id": "00000000-0000-0000-0000-000000000099",
        "can_execute": False,
    }
    value.update(updates)
    return value


def test_market_edge_cannot_clobber_a_completed_sporting_probability():
    row = _row("MARKET_EDGE", True)
    value = _outcome(objective_rank_eligible=False)
    runtime._apply_card_admission(row, value)
    assert value["rank_eligible"] is True
    assert value["probability_rank_eligible"] is True
    assert value["objective_rank_eligible"] is False
    assert value["probability_publishable"] is True
    assert value["card_admission_eligible"] is False
    assert "CARD_ADMISSION:RANK_INELIGIBLE" in value["card_admission_blockers"]
    assert value["can_execute"] is False
    assert value["card_admission_receipt"]["rank_eligible"] is True
    assert value["card_admission_receipt"]["objective_rank_eligible"] is False


def test_both_lanes_and_identified_prediction_required_for_card():
    row = _row()
    value = _outcome()
    runtime._apply_card_admission(row, value)
    assert value["rank_eligible"] is True
    assert value["card_admission_eligible"] is True
    assert value["card_admission_blockers"] == []
    assert value["can_execute"] is False
    assert value["card_admission_receipt"]["can_execute"] is False


def test_missing_sporting_flag_fails_closed_even_if_objective_flag_true():
    row = _row()
    value = _outcome(probability_rank_eligible=None, objective_rank_eligible=True)
    runtime._apply_card_admission(row, value)
    assert value["rank_eligible"] is False
    assert value["card_admission_eligible"] is False
    assert "CARD_ADMISSION:PROBABILITY_RANK_INELIGIBLE" in value["card_admission_blockers"]
    assert value["can_execute"] is False


def test_held_scoring_row_cannot_be_admitted_from_both_positive_flags():
    row = _row()
    value = _outcome(terminal_status="HELD", probability_publishable=False)
    runtime._apply_card_admission(row, value)
    assert value["card_admission_eligible"] is False
    assert "CARD_ADMISSION:TERMINAL_NOT_COMPLETED" in value["card_admission_blockers"]
    assert value["can_execute"] is False


def test_missing_nfl_fitted_rank_flag_does_not_default_to_eligible():
    row = _row()
    scored = {
        "terminal_label": "FINAL_APPROVED",
        "probability_publishable": True,
        "selected_participant": "Home Team",
        "calibrated_selection_probability": 0.63,
        "rank_calibrated_lower_bound": 0.55,
        "calibrated_home_upper_bound": 0.70,
        "event_prediction_id": "00000000-0000-0000-0000-000000000099",
        "can_execute": False,
    }
    result = runtime._completed_nfl(row, scored)
    assert result["terminal_status"] == "COMPLETED"
    assert result["probability_rank_eligible"] is False
    assert result["objective_rank_eligible"] is False
    runtime._apply_card_admission(row, result)
    assert result["rank_eligible"] is False
    assert result["card_admission_eligible"] is False
    assert result["can_execute"] is False


def test_nfl_explicit_eligible_flag_does_not_authorize_market_card():
    row = _row("MARKET_EDGE", True)
    scored = {
        "terminal_label": "FINAL_APPROVED",
        "probability_publishable": True,
        "rank_eligible": True,
        "selected_participant": "Home Team",
        "calibrated_selection_probability": 0.63,
        "rank_calibrated_lower_bound": 0.55,
        "calibrated_home_upper_bound": 0.70,
        "event_prediction_id": "00000000-0000-0000-0000-000000000099",
        "can_execute": False,
    }
    result = runtime._completed_nfl(row, scored)
    runtime._apply_card_admission(row, result)
    assert result["probability_rank_eligible"] is True
    assert result["rank_eligible"] is True
    assert result["objective_rank_eligible"] is False
    assert result["card_admission_eligible"] is False
    assert result["can_execute"] is False
