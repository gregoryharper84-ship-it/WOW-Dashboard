from __future__ import annotations

import pick_request_runtime as prop_runtime
import team_event_request_runtime as team_runtime


def _prop_leg(*, line: float = 12.5, direction: str = "MORE") -> dict:
    return {
        "row_id": "kirby-12.5-more",
        "event_id": "MLB:SEA:LAA:2026-09-16",
        "player": "George Kirby",
        "prop_type": "FIRST_INNING_PITCHES_THROWN",
        "direction": direction,
        "line": line,
        "model_probability": 0.63,
        "calibrated_probability": 0.61,
        "calibrated_lower_bound": 0.56,
    }


def _prop_outcome(
    *,
    rank_eligible: bool = True,
    probability_publishable: bool = True,
    downstream_money_evaluation_allowed: bool | None = None,
) -> dict:
    outcome = {
        "row_key": "kirby-12.5-more",
        "terminal_status": "COMPLETED",
        "terminal_label": "MODEL_QUALIFIED_HOLD",
        "rank_eligible": rank_eligible,
        "probability_publishable": probability_publishable,
        "pick_rejected": False,
        "result": {
            "prediction": {
                "prediction_id": "00000000-0000-4000-8000-000000000125",
                "calibrated_probability": 0.61,
                "calibrated_probability_lower_bound": 0.56,
            }
        },
        "can_execute": False,
    }
    if downstream_money_evaluation_allowed is not None:
        outcome["downstream_money_evaluation_allowed"] = downstream_money_evaluation_allowed
    return outcome


def test_prop_rank_ineligible_probability_is_preserved_but_card_admission_is_blocked():
    outcome = _prop_outcome(rank_eligible=False, probability_publishable=True)
    before_probability = outcome["result"]["prediction"]["calibrated_probability"]

    prop_runtime._apply_portfolio_governance("retro-2026-09-16", [(_prop_leg(), outcome)])

    assert outcome["card_admission_eligible"] is False
    assert "CARD_ADMISSION:RANK_INELIGIBLE" in outcome["card_admission_blockers"]
    assert outcome["downstream_portfolio_evaluation_allowed"] is False
    assert outcome["result"]["prediction"]["calibrated_probability"] == before_probability
    assert outcome["can_execute"] is False


def test_prop_card_receipt_binds_exact_line_direction_and_prediction_id():
    outcome = _prop_outcome(rank_eligible=True, probability_publishable=True)
    prop_runtime._apply_portfolio_governance("retro-2026-09-16", [(_prop_leg(line=12.5, direction="MORE"), outcome)])

    receipt = outcome["card_admission_receipt"]
    assert outcome["card_admission_eligible"] is True
    assert receipt["prediction_id"] == "00000000-0000-4000-8000-000000000125"
    assert receipt["event_id"] == "MLB:SEA:LAA:2026-09-16"
    assert receipt["participant"] == "George Kirby"
    assert receipt["market_stat"] == "FIRST_INNING_PITCHES_THROWN"
    assert receipt["exact_line"] == 12.5
    assert receipt["direction"] == "MORE"
    assert receipt["money_evaluation_allowed"] is False
    assert receipt["can_execute"] is False


def test_prop_publishability_hold_blocks_card_even_when_rank_flag_is_true():
    outcome = _prop_outcome(rank_eligible=True, probability_publishable=False)
    prop_runtime._apply_portfolio_governance("retro-2026-09-16", [(_prop_leg(), outcome)])

    assert outcome["card_admission_eligible"] is False
    assert "CARD_ADMISSION:PROBABILITY_NOT_PUBLISHABLE" in outcome["card_admission_blockers"]
    assert outcome["downstream_portfolio_evaluation_allowed"] is False


def test_prop_money_hold_blocks_card_without_erasing_probability_or_portfolio_analysis():
    outcome = _prop_outcome(
        rank_eligible=True,
        probability_publishable=True,
        downstream_money_evaluation_allowed=False,
    )
    before_probability = outcome["result"]["prediction"]["calibrated_probability"]

    prop_runtime._apply_portfolio_governance("mlb-1ip-payout-hold", [(_prop_leg(), outcome)])

    assert outcome["rank_eligible"] is True
    assert outcome["probability_publishable"] is True
    assert outcome["card_admission_eligible"] is False
    assert "CARD_ADMISSION:MONEY_EVALUATION_HELD" in outcome["card_admission_blockers"]
    assert outcome["downstream_money_evaluation_allowed"] is False
    assert outcome["downstream_portfolio_evaluation_allowed"] is True
    assert outcome["portfolio_governance"]["blockers"] == []
    assert outcome["result"]["prediction"]["calibrated_probability"] == before_probability
    assert outcome["card_admission_receipt"]["money_evaluation_allowed"] is False
    assert outcome["card_admission_receipt"]["portfolio_eligible"] is True
    assert outcome["can_execute"] is False


def _team_row() -> team_runtime.TeamEventRequestRow:
    return team_runtime.TeamEventRequestRow(
        research_run_id="retro-2026-09-16",
        objective_lane="OUTRIGHT_WIN_PROBABILITY",
        sport="MLB",
        league="MLB",
        event_key="MLB:ATH:TB:2026-09-16",
        event_state="PREGAME",
        event_date="2026-09-16",
        timezone="America/Chicago",
        price_required_for_objective=False,
    )


def _team_event() -> dict:
    return {
        "official_event_id": "MLB:ATH:TB:2026-09-16",
        "home_team": "Rays",
        "away_team": "Athletics",
    }


def _team_scored(*, rank_eligible: bool, probability_publishable: bool = True) -> dict:
    return {
        "calibrated_home_probability": 0.5586,
        "calibrated_away_probability": 0.4414,
        "calibrated_home_lower_bound": 0.5053,
        "calibrated_away_lower_bound": 0.3881,
        "calibrated_home_upper_bound": 0.6100,
        "calibrated_away_upper_bound": 0.4940,
        "probability_publishable": probability_publishable,
        "rank_eligible": rank_eligible,
        "event_prediction_id": "00000000-0000-4000-8000-000000000404",
        "terminal_label": "MODEL_QUALIFIED_HOLD",
        "blockers": [],
        "can_execute": False,
    }


def test_team_event_rank_ineligible_row_cannot_enter_card_pool():
    outcome = team_runtime._completed(_team_row(), _team_event(), _team_scored(rank_eligible=False))

    assert outcome["terminal_status"] == "COMPLETED"
    assert outcome["calibrated_probability"] == 0.5586
    assert outcome["probability_publishable"] is True
    assert outcome["rank_eligible"] is False
    assert outcome["card_admission_eligible"] is False
    assert "CARD_ADMISSION:RANK_INELIGIBLE" in outcome["card_admission_blockers"]
    assert outcome["card_admission_receipt"]["selection"] == "Rays"
    assert outcome["can_execute"] is False


def test_team_event_rank_eligible_publishable_row_is_bound_to_event_prediction():
    outcome = team_runtime._completed(_team_row(), _team_event(), _team_scored(rank_eligible=True))

    assert outcome["card_admission_eligible"] is True
    assert outcome["card_admission_blockers"] == []
    receipt = outcome["card_admission_receipt"]
    assert receipt["event_prediction_id"] == "00000000-0000-4000-8000-000000000404"
    assert receipt["official_event_id"] == "MLB:ATH:TB:2026-09-16"
    assert receipt["market_family"] == "OUTRIGHT_WINNER"
    assert receipt["rank_eligible"] is True
    assert receipt["probability_publishable"] is True
    assert receipt["can_execute"] is False


def test_team_event_held_row_is_explicitly_card_ineligible():
    outcome = team_runtime._held(_team_row(), "MODEL_INPUTS_INSUFFICIENT", "STARTER_UNRESOLVED")

    assert outcome["rank_eligible"] is False
    assert outcome["probability_publishable"] is False
    assert outcome["card_admission_eligible"] is False
    assert outcome["card_admission_receipt"] is None
    assert outcome["can_execute"] is False

# CI context refresh: preserve exact card-admission regression coverage.