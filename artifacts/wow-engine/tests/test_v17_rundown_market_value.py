from datetime import datetime, timezone

import pytest

from v17.rundown_market_value import (
    american_implied_probability,
    build_moneyline_market_features,
    build_value_lane,
)


def _snapshot(kind, home_a, away_a, home_b, away_b):
    return {
        "snapshot_kind": kind,
        "timestamp": "2026-09-22T20:00:00Z",
        "moneyline": {
            "home_quotes": [
                {"bookmaker": "Pinnacle", "american_odds": home_a, "updated_at": "2026-09-22T19:59:50Z"},
                {"bookmaker": "Caesars", "american_odds": home_b, "updated_at": "2026-09-22T19:59:55Z"},
            ],
            "away_quotes": [
                {"bookmaker": "Pinnacle", "american_odds": away_a, "updated_at": "2026-09-22T19:59:50Z"},
                {"bookmaker": "Caesars", "american_odds": away_b, "updated_at": "2026-09-22T19:59:55Z"},
            ],
        },
    }


def test_best_price_is_not_sporting_probability_and_no_vig_is_same_book_pairing():
    features = build_moneyline_market_features(
        current=_snapshot("CURRENT", -150, 130, -125, 110),
        opening=_snapshot("OPENING", -130, 115, -120, 105),
        now=datetime(2026, 9, 22, 20, 0, tzinfo=timezone.utc),
    )
    current = features["current"]
    assert current["best_price"]["home"]["american_odds"] == -125
    assert current["best_executable_breakeven_probability"]["home"] == pytest.approx(125 / 225)
    assert current["best_price_age_seconds"]["home"] == pytest.approx(5.0)
    assert current["book_count"] == 2
    assert current["consensus_no_vig_probability"]["home"] != pytest.approx(125 / 225)
    assert features["movement_from_open_pp"]["home"] is not None
    assert features["probability_mutated_by_evidence"] is False
    assert features["can_execute"] is False


def test_value_lane_uses_governed_probability_and_lower_bound_separately():
    features = build_moneyline_market_features(
        current=_snapshot("CURRENT", -150, 130, -125, 110),
        now=datetime(2026, 9, 22, 20, 0, tzinfo=timezone.utc),
    )
    result = {
        "calibrated_home_probability": 0.624,
        "calibrated_away_probability": 0.376,
        "calibrated_home_lower_bound": 0.593,
        "calibrated_away_lower_bound": 0.345,
        "rank_eligible": True,
        "can_execute": False,
    }
    lane = build_value_lane(result, features)
    home = lane["sides"]["home"]
    break_even = american_implied_probability(-125)
    assert home["point_price_edge_pp"] == pytest.approx((0.624 - break_even) * 100)
    assert home["conservative_price_edge_pp"] == pytest.approx((0.593 - break_even) * 100)
    assert lane["probability_rank_mutated"] is False
    assert lane["can_execute"] is False


def test_market_features_do_not_invent_no_vig_without_paired_books():
    current = {
        "snapshot_kind": "CURRENT",
        "moneyline": {
            "home_quotes": [{"bookmaker": "Book A", "american_odds": -120}],
            "away_quotes": [{"bookmaker": "Book B", "american_odds": 110}],
        },
    }
    features = build_moneyline_market_features(current=current)
    assert features["current"]["book_count"] == 0
    assert features["current"]["consensus_no_vig_probability"]["home"] is None
    assert features["current"]["best_price"]["home"]["american_odds"] == -120
