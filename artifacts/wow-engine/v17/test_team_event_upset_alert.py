from __future__ import annotations

import pytest

from v17.team_event_upset_alert import (
    AUTOMATIC_PICK_PROMOTION,
    CAN_EXECUTE,
    MUTATES_ADMISSION,
    MUTATES_CASH_GATE,
    MUTATES_SPORTING_PROBABILITY,
    evaluate_favorite_upset_alert,
)


def outcome(label: str, p: float, lo: float, hi: float):
    return {
        "label": label,
        "calibrated_probability": p,
        "calibrated_lower_bound": lo,
        "calibrated_upper_bound": hi,
    }


def test_model_flip_flags_market_favorite_high_alert():
    result = evaluate_favorite_upset_alert(
        sport="MLB",
        market_favorite="SEA",
        market_favorite_verified=True,
        governed_outcomes=(
            outcome("SEA", 0.46, 0.41, 0.51),
            outcome("OAK", 0.54, 0.49, 0.59),
        ),
        favorite_failure_path_probability_if_modeled=0.54,
        largest_favorite_loss_path="starter collapse + bullpen handoff",
        underdog_upset_path="early run cluster",
    )
    assert result.alert is True
    assert result.severity == "HIGH"
    assert result.status == "UPSET_ALERT_MODEL_FLIP"
    assert result.upset_candidate == "OAK"
    assert result.upset_candidate_probability == pytest.approx(0.54)
    assert "GOVERNED_MODEL_PREFERS_NON_FAVORITE_OUTCOME" in result.reason_codes


def test_bound_overlap_flags_elevated_without_changing_model_pick():
    result = evaluate_favorite_upset_alert(
        sport="NFL",
        market_favorite="DAL",
        market_favorite_verified=True,
        governed_outcomes=(
            outcome("DAL", 0.57, 0.49, 0.64),
            outcome("NYG", 0.43, 0.36, 0.51),
        ),
    )
    assert result.alert is True
    assert result.severity == "ELEVATED"
    assert result.status == "UPSET_ALERT_UNCERTAINTY_OVERLAP"
    assert result.market_favorite == "DAL"
    assert result.upset_candidate == "NYG"


def test_clear_favorite_has_no_upset_alert():
    result = evaluate_favorite_upset_alert(
        sport="NBA",
        market_favorite="BOS",
        market_favorite_verified=True,
        governed_outcomes=(
            outcome("BOS", 0.66, 0.59, 0.72),
            outcome("CHI", 0.34, 0.28, 0.41),
        ),
    )
    assert result.alert is False
    assert result.severity == "NONE"
    assert result.status == "FAVORITE_MODEL_CLEAR"


def test_three_way_market_does_not_use_universal_coinflip_threshold():
    result = evaluate_favorite_upset_alert(
        sport="SOCCER",
        market_favorite="HOME",
        market_favorite_verified=True,
        governed_outcomes=(
            outcome("HOME", 0.44, 0.39, 0.49),
            outcome("DRAW", 0.31, 0.27, 0.38),
            outcome("AWAY", 0.25, 0.21, 0.34),
        ),
    )
    assert result.alert is False
    assert result.status == "FAVORITE_MODEL_CLEAR"
    assert result.favorite_probability < 0.50


def test_three_way_model_flip_can_identify_draw_as_favorite_threat():
    result = evaluate_favorite_upset_alert(
        sport="SOCCER",
        market_favorite="HOME",
        market_favorite_verified=True,
        governed_outcomes=(
            outcome("HOME", 0.36, 0.31, 0.41),
            outcome("DRAW", 0.39, 0.34, 0.44),
            outcome("AWAY", 0.25, 0.20, 0.30),
        ),
    )
    assert result.alert is True
    assert result.status == "UPSET_ALERT_MODEL_FLIP"
    assert result.upset_candidate == "DRAW"


def test_unverified_market_favorite_does_not_manufacture_alert():
    result = evaluate_favorite_upset_alert(
        sport="TENNIS",
        market_favorite="Player A",
        market_favorite_verified=False,
        governed_outcomes=(
            outcome("Player A", 0.47, 0.41, 0.53),
            outcome("Player B", 0.53, 0.47, 0.59),
        ),
    )
    assert result.alert is False
    assert result.status == "UPSET_ALERT_UNAVAILABLE"
    assert result.reason_codes == ("MARKET_FAVORITE_CLASSIFICATION_UNVERIFIED",)


def test_alert_layer_never_mutates_probability_or_admission():
    assert MUTATES_SPORTING_PROBABILITY is False
    assert MUTATES_ADMISSION is False
    assert MUTATES_CASH_GATE is False
    assert AUTOMATIC_PICK_PROMOTION is False
    assert CAN_EXECUTE is False


def test_invalid_probability_package_fails_closed():
    with pytest.raises(ValueError, match="probability_outside_bounds"):
        evaluate_favorite_upset_alert(
            sport="WNBA",
            market_favorite="A",
            market_favorite_verified=True,
            governed_outcomes=(
                outcome("A", 0.60, 0.65, 0.70),
                outcome("B", 0.40, 0.30, 0.45),
            ),
        )
