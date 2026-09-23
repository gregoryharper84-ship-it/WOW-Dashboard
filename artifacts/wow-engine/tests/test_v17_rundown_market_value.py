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


def test_best_price_is_executable_threshold_not_sporting_probability():
    features = build_moneyline_market_features(
        current=_snapshot(),
        now=datetime(2026, 9, 22, 20, 0, tzinfo=timezone.utc),
    )
    current = features["current"]
    assert current["best_price"]["home"]["american_odds"] == -125
    assert current["best_executable_breakeven_probability"]["home"] == pytest.approx(125 / 225)
    assert current["consensus_no_vig_probability"]["home"] != pytest.approx(125 / 225)
    assert current["best_price_age_seconds"]["home"] == pytest.approx(5.0)
    assert features["probability_mutated_by_evidence"] is False
    assert features["can_execute"] is False


def test_no_vig_consensus_requires_same_book_opposing_prices():
    current = {
        "moneyline": {
            "home_quotes": [{"bookmaker": "Book A", "american_odds": -120}],
            "away_quotes": [{"bookmaker": "Book B", "american_odds": 110}],
        }
    }
    features = build_moneyline_market_features(current=current)
    assert features["current"]["book_count"] == 0
    assert features["current"]["consensus_no_vig_probability"]["home"] is None
    assert features["current"]["best_price"]["home"]["american_odds"] == -120


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
    assert lane["sides"]["home"]["point_price_edge_pp"] == pytest.approx((0.624 - threshold) * 100)
    assert lane["sides"]["home"]["conservative_price_edge_pp"] == pytest.approx((0.593 - threshold) * 100)
    assert lane["probability_rank_mutated"] is False
    assert lane["can_execute"] is False
