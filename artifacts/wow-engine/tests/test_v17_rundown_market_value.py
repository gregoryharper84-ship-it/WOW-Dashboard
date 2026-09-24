from datetime import datetime, timezone

import pytest

from v17.rundown_market_value import (
    american_implied_probability,
    build_moneyline_market_features,
    build_value_lane,
)


def _snapshot():
    return {
        "snapshot_kind": "CURRENT",
        "moneyline": {
            "home_quotes": [
                {"bookmaker": "Pinnacle", "american_odds": -150, "updated_at": "2026-09-22T19:59:50Z"},
                {"bookmaker": "Caesars", "american_odds": -125, "updated_at": "2026-09-22T19:59:55Z"},
            ],
            "away_quotes": [
                {"bookmaker": "Pinnacle", "american_odds": 130, "updated_at": "2026-09-22T19:59:50Z"},
                {"bookmaker": "Caesars", "american_odds": 110, "updated_at": "2026-09-22T19:59:55Z"},
            ],
        },
    }


def test_american_implied_probability_supports_favorites_and_underdogs():
    assert american_implied_probability(-152) == pytest.approx(152 / 252)
    assert american_implied_probability(148) == pytest.approx(100 / 248)


def test_best_price_is_executable_threshold_not_sporting_probability():
    features = build_moneyline_market_features(
        current=_snapshot(),
        now=datetime(2026, 9, 22, 20, 0, tzinfo=timezone.utc),
    )
    current = features["current"]
    assert features["market_type"] == "MONEYLINE"
    assert current["market_selector_required"] == "MONEYLINE"
    assert current["best_price"]["home"]["american_odds"] == -125
    assert current["best_executable_breakeven_probability"]["home"] == pytest.approx(125 / 225)
    assert current["consensus_no_vig_probability"]["home"] != pytest.approx(125 / 225)
    assert current["best_price_age_seconds"]["home"] == pytest.approx(5.0)
    assert features["probability_mutated_by_evidence"] is False
    assert features["can_execute"] is False


def test_no_vig_consensus_requires_same_book_opposing_prices_and_reports_vig():
    current = {
        "moneyline": {
            "home_quotes": [{"bookmaker": "Book A", "american_odds": -120}],
            "away_quotes": [{"bookmaker": "Book B", "american_odds": 110}],
        }
    }
    features = build_moneyline_market_features(current=current)
    assert features["current"]["book_count"] == 0
    assert features["current"]["consensus_no_vig_probability"]["home"] is None
    assert features["current"]["average_market_overround_pp"] is None
    assert features["current"]["best_price"]["home"]["american_odds"] == -120

    paired = build_moneyline_market_features(current=_snapshot())["current"]
    pinnacle = next(row for row in paired["paired_books"] if row["bookmaker"] == "Pinnacle")
    expected_overround = american_implied_probability(-150) + american_implied_probability(130) - 1.0
    assert pinnacle["market_overround_pp"] == pytest.approx(expected_overround * 100)
    assert paired["average_market_overround_pp"] is not None


def test_line_shopping_prefers_less_negative_price_for_same_side():
    current = {
        "moneyline": {
            "home_quotes": [
                {"bookmaker": "Book -175", "american_odds": -175},
                {"bookmaker": "Book -145", "american_odds": -145},
            ],
            "away_quotes": [],
        }
    }
    home = build_moneyline_market_features(current=current)["current"]["line_shopping"]["home"]
    assert home["best_price"]["american_odds"] == -145
    assert home["worst_price"]["american_odds"] == -175
    assert home["breakeven_improvement_pp"] == pytest.approx(
        (american_implied_probability(-175) - american_implied_probability(-145)) * 100
    )
    assert home["decimal_return_improvement_per_unit_staked"] > 0


def test_open_to_current_movement_is_market_evidence_only():
    opening = {
        "snapshot_kind": "OPEN",
        "moneyline": {
            "home_quotes": [{"bookmaker": "Pinnacle", "american_odds": -130}],
            "away_quotes": [{"bookmaker": "Pinnacle", "american_odds": 115}],
        },
    }
    current = {
        "snapshot_kind": "CURRENT",
        "moneyline": {
            "home_quotes": [{"bookmaker": "Pinnacle", "american_odds": -150}],
            "away_quotes": [{"bookmaker": "Pinnacle", "american_odds": 130}],
        },
    }
    features = build_moneyline_market_features(current=current, opening=opening)
    assert features["movement_from_open_pp"]["home"] is not None
    assert features["movement_role"] == "MARKET_EVIDENCE_ONLY_NOT_PROOF_OF_SHARP_MONEY_OR_NEWS"
    assert features["probability_mutated_by_evidence"] is False


def test_point_edge_and_lower_bound_edge_stay_separate():
    features = build_moneyline_market_features(current=_snapshot())
    scored = {
        "calibrated_home_probability": 0.624,
        "calibrated_away_probability": 0.376,
        "calibrated_home_lower_bound": 0.593,
        "calibrated_away_lower_bound": 0.345,
    }
    lane = build_value_lane(scored, features)
    threshold = american_implied_probability(-125)
    assert lane["market_type"] == "MONEYLINE"
    assert lane["value_basis"] == "GOVERNED_PROBABILITY_VS_BEST_EXECUTABLE_RAW_BREAKEVEN"
    assert lane["sides"]["home"]["point_price_edge_pp"] == pytest.approx((0.624 - threshold) * 100)
    assert lane["sides"]["home"]["conservative_price_edge_pp"] == pytest.approx((0.593 - threshold) * 100)
    assert lane["sides"]["home"]["line_shopping"]["best_price"]["american_odds"] == -125
    assert lane["probability_rank_mutated"] is False
    assert lane["can_execute"] is False
