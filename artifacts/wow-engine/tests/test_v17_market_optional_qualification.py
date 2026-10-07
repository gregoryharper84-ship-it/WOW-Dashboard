from __future__ import annotations

import pytest

from v17.market_optional_qualification import (
    MARKET_VALUE_INPUT_AVAILABLE,
    MARKET_VALUE_INPUT_UNAVAILABLE,
    MODEL_PROBABILITY_AVAILABLE,
    MarketInputError,
    normalize_user_market_input,
    qualification_receipt,
)


def test_model_probability_is_independent_of_market_availability():
    receipt = qualification_receipt(
        {"calibrated_probability": 0.71, "calibrated_lower_bound": 0.64}
    )
    assert receipt["probability_qualification"] == MODEL_PROBABILITY_AVAILABLE
    assert receipt["market_value_qualification"] == MARKET_VALUE_INPUT_UNAVAILABLE
    assert receipt["probability_can_exist_without_market"] is True
    assert receipt["market_probability_used_as_model"] is False
    assert receipt["can_execute"] is False


def test_user_supplied_spread_is_market_identity_only():
    market = normalize_user_market_input(
        {
            "market_family": "SPREAD",
            "selection": "Kansas City Chiefs",
            "line": -4.5,
            "american_odds": -110,
            "captured_at": "2026-10-07T12:00:00+00:00",
        }
    )
    receipt = qualification_receipt({"calibrated_probability": 0.68}, market)
    assert receipt["market_value_qualification"] == MARKET_VALUE_INPUT_AVAILABLE
    assert receipt["market_input"]["line"] == -4.5
    assert receipt["market_input"]["sporting_probability_authority"] is False
    assert receipt["market_probability_used_as_model"] is False


def test_user_supplied_prop_requires_exact_stat_line_and_direction():
    with pytest.raises(MarketInputError, match="PROP_STAT_TYPE_REQUIRED"):
        normalize_user_market_input(
            {
                "market_family": "PROP",
                "selection": "Player",
                "line": 24.5,
                "direction": "MORE",
            }
        )
    market = normalize_user_market_input(
        {
            "market_family": "PROP",
            "selection": "Player",
            "stat_type": "points",
            "line": 24.5,
            "direction": "LESS",
        }
    )
    assert market.line == 24.5
    assert market.direction == "LESS"
    assert market.sporting_probability_authority is False


def test_moneyline_user_input_does_not_compute_or_supply_probability():
    market = normalize_user_market_input(
        {
            "market_family": "MONEYLINE",
            "selection": "Texas Rangers",
            "american_odds": -135,
        }
    )
    payload = market.as_dict()
    assert payload["american_odds"] == -135.0
    assert "implied_probability" not in payload
    assert payload["sporting_probability_authority"] is False
