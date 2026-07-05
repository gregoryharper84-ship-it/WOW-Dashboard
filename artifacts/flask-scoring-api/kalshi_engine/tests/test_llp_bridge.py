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
        # 2026-07-05: "stub"/"connected" now reflect live inventory_signal only,
        # not per-row gate outcomes — this row is capped at SCOUT for missing
        # settlement fields, but inventory itself is READY, so it's connected.
        assert result["stub"] is False
        assert result["connected"] is True

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

    def test_never_emits_approved_even_at_high_probability(self):
        # 2026-07-05: STUB_CEILING removed — LLP_PLAYABLE is now reachable
        # (see TestEvaluateStub playable-reachability tests below), but
        # LLP_APPROVED must remain permanently unreachable from this
        # stateless endpoint regardless of how favorable the inputs are.
        result = ml_evaluate.evaluate_stub(
            ticker="T", event_ticker="E", market_title="Team A vs Team B",
            settlement_condition="Official final score from league box score",
            model_probability=0.95, match_type="EXACT",
            normalized_price=self._full_normalized_price(),
            inventory_signal="INVENTORY_READY",
        )
        assert result["label"] != "LLP_APPROVED"

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

    # -- 2026-07-05 STUB_CEILING removal regression: LLP_PLAYABLE must now be
    #    reachable when every real gate passes, but LLP_APPROVED must remain
    #    permanently unreachable from this stateless endpoint. --------------
    def test_playable_reachable_when_all_real_gates_pass(self):
        # executable_price is 0.59 here (yes_bid=0.57/no_bid=0.41), so the
        # model probability must clear price + post-friction floor (~0.62)
        # to produce a real positive edge, not just a plausible-looking one.
        result = ml_evaluate.evaluate_stub(
            ticker="T", event_ticker="E", market_title="Team A vs Team B",
            settlement_condition="Official final score from league box score",
            model_probability=0.75, match_type="EXACT",
            normalized_price=self._full_normalized_price(age_seconds=5, liquidity_grade="A"),
            inventory_signal="INVENTORY_READY",
        )
        assert result["label"] == "LLP_PLAYABLE"
        assert result["ceilings_applied"] == []
        assert result["connected"] is True
        assert result["connected_status"] == "CONNECTED_READONLY"
        assert result["stub"] is False
        assert result["dry_run_only"] is True
        assert result["can_execute"] is False

    def test_approved_never_reachable_even_with_perfect_inputs(self):
        result = ml_evaluate.evaluate_stub(
            ticker="T", event_ticker="E", market_title="Team A vs Team B",
            settlement_condition="Official final score from league box score",
            model_probability=0.55, match_type="EXACT",
            normalized_price=self._full_normalized_price(age_seconds=1, liquidity_grade="A"),
            inventory_signal="INVENTORY_READY",
        )
        assert result["label"] != "LLP_APPROVED"

    def test_disconnected_stub_status_when_inventory_not_ready(self):
        result = ml_evaluate.evaluate_stub(
            ticker="T", event_ticker="E", market_title="Team A vs Team B",
            settlement_condition="Official final score from league box score",
            model_probability=0.55, match_type="EXACT",
            normalized_price=self._full_normalized_price(),
            inventory_signal="INVENTORY_EMPTY",
        )
        assert result["connected"] is False
        assert result["connected_status"] == "DRY_RUN_READY"
        assert result["stub"] is True


# ---------------------------------------------------------------------------
# orderbook_normalizer — live dollar-format (orderbook_fp) parsing
# 2026-07-05 root-cause fix: live Kalshi orderbook responses use
# orderbook_fp: {yes_dollars, no_dollars} with dollar-STRING prices
# (e.g. "0.4600"), not the previously-assumed integer-cents format.
# ---------------------------------------------------------------------------

class TestOrderbookNormalizerDollarFormat:
    def _raw_orderbook_fp(self, yes_dollars=None, no_dollars=None):
        return {
            "orderbook_fp": {
                "yes_dollars": yes_dollars if yes_dollars is not None else [["0.5500", "300.00"]],
                "no_dollars":  no_dollars  if no_dollars  is not None else [["0.4100", "300.00"]],
            }
        }

    def test_dollar_format_detected_and_parsed_correctly(self):
        from kalshi_engine.orderbook_normalizer import normalize
        result = normalize(self._raw_orderbook_fp(), ticker="TEST-DOLLARS")
        # yes_bid=0.55 -> yes_ask = 1 - no_bid(0.41) = 0.59
        assert result["best_yes_bid"] == 0.55
        assert result["best_yes_ask"] == 0.59
        assert result["mid_price"] == 0.57

    def test_dollar_format_not_misread_as_cents(self):
        # If this were mistakenly parsed as cents, "0.5500" -> 0.0055, which
        # would be wildly wrong. Confirm it stays in the 0-1 decimal range
        # as an actual dollar price, not divided by 100 again.
        from kalshi_engine.orderbook_normalizer import normalize
        result = normalize(self._raw_orderbook_fp(), ticker="TEST-DOLLARS")
        assert result["best_yes_bid"] == 0.55
        assert result["best_yes_bid"] != 0.0055

    def test_legacy_cents_format_still_works(self):
        from kalshi_engine.orderbook_normalizer import normalize
        legacy = {"orderbook": {"yes": [{"price": 57, "quantity": 300}],
                                 "no":  [{"price": 41, "quantity": 300}]}}
        result = normalize(legacy, ticker="TEST-CENTS")
        assert result["best_yes_bid"] == 0.57

    def test_liquidity_grade_present_for_dollar_format(self):
        from kalshi_engine.orderbook_normalizer import normalize
        result = normalize(
            self._raw_orderbook_fp(
                yes_dollars=[["0.5500", "600.00"]],
                no_dollars=[["0.4400", "600.00"]],
            ),
            ticker="TEST-DOLLARS",
        )
        assert result["liquidity_grade"] in ("A", "B", "C", "D", "F")


# ---------------------------------------------------------------------------
# KalshiInventoryAdapter — combo exclusion + signal semantics
# ---------------------------------------------------------------------------

class TestInventoryAdapterSignals:
    def test_combo_market_excluded_from_candidates(self):
        from kalshi_engine.llp_bridge.inventory_adapter import KalshiInventoryAdapter
        adapter = KalshiInventoryAdapter()
        real_game = {"ticker": "KXMLBGAME-1-A", "event_ticker": "KXMLBGAME-1",
                     "title": "A vs B", "mve_collection_ticker": None}
        combo = {"ticker": "KXMVE-COMBO-1", "event_ticker": "KXMVE-COMBO",
                 "title": "Multi-game combo", "mve_collection_ticker": "KXMVE-COMBO"}
        assert adapter._looks_like_sports_winner_market(real_game) is True
        assert adapter._looks_like_sports_winner_market(combo) is False

    def test_summarize_trims_to_expected_fields(self):
        from kalshi_engine.llp_bridge.inventory_adapter import KalshiInventoryAdapter
        raw = {
            "ticker": "KXMLBGAME-1-A", "event_ticker": "KXMLBGAME-1",
            "title": "A vs B", "status": "active", "close_time": "2026-07-06T00:00:00Z",
            "rules_primary": "Official box score", "mve_collection_ticker": None,
            "some_internal_field_we_never_forward": "should not leak",
        }
        summary = KalshiInventoryAdapter._summarize(raw, "MLB")
        assert summary["ticker"] == "KXMLBGAME-1-A"
        assert summary["league"] == "MLB"
        assert "some_internal_field_we_never_forward" not in summary

    def test_empty_inventory_signal_is_inventory_empty_not_error(self, monkeypatch):
        from kalshi_engine.llp_bridge import inventory_adapter as _ia_module

        def _fake_search_markets(series_ticker, status, limit):
            return {"markets": []}

        monkeypatch.setattr(_ia_module.kalshi_client, "search_markets", _fake_search_markets)
        result = _ia_module.KalshiInventoryAdapter().check_sports_inventory(limit=10)
        assert result["signal"] == "INVENTORY_EMPTY"
        assert result["candidates"] == []
        assert result["error"] is None
        assert result["dry_run_only"] is True
        assert result["can_execute"] is False

    def test_all_series_failing_is_unobtainable_not_empty(self, monkeypatch):
        from kalshi_engine.llp_bridge import inventory_adapter as _ia_module

        def _fake_search_markets(series_ticker, status, limit):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(_ia_module.kalshi_client, "search_markets", _fake_search_markets)
        result = _ia_module.KalshiInventoryAdapter().check_sports_inventory(limit=10)
        assert result["signal"] == "KALSHI_DATA_UNOBTAINABLE"
        assert result["candidates"] == []


# ---------------------------------------------------------------------------
# Read-only guarantee — no order-placement code exists anywhere in this stack
# ---------------------------------------------------------------------------

class TestNoOrderPlacementCode:
    _FORBIDDEN_PATTERNS = (
        "place_order", "create_order", "/orders\"", "'/orders'",
        "cancel_order", "\"POST\", \"/portfolio", "amend_order",
    )

    def _kalshi_engine_dir(self):
        return os.path.join(os.path.dirname(__file__), "..")

    def test_no_order_placement_keywords_in_kalshi_engine_source(self):
        base = self._kalshi_engine_dir()
        hits = []
        for root, _dirs, files in os.walk(base):
            if "tests" in root.split(os.sep):
                continue
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                path = os.path.join(root, fname)
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    content = fh.read()
                for pattern in self._FORBIDDEN_PATTERNS:
                    if pattern in content:
                        hits.append(f"{path}: {pattern}")
        assert hits == [], f"Forbidden order-placement pattern(s) found: {hits}"

    def test_kalshi_client_only_exposes_get_helpers(self):
        # "orderbook" is a legitimate read-only market-data noun (public
        # GET /markets/{ticker}/orderbook) — only flag callables that look
        # like actual order-PLACEMENT/management verbs.
        _ORDER_VERB_SUBSTRINGS = (
            "place_order", "create_order", "cancel_order", "amend_order",
            "submit_order", "modify_order", "delete_order",
        )
        from kalshi_engine import kalshi_client
        public_callables = [
            name for name in dir(kalshi_client)
            if not name.startswith("_") and callable(getattr(kalshi_client, name))
        ]
        for name in public_callables:
            lowered = name.lower()
            for verb in _ORDER_VERB_SUBSTRINGS:
                assert verb not in lowered, (
                    f"kalshi_client exposes an order-placement callable: {name} — "
                    f"this module must remain public-market-data-only."
                )
