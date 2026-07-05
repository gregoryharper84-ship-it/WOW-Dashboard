"""
test_llp_bridge.py  —  LLP-Kalshi Sports Bridge unit tests
WOW-PATCH-2026-07-05-LLP-KALSHI-SPORTS-BRIDGE v2

Tests run without external network access. All mapper/normalizer/eval logic
is deterministic and testable offline.

Run:
  cd artifacts/flask-scoring-api
  python -m pytest kalshi_engine/tests/test_llp_bridge.py -v
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from kalshi_engine.llp_bridge.market_mapper import KalshiMarketMapper
from kalshi_engine.llp_bridge.price_normalizer import KalshiPriceNormalizer
from kalshi_engine.llp_bridge import ml_evaluate


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _raw_orderbook(yes_bid_cents=57, no_bid_cents=41):
    return {
        "orderbook": {
            "yes": [{"price": yes_bid_cents, "quantity": 300}],
            "no":  [{"price": no_bid_cents,  "quantity": 300}],
        }
    }


def _iso_seconds_ago(seconds: float) -> str:
    ts = datetime.now(tz=timezone.utc) - timedelta(seconds=seconds)
    return ts.isoformat().replace("+00:00", "Z")


LAKERS_MARKET = {
    "ticker": "KXNBAGAME-25JUL05LAL/W",
    "event_ticker": "KXNBAGAME-25JUL05LAL",
    "title": "Los Angeles Lakers vs Boston Celtics winner",
    "mve_collection_ticker": None,
}
COMBO_MARKET = {
    "ticker": "KXNBACOMBO-25JUL05/W",
    "event_ticker": "KXNBACOMBO-25JUL05",
    "title": "NBA multi-game combo",
    "mve_collection_ticker": "KXNBACOMBO-25JUL05",
}
UNRELATED_MARKET = {
    "ticker": "KXNBAGAME-25JUL05WAR/W",
    "event_ticker": "KXNBAGAME-25JUL05WAR",
    "title": "Golden State Warriors vs Phoenix Suns winner",
    "mve_collection_ticker": None,
}
PARTIAL_MARKET = {
    "ticker": "KXNBAGAME-25JUL05LALX/W",
    "event_ticker": "KXNBAGAME-25JUL05LALX",
    "title": "Los Angeles Lakers vs Miami Heat winner",
    "mve_collection_ticker": None,
}


# ---------------------------------------------------------------------------
# KalshiMarketMapper — exact vs fuzzy
# ---------------------------------------------------------------------------

class TestMarketMapper:
    def test_exact_match(self):
        result = KalshiMarketMapper().map_game_to_ticker(
            llp_home_team="Los Angeles Lakers",
            llp_away_team="Boston Celtics",
            llp_sport="NBA",
            candidate_markets=[LAKERS_MARKET, UNRELATED_MARKET],
        )
        assert result["match_type"] == "EXACT"
        assert result["ticker"] == LAKERS_MARKET["ticker"]
        assert result["label_ceiling"] is None
        assert result["can_approve_bets"] is False
        assert result["dry_run_only"] is True
        assert result["can_execute"] is False

    def test_fuzzy_single_team_match_caps_scout(self):
        result = KalshiMarketMapper().map_game_to_ticker(
            llp_home_team="Los Angeles Lakers",
            llp_away_team="Denver Nuggets",  # not in any candidate
            llp_sport="NBA",
            candidate_markets=[PARTIAL_MARKET],
        )
        assert result["match_type"] == "FUZZY"
        assert result["label_ceiling"] == "LLP_SCOUT"
        assert result["can_approve_bets"] is False

    def test_ambiguous_multi_exact_caps_scout(self):
        duplicate = dict(LAKERS_MARKET, ticker="KXNBAGAME-DUPLICATE/W")
        result = KalshiMarketMapper().map_game_to_ticker(
            llp_home_team="Los Angeles Lakers",
            llp_away_team="Boston Celtics",
            llp_sport="NBA",
            candidate_markets=[LAKERS_MARKET, duplicate],
        )
        assert result["match_type"] == "FUZZY"
        assert result["label_ceiling"] == "LLP_SCOUT"

    def test_no_match(self):
        result = KalshiMarketMapper().map_game_to_ticker(
            llp_home_team="Milwaukee Bucks",
            llp_away_team="Dallas Mavericks",
            llp_sport="NBA",
            candidate_markets=[UNRELATED_MARKET],
        )
        assert result["match_type"] == "NONE"
        assert result["ticker"] is None
        assert result["label_ceiling"] == "LLP_SCOUT"

    def test_combo_market_never_treated_as_winner_market_upstream(self):
        # Mapper itself doesn't filter combos (inventory adapter does), but
        # verify combo titles don't accidentally exact-match team names badly.
        result = KalshiMarketMapper().map_game_to_ticker(
            llp_home_team="Los Angeles Lakers",
            llp_away_team="Boston Celtics",
            llp_sport="NBA",
            candidate_markets=[COMBO_MARKET],
        )
        assert result["match_type"] == "NONE"


# ---------------------------------------------------------------------------
# KalshiPriceNormalizer — staleness buckets + executable-vs-midpoint
# ---------------------------------------------------------------------------

class TestPriceNormalizer:
    @pytest.mark.parametrize("age_seconds,expected_grade", [
        (10,  "A"),
        (59,  "A"),
        (60,  "B"),
        (250, "B"),
        (300, "C"),
        (599, "C"),
        (600, "KALSHI_DATA_UNOBTAINABLE"),
        (1000, "KALSHI_DATA_UNOBTAINABLE"),
    ])
    def test_staleness_grade_buckets(self, age_seconds, expected_grade):
        result = KalshiPriceNormalizer().normalize_for_side(
            raw_orderbook=_raw_orderbook(),
            ticker="TEST-1",
            side="YES",
            orderbook_timestamp_utc=_iso_seconds_ago(age_seconds),
        )
        assert result["staleness_grade"] == expected_grade

    def test_missing_timestamp_is_unobtainable(self):
        result = KalshiPriceNormalizer().normalize_for_side(
            raw_orderbook=_raw_orderbook(),
            ticker="TEST-1",
            side="YES",
            orderbook_timestamp_utc=None,
        )
        assert result["staleness_grade"] == "KALSHI_DATA_UNOBTAINABLE"
        assert result["usable"] is False

    def test_executable_price_differs_from_midpoint(self):
        # yes_bid=0.57, no_bid=0.41 -> yes_ask = 1-0.41 = 0.59; mid = (0.57+0.59)/2 = 0.58
        result = KalshiPriceNormalizer().normalize_for_side(
            raw_orderbook=_raw_orderbook(yes_bid_cents=57, no_bid_cents=41),
            ticker="TEST-1",
            side="YES",
            orderbook_timestamp_utc=_iso_seconds_ago(5),
        )
        assert result["executable_price"] == 0.59
        assert result["midpoint_price"] == 0.58
        assert result["executable_price"] != result["midpoint_price"]

    def test_always_carries_dry_run_flags(self):
        result = KalshiPriceNormalizer().normalize_for_side(
            raw_orderbook=_raw_orderbook(),
            ticker="TEST-1",
            side="YES",
            orderbook_timestamp_utc=_iso_seconds_ago(5),
        )
        assert result["dry_run_only"] is True
        assert result["can_execute"] is False


# ---------------------------------------------------------------------------
# evaluate_stub — hard caps + edge sequencing
# ---------------------------------------------------------------------------

class TestEvaluateStub:
    def _full_normalized_price(self, age_seconds=5, liquidity_grade="B"):
        np = KalshiPriceNormalizer().normalize_for_side(
            raw_orderbook=_raw_orderbook(),
            ticker="TEST-1",
            side="YES",
            orderbook_timestamp_utc=_iso_seconds_ago(age_seconds),
        )
        np["liquidity_grade"] = liquidity_grade
        return np

    def test_missing_settlement_fields_cap_scout(self):
        result = ml_evaluate.evaluate_stub(
            ticker="T", event_ticker=None, market_title="Some Game",
            settlement_condition="Official box score", model_probability=0.6,
            match_type="EXACT", normalized_price=self._full_normalized_price(),
            inventory_signal="INVENTORY_READY",
        )
        assert result["label"] == "LLP_SCOUT"
        assert result["dry_run_only"] is True
        assert result["can_execute"] is False
        assert result["stub"] is True
        assert result["connected"] is False

    def test_fuzzy_match_caps_scout_even_with_full_settlement(self):
        result = ml_evaluate.evaluate_stub(
            ticker="T", event_ticker="E", market_title="Team A vs Team B",
            settlement_condition="Official final score from league box score",
            model_probability=0.6, match_type="FUZZY",
            normalized_price=self._full_normalized_price(),
            inventory_signal="INVENTORY_READY",
        )
        assert result["label"] == "LLP_SCOUT"

    def test_missing_price_data_caps_watch_not_higher(self):
        result = ml_evaluate.evaluate_stub(
            ticker="T", event_ticker="E", market_title="Team A vs Team B",
            settlement_condition="Official final score from league box score",
            model_probability=0.6, match_type="EXACT",
            normalized_price=None,
            inventory_signal="INVENTORY_READY",
        )
        # fee/friction unavailable -> LLP_WATCH ceiling; no SCOUT-forcing condition here
        assert result["label"] in ("LLP_WATCH", "LLP_SCOUT")
        assert "LLP_WATCH" in result["ceilings_applied"] or "LLP_SCOUT" in result["ceilings_applied"]
        assert result["can_execute"] is False

    def test_never_emits_playable_or_approved(self):
        result = ml_evaluate.evaluate_stub(
            ticker="T", event_ticker="E", market_title="Team A vs Team B",
            settlement_condition="Official final score from league box score",
            model_probability=0.95, match_type="EXACT",
            normalized_price=self._full_normalized_price(),
            inventory_signal="INVENTORY_READY",
        )
        assert result["label"] not in ("LLP_PLAYABLE", "LLP_APPROVED")
        assert result["stub"] is True

    def test_edge_sequencing_order_present_and_in_order(self):
        result = ml_evaluate.evaluate_stub(
            ticker="T", event_ticker="E", market_title="Team A vs Team B",
            settlement_condition="Official final score from league box score",
            model_probability=0.85, match_type="EXACT",
            normalized_price=self._full_normalized_price(),
            inventory_signal="INVENTORY_READY",
        )
        names = [s["name"] for s in result["steps"]]
        assert names == ["spread", "fee_friction", "staleness_grade", "shrinkage", "compare_to_floor"]

    def test_shrinkage_applied_only_above_threshold(self):
        below = ml_evaluate.evaluate_stub(
            ticker="T", event_ticker="E", market_title="Team A vs Team B",
            settlement_condition="Official final score from league box score",
            model_probability=0.70, match_type="EXACT",
            normalized_price=self._full_normalized_price(),
            inventory_signal="INVENTORY_READY",
        )
        above = ml_evaluate.evaluate_stub(
            ticker="T", event_ticker="E", market_title="Team A vs Team B",
            settlement_condition="Official final score from league box score",
            model_probability=0.90, match_type="EXACT",
            normalized_price=self._full_normalized_price(),
            inventory_signal="INVENTORY_READY",
        )
        below_shrink_step = next(s for s in below["steps"] if s["name"] == "shrinkage")
        above_shrink_step = next(s for s in above["steps"] if s["name"] == "shrinkage")
        assert below_shrink_step["applied"] is False
        assert above_shrink_step["applied"] is True

    def test_dry_run_and_execute_flags_always_present(self):
        result = ml_evaluate.evaluate_stub(
            ticker=None, event_ticker=None, market_title=None,
            settlement_condition=None, model_probability=None,
            match_type="NONE", normalized_price=None,
        )
        assert result["dry_run_only"] is True
        assert result["can_execute"] is False
        assert result["can_approve_bets"] is False

    def test_inventory_not_ready_caps_scout_even_with_full_data(self):
        result = ml_evaluate.evaluate_stub(
            ticker="T", event_ticker="E", market_title="Team A vs Team B",
            settlement_condition="Official final score from league box score",
            model_probability=0.95, match_type="EXACT",
            normalized_price=self._full_normalized_price(),
            inventory_signal="INVENTORY_EMPTY",
        )
        assert result["label"] == "LLP_SCOUT"
        assert any("INVENTORY_NOT_READY" in w for w in result["warnings"])

    def test_inventory_signal_defaults_to_empty_when_unspecified(self):
        result = ml_evaluate.evaluate_stub(
            ticker="T", event_ticker="E", market_title="Team A vs Team B",
            settlement_condition="Official final score from league box score",
            model_probability=0.95, match_type="EXACT",
            normalized_price=self._full_normalized_price(),
        )
        assert result["label"] == "LLP_SCOUT"

    def test_inventory_ready_alone_does_not_bypass_other_caps(self):
        result = ml_evaluate.evaluate_stub(
            ticker="T", event_ticker="E", market_title="Team A vs Team B",
            settlement_condition="Official final score from league box score",
            model_probability=0.6, match_type="FUZZY",
            normalized_price=self._full_normalized_price(),
            inventory_signal="INVENTORY_READY",
        )
        assert result["label"] == "LLP_SCOUT"
