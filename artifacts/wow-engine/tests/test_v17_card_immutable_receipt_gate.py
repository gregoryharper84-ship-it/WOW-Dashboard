from copy import deepcopy

from card_immutable_receipt_gate import (
    evaluate_card_leg_admission,
    gate_proposed_card,
)


def leg(line=3.5, direction="MORE"):
    return {
        "event_id": "MLB:823499",
        "participant": "Max Fried",
        "market": "PITCHER_STRIKEOUTS",
        "period": "FULL_GAME",
        "exact_line": line,
        "direction": direction,
        "settlement_basis": "PRIZEPICKS_STANDARD",
    }


def receipt(line=3.5, direction="MORE", **overrides):
    row = {
        "governed_prediction_id": "pred-123",
        "governed_prediction_table": "wow_predictions",
        "event_id": "MLB:823499",
        "participant": "Max Fried",
        "market": "PITCHER_STRIKEOUTS",
        "period": "FULL_GAME",
        "exact_line": line,
        "direction": direction,
        "settlement_basis": "PRIZEPICKS_STANDARD",
        "is_immutable_pregame": True,
        "probability_publishable": True,
        "lane_card_eligible": True,
        "terminal_blocked": False,
        "terminal_label": "MODEL_QUALIFIED",
        "calibrated_probability": 0.64,
        "calibrated_lower_bound": 0.59,
        "can_execute": False,
    }
    row.update(overrides)
    return row


def test_exact_governed_receipt_passes():
    result = evaluate_card_leg_admission(leg(), receipt())
    assert result.eligible is True
    assert result.blockers == ()
    assert result.can_execute is False


def test_adjacent_line_cannot_inherit_receipt():
    result = evaluate_card_leg_admission(leg(line=3.5), receipt(line=4.5))
    assert result.eligible is False
    assert "CARD_EXACT_IDENTITY_MISMATCH:exact_line" in result.blockers


def test_direction_mismatch_is_rejected():
    result = evaluate_card_leg_admission(
        leg(direction="MORE"), receipt(direction="LESS")
    )
    assert result.eligible is False
    assert "CARD_EXACT_IDENTITY_MISMATCH:direction_or_side" in result.blockers


def test_research_only_leg_without_receipt_stays_ineligible_even_if_it_won():
    research_leg = {**leg(), "settled_result": "WIN"}
    result = evaluate_card_leg_admission(research_leg, None)
    assert result.eligible is False
    assert result.blockers == ("CARD_NO_EXACT_GOVERNED_PREGAME_RECEIPT",)


def test_blocked_but_winning_row_cannot_be_promoted_postgame():
    winning_but_blocked = receipt(
        lane_card_eligible=False,
        terminal_label="MODEL_INPUTS_INSUFFICIENT",
    )
    result = evaluate_card_leg_admission(
        {**leg(), "settled_result": "WIN"},
        winning_but_blocked,
    )
    assert result.eligible is False
    assert "CARD_UPSTREAM_ELIGIBILITY_NOT_VERIFIED" in result.blockers


def test_invalid_probability_package_is_rejected():
    result = evaluate_card_leg_admission(
        leg(),
        receipt(calibrated_probability=0.55, calibrated_lower_bound=0.60),
    )
    assert result.eligible is False
    assert "CARD_GOVERNED_PROBABILITY_PACKAGE_INVALID" in result.blockers


def test_missing_explicit_can_execute_false_fails_closed():
    row = receipt()
    row.pop("can_execute")
    result = evaluate_card_leg_admission(leg(), row)
    assert result.eligible is False
    assert "CARD_CAN_EXECUTE_INVARIANT_UNVERIFIED" in result.blockers


def test_ineligible_leg_forces_shrink_not_filler():
    proposed = [
        (leg(), receipt()),
        (
            {
                **leg(),
                "participant": "Ryan Feltner",
                "exact_line": 4.5,
                "direction": "LESS",
            },
            None,
        ),
        ({**leg(), "participant": "Hagen Smith", "exact_line": 2.5}, None),
    ]
    result = gate_proposed_card(
        proposed,
        requested_leg_count=3,
        minimum_leg_count=1,
    )
    assert result.admitted_indices == (0,)
    assert result.admitted_leg_count == 1
    assert result.shrunk is True
    assert result.portfolio_status == "SHRUNK"
    assert result.can_execute is False


def test_shrink_below_platform_minimum_holds_card():
    proposed = [
        (leg(), receipt()),
        ({**leg(), "participant": "Research Only"}, None),
    ]
    result = gate_proposed_card(
        proposed,
        requested_leg_count=2,
        minimum_leg_count=2,
    )
    assert result.portfolio_status == "HELD"
    assert "INSUFFICIENT_LEGS_AFTER_MANDATORY_SHRINK" in result.blockers


def test_gate_never_mutates_probability_fields():
    row = receipt(calibrated_probability=0.71, calibrated_lower_bound=0.66)
    before = deepcopy(row)
    result = gate_proposed_card([(leg(), row)], requested_leg_count=1)
    assert result.portfolio_status == "PASS"
    assert row == before
    assert row["calibrated_probability"] == 0.71
    assert row["calibrated_lower_bound"] == 0.66


def test_team_event_without_scalar_line_can_pass_when_exact_identity_matches():
    team_leg = {
        "official_event_id": "NFL:abc",
        "participant": "LA Rams",
        "market_family": "OUTRIGHT_WINNER",
        "settlement_basis": "FULL_GAME",
    }
    team_receipt = {
        "governed_prediction_id": "event-pred-1",
        "governed_prediction_table": "wow_event_predictions",
        **team_leg,
        "is_immutable_pregame": True,
        "probability_publishable": True,
        "lane_card_eligible": True,
        "terminal_blocked": False,
        "terminal_label": "MODEL_QUALIFIED",
        "calibrated_probability": 0.62,
        "calibrated_lower_bound": 0.57,
        "can_execute": False,
    }
    result = evaluate_card_leg_admission(team_leg, team_receipt)
    assert result.eligible is True
