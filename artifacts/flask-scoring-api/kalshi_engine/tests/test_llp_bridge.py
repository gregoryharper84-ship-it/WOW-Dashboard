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
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from kalshi_engine.llp_bridge.market_mapper import KalshiMarketMapper
from kalshi_engine.llp_bridge.price_normalizer import KalshiPriceNormalizer
from kalshi_engine.llp_bridge import ml_evaluate
from kalshi_engine.llp_bridge import consensus_odds as _consensus_odds_mod


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

    def _consensus(self, fair_probability=0.6, status="AVAILABLE", single_book=False, book_count=2):
        return {
            "status": status,
            "consensus_fair_probability": fair_probability,
            "books_used": ["fanduel", "draftkings"][:max(book_count, 0)] or [],
            "book_count": book_count,
            "single_book_fallback": single_book,
            "max_book_spread": 0.01,
            "oldest_book_age_seconds": 60.0,
            "source": "the_odds_api",
            "blocker_tags": [] if status == "AVAILABLE" and not single_book else [f"ODDS_CONSENSUS_{status}"],
            "detail": "test fixture",
        }

    def test_missing_settlement_fields_cap_scout(self):
        result = ml_evaluate.evaluate_stub(
            ticker="T", event_ticker=None, market_title="Some Game",
            settlement_condition="Official box score", model_probability=0.6,
            match_type="EXACT", normalized_price=self._full_normalized_price(),
            inventory_signal="INVENTORY_READY", consensus_odds=self._consensus(),
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
            inventory_signal="INVENTORY_READY", consensus_odds=self._consensus(),
        )
        names = [s["name"] for s in result["steps"]]
        assert names == [
            "sportsbook_no_vig_consensus", "spread", "fee_friction",
            "staleness_grade", "shrinkage", "compare_to_floor",
        ]

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
        # WOW-PATCH-2026-07-07: must also supply the three new gate params
        # (direct_api source, market open, fresh final-lock) to avoid Gate A/C.
        fresh_ts = (
            datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")
        )
        result = ml_evaluate.evaluate_stub(
            ticker="T", event_ticker="E", market_title="Team A vs Team B",
            settlement_condition="Official final score from league box score",
            model_probability=0.75, match_type="EXACT",
            normalized_price=self._full_normalized_price(age_seconds=5, liquidity_grade="A"),
            inventory_signal="INVENTORY_READY",
            consensus_odds=self._consensus(fair_probability=0.75),
            kalshi_orderbook_source="direct_api",
            trading_active=True,
            final_lock_rechecked_at=fresh_ts,
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

    # -- 2026-07-05 Kalshi Sports ML Edge Rule (WNBA/MLB Only) — mandatory
    #    sportsbook no-vig consensus HARD GATE, evaluated via evaluate_stub's
    #    consensus_odds param (dict shape identical to
    #    consensus_odds.get_consensus_no_vig_probability's return). --------

    def test_consensus_missing_caps_scout(self):
        # NOT_CALLED (e.g. odds API key unset / unknown sport) — no consensus
        # quote exists at all, so a money edge can never be computed from
        # model_probability alone. Must cap at LLP_SCOUT even with otherwise
        # perfect data.
        result = ml_evaluate.evaluate_stub(
            ticker="T", event_ticker="E", market_title="Team A vs Team B",
            settlement_condition="Official final score from league box score",
            model_probability=0.75, match_type="EXACT",
            normalized_price=self._full_normalized_price(age_seconds=5, liquidity_grade="A"),
            inventory_signal="INVENTORY_READY",
            consensus_odds=self._consensus(status="NOT_CALLED", fair_probability=None,
                                            book_count=0),
        )
        assert result["label"] == "LLP_SCOUT"
        assert any("ODDS_CONSENSUS_NOT_CALLED" in w for w in result["warnings"])
        assert result["dry_run_only"] is True
        assert result["can_execute"] is False

    def test_consensus_failed_caps_scout(self):
        # FAILED (e.g. odds API returned 401/timeout) is distinct from
        # NOT_CALLED but must be capped identically — never fabricate a
        # fallback probability when the real lookup errored.
        result = ml_evaluate.evaluate_stub(
            ticker="T", event_ticker="E", market_title="Team A vs Team B",
            settlement_condition="Official final score from league box score",
            model_probability=0.75, match_type="EXACT",
            normalized_price=self._full_normalized_price(age_seconds=5, liquidity_grade="A"),
            inventory_signal="INVENTORY_READY",
            consensus_odds=self._consensus(status="FAILED", fair_probability=None,
                                            book_count=0),
        )
        assert result["label"] == "LLP_SCOUT"
        assert any("ODDS_CONSENSUS_FAILED" in w for w in result["warnings"])

    def test_consensus_stale_caps_watch_not_playable(self):
        # A real consensus once existed but the newest usable quote exceeds
        # the freshness window — real data, but not trustworthy enough to
        # approve off. Ceiling is LLP_WATCH, not LLP_SCOUT and not PLAYABLE.
        result = ml_evaluate.evaluate_stub(
            ticker="T", event_ticker="E", market_title="Team A vs Team B",
            settlement_condition="Official final score from league box score",
            model_probability=0.75, match_type="EXACT",
            normalized_price=self._full_normalized_price(age_seconds=5, liquidity_grade="A"),
            inventory_signal="INVENTORY_READY",
            consensus_odds=self._consensus(status="STALE", fair_probability=None,
                                            book_count=1),
        )
        assert result["label"] == "LLP_WATCH"
        assert result["label"] != "LLP_PLAYABLE"
        assert any("ODDS_CONSENSUS_STALE" in w for w in result["warnings"])

    def test_consensus_single_book_fallback_caps_watch(self):
        # Exactly one live bookmaker quote — per the rule, a single raw ML
        # price is NEVER treated as fair probability outright. Must cap at
        # LLP_WATCH even though the quote itself is fresh and would
        # otherwise clear the edge floor.
        result = ml_evaluate.evaluate_stub(
            ticker="T", event_ticker="E", market_title="Team A vs Team B",
            settlement_condition="Official final score from league box score",
            model_probability=0.75, match_type="EXACT",
            normalized_price=self._full_normalized_price(age_seconds=5, liquidity_grade="A"),
            inventory_signal="INVENTORY_READY",
            consensus_odds=self._consensus(status="AVAILABLE", fair_probability=0.75,
                                            single_book=True, book_count=1),
        )
        assert result["label"] == "LLP_WATCH"
        assert result["label"] != "LLP_PLAYABLE"
        assert any("ODDS_CONSENSUS_SINGLE_BOOK" in w for w in result["warnings"])

    def test_consensus_contradiction_caps_watch(self):
        # Two-plus fresh books disagree beyond the tolerance spread — a
        # real consensus attempt was made, but it isn't trustworthy/
        # independent enough to approve off. Ceiling is LLP_WATCH.
        result = ml_evaluate.evaluate_stub(
            ticker="T", event_ticker="E", market_title="Team A vs Team B",
            settlement_condition="Official final score from league box score",
            model_probability=0.75, match_type="EXACT",
            normalized_price=self._full_normalized_price(age_seconds=5, liquidity_grade="A"),
            inventory_signal="INVENTORY_READY",
            consensus_odds=self._consensus(status="CONTRADICTORY", fair_probability=0.60,
                                            book_count=2),
        )
        assert result["label"] == "LLP_WATCH"
        assert result["label"] != "LLP_PLAYABLE"
        assert any("ODDS_CONSENSUS_CONTRADICTORY" in w for w in result["warnings"])

    def test_consensus_successful_gate_reaches_playable(self):
        # A fresh, non-contradictory, 2+-book consensus that independently
        # clears the edge floor alongside the model probability is the only
        # path to LLP_PLAYABLE under the new rule.
        # WOW-PATCH-2026-07-07: must also supply the three new gate params.
        fresh_ts = (
            datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")
        )
        result = ml_evaluate.evaluate_stub(
            ticker="T", event_ticker="E", market_title="Team A vs Team B",
            settlement_condition="Official final score from league box score",
            model_probability=0.75, match_type="EXACT",
            normalized_price=self._full_normalized_price(age_seconds=5, liquidity_grade="A"),
            inventory_signal="INVENTORY_READY",
            consensus_odds=self._consensus(status="AVAILABLE", fair_probability=0.75,
                                            single_book=False, book_count=2),
            kalshi_orderbook_source="direct_api",
            trading_active=True,
            final_lock_rechecked_at=fresh_ts,
        )
        assert result["label"] == "LLP_PLAYABLE"
        assert result["ceilings_applied"] == []
        assert result["blocker_tags"] == []
        compare_step = next(s for s in result["steps"] if s["name"] == "compare_to_floor")
        assert compare_step["consensus_adjusted_edge"] is not None
        assert compare_step["consensus_adjusted_edge"] >= ml_evaluate.EDGE_FLOOR
        assert compare_step["meets_floor"] is True

    def test_consensus_edge_floor_checked_independently_of_model_edge(self):
        # A model that clears its own edge floor must still be blocked when
        # the independent consensus-derived edge does not clear the floor —
        # the model can never manufacture approval-eligible edge alone.
        result = ml_evaluate.evaluate_stub(
            ticker="T", event_ticker="E", market_title="Team A vs Team B",
            settlement_condition="Official final score from league box score",
            model_probability=0.80, match_type="EXACT",
            normalized_price=self._full_normalized_price(age_seconds=5, liquidity_grade="A"),
            inventory_signal="INVENTORY_READY",
            # consensus fair probability barely above executable_price(0.59) +
            # fee drag -> consensus-side edge should fail to clear EDGE_FLOOR
            # even though the model side (0.80, shrunk) clears it.
            consensus_odds=self._consensus(status="AVAILABLE", fair_probability=0.60,
                                            single_book=False, book_count=2),
        )
        compare_step = next(s for s in result["steps"] if s["name"] == "compare_to_floor")
        assert compare_step["meets_floor"] is False
        assert result["label"] != "LLP_PLAYABLE"


# ---------------------------------------------------------------------------
# consensus_odds.get_consensus_no_vig_probability — sportsbook no-vig
# consensus gate (Kalshi Sports ML Edge Rule, WNBA/MLB Only, 2026-07-05).
# The Odds API is mocked as primary source; TheRundown is mocked as
# fallback/corroboration only. Both are patched at the module boundary the
# consensus_odds module actually imports (`_odds_api` / `_rundown`), never
# hitting the network.
# ---------------------------------------------------------------------------

class TestConsensusNoVigProbability:
    def _odds_api_event(self, home="Seattle Storm", away="Las Vegas Aces",
                         books=None, seconds_ago=60):
        ts = _iso_seconds_ago(seconds_ago)
        default_books = books if books is not None else [
            {"key": "fanduel",   "price_home": -150, "price_away": 130},
            {"key": "draftkings", "price_home": -145, "price_away": 125},
        ]
        bookmakers = []
        for b in default_books:
            bookmakers.append({
                "key": b["key"],
                "markets": [{
                    "key": "h2h",
                    "last_update": ts,
                    "outcomes": [
                        {"name": home, "price": b["price_home"]},
                        {"name": away, "price": b["price_away"]},
                    ],
                }],
            })
        return {"home_team": home, "away_team": away, "bookmakers": bookmakers}

    def test_missing_odds_returns_not_called_when_no_key(self):
        # services.odds_api._get already handles the "no API key" case by
        # returning (None, "NOT_CALLED: ...") — get_h2h_odds propagates that
        # as ([], status). With Rundown also producing nothing, the overall
        # result must be NOT_CALLED (or FAILED if Rundown hard-errors),
        # never a fabricated probability.
        with mock.patch.object(_consensus_odds_mod._odds_api, "get_h2h_odds",
                                return_value=([], "NOT_CALLED: ODDS_API_KEY not set")), \
             mock.patch.object(_consensus_odds_mod._rundown, "get_moneyline_events_for_sport",
                                return_value=([], "NOT_CALLED: RUNDOWN_API_KEY not set")):
            result = _consensus_odds_mod.get_consensus_no_vig_probability(
                "WNBA", "Seattle Storm", "Las Vegas Aces", "Seattle Storm",
            )
        assert result["status"] == "NOT_CALLED"
        assert result["consensus_fair_probability"] is None
        assert result["book_count"] == 0
        assert "ODDS_CONSENSUS_UNAVAILABLE" in result["blocker_tags"]

    def test_missing_odds_returns_failed_on_hard_error(self):
        # A hard failure (e.g. invalid key / timeout) is distinct from
        # NOT_CALLED and must be surfaced as FAILED, not silently treated
        # the same or papered over with a fabricated value.
        with mock.patch.object(_consensus_odds_mod._odds_api, "get_h2h_odds",
                                return_value=([], "FAILED: invalid ODDS_API_KEY")), \
             mock.patch.object(_consensus_odds_mod._rundown, "get_moneyline_events_for_sport",
                                return_value=([], "FAILED: invalid RUNDOWN_API_KEY")):
            result = _consensus_odds_mod.get_consensus_no_vig_probability(
                "WNBA", "Seattle Storm", "Las Vegas Aces", "Seattle Storm",
            )
        assert result["status"] == "FAILED"
        assert result["consensus_fair_probability"] is None

    def test_stale_odds_returns_stale_status_not_available(self):
        # Both books are older than STALE_SECONDS -> STALE, not AVAILABLE.
        # Never treat an old snapshot as a live fair probability.
        stale_event = self._odds_api_event(
            seconds_ago=_consensus_odds_mod.STALE_SECONDS + 600,
        )
        with mock.patch.object(_consensus_odds_mod._odds_api, "get_h2h_odds",
                                return_value=([stale_event], "AVAILABLE (remaining=10)")), \
             mock.patch.object(_consensus_odds_mod._rundown, "get_moneyline_events_for_sport",
                                return_value=([], "NOT_CALLED: RUNDOWN_API_KEY not set")):
            result = _consensus_odds_mod.get_consensus_no_vig_probability(
                "WNBA", "Seattle Storm", "Las Vegas Aces", "Seattle Storm",
            )
        assert result["status"] == "STALE"
        assert result["consensus_fair_probability"] is None
        assert result["oldest_book_age_seconds"] > _consensus_odds_mod.STALE_SECONDS

    def test_single_book_fallback_flagged(self):
        # Only one fresh bookmaker reports this game -> AVAILABLE but
        # single_book_fallback=True; a single raw ML price is never treated
        # as fair probability outright (caller must cap at LLP_WATCH).
        one_book_event = self._odds_api_event(books=[
            {"key": "fanduel", "price_home": -150, "price_away": 130},
        ])
        with mock.patch.object(_consensus_odds_mod._odds_api, "get_h2h_odds",
                                return_value=([one_book_event], "AVAILABLE (remaining=10)")), \
             mock.patch.object(_consensus_odds_mod._rundown, "get_moneyline_events_for_sport",
                                return_value=([], "NOT_CALLED: RUNDOWN_API_KEY not set")):
            result = _consensus_odds_mod.get_consensus_no_vig_probability(
                "WNBA", "Seattle Storm", "Las Vegas Aces", "Seattle Storm",
            )
        assert result["status"] == "AVAILABLE"
        assert result["single_book_fallback"] is True
        assert result["book_count"] == 1
        assert "ODDS_CONSENSUS_SINGLE_BOOK" in result["blocker_tags"]

    def test_consensus_contradiction_flagged(self):
        # Two fresh books disagree by more than CONTRADICTION_SPREAD ->
        # CONTRADICTORY, not silently averaged into a trusted consensus.
        contradictory_event = self._odds_api_event(books=[
            {"key": "fanduel",   "price_home": -400, "price_away": 320},  # ~0.80 fair
            {"key": "draftkings", "price_home": 150,  "price_away": -180},  # ~0.40 fair
        ])
        with mock.patch.object(_consensus_odds_mod._odds_api, "get_h2h_odds",
                                return_value=([contradictory_event], "AVAILABLE (remaining=10)")), \
             mock.patch.object(_consensus_odds_mod._rundown, "get_moneyline_events_for_sport",
                                return_value=([], "NOT_CALLED: RUNDOWN_API_KEY not set")):
            result = _consensus_odds_mod.get_consensus_no_vig_probability(
                "WNBA", "Seattle Storm", "Las Vegas Aces", "Seattle Storm",
            )
        assert result["status"] == "CONTRADICTORY"
        assert result["max_book_spread"] > _consensus_odds_mod.CONTRADICTION_SPREAD
        assert "ODDS_CONSENSUS_CONTRADICTORY" in result["blocker_tags"]

    def test_successful_no_vig_gate_two_books_available(self):
        # Two fresh, agreeing books -> AVAILABLE, not single_book_fallback,
        # no blocker tags — the only shape that lets ml_evaluate proceed to
        # a real edge comparison.
        agreeing_event = self._odds_api_event(books=[
            {"key": "fanduel",   "price_home": -150, "price_away": 130},
            {"key": "draftkings", "price_home": -145, "price_away": 125},
        ])
        with mock.patch.object(_consensus_odds_mod._odds_api, "get_h2h_odds",
                                return_value=([agreeing_event], "AVAILABLE (remaining=10)")), \
             mock.patch.object(_consensus_odds_mod._rundown, "get_moneyline_events_for_sport",
                                return_value=([], "NOT_CALLED: RUNDOWN_API_KEY not set")):
            result = _consensus_odds_mod.get_consensus_no_vig_probability(
                "WNBA", "Seattle Storm", "Las Vegas Aces", "Seattle Storm",
            )
        assert result["status"] == "AVAILABLE"
        assert result["single_book_fallback"] is False
        assert result["book_count"] == 2
        assert 0.0 < result["consensus_fair_probability"] < 1.0
        assert result["blocker_tags"] == []

    def test_rundown_fallback_used_only_when_odds_api_has_no_fresh_books(self):
        # Odds API returns zero events; Rundown corroborates with a single
        # fresh book -> should still surface as AVAILABLE/single_book, using
        # the Rundown fallback path, not silently NOT_CALLED.
        rundown_event = {
            "teams_normalized": [{"name": "Seattle Storm"}, {"name": "Las Vegas Aces"}],
            "lines": {
                "1": {
                    "moneyline": {
                        "moneyline_home": -150,
                        "moneyline_away": 130,
                        "date_updated": _iso_seconds_ago(60),
                    },
                },
            },
        }
        with mock.patch.object(_consensus_odds_mod._odds_api, "get_h2h_odds",
                                return_value=([], "AVAILABLE (remaining=10)")), \
             mock.patch.object(_consensus_odds_mod._rundown, "get_moneyline_events_for_sport",
                                return_value=([rundown_event], "AVAILABLE")):
            result = _consensus_odds_mod.get_consensus_no_vig_probability(
                "WNBA", "Seattle Storm", "Las Vegas Aces", "Seattle Storm",
            )
        assert result["status"] == "AVAILABLE"
        assert result["single_book_fallback"] is True
        assert result["source"] == "rundown"

    def test_never_calls_rundown_when_odds_api_has_fresh_books(self):
        # Rundown is fallback/corroboration only — must not be consulted at
        # all once Odds API already produced fresh usable books.
        agreeing_event = self._odds_api_event()
        with mock.patch.object(_consensus_odds_mod._odds_api, "get_h2h_odds",
                                return_value=([agreeing_event], "AVAILABLE (remaining=10)")), \
             mock.patch.object(_consensus_odds_mod._rundown, "get_moneyline_events_for_sport") as rd_mock:
            _consensus_odds_mod.get_consensus_no_vig_probability(
                "WNBA", "Seattle Storm", "Las Vegas Aces", "Seattle Storm",
            )
        rd_mock.assert_not_called()


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


# ---------------------------------------------------------------------------
# WOW-PATCH-2026-07-07-KALSHI-FINAL-LOCK-EDGE-DISCOVERY regression tests
# ---------------------------------------------------------------------------

class TestFinalLockEdgeDiscovery:
    """
    Six required regression tests:
      1. web-price-only cannot return LLP_PLAYABLE
      2. missing orderbook cannot return LLP_PLAYABLE
      3. stale orderbook cannot return LLP_PLAYABLE
      4. final-lock-skipped cannot return LLP_PLAYABLE
      5. edge below threshold cannot return LLP_PLAYABLE
      6. fresh orderbook + market open + final lock + edge above threshold
         → LLP_PLAYABLE is reachable, can_execute always False
    """

    def _full_np(self, age_seconds=5, liquidity_grade="B"):
        np = KalshiPriceNormalizer().normalize_for_side(
            raw_orderbook=_raw_orderbook(),
            ticker="KXMLBGAME-TEST/W",
            side="YES",
            orderbook_timestamp_utc=_iso_seconds_ago(age_seconds),
        )
        np["liquidity_grade"] = liquidity_grade
        return np

    def _good_consensus(self):
        return {
            "status": "AVAILABLE",
            "consensus_fair_probability": 0.70,
            "books_used": ["fanduel", "draftkings"],
            "book_count": 2,
            "single_book_fallback": False,
            "max_book_spread": 0.01,
            "oldest_book_age_seconds": 30.0,
            "source": "the_odds_api",
            "blocker_tags": [],
            "detail": "test fixture",
        }

    def _fresh_final_lock_ts(self, seconds_ago=60):
        from datetime import timedelta
        ts = datetime.now(tz=timezone.utc) - timedelta(seconds=seconds_ago)
        return ts.isoformat().replace("+00:00", "Z")

    def _base_kwargs(self):
        """Full passing inputs — individual tests override one field to fail."""
        return dict(
            ticker="KXMLBGAME-25JUL07COL/W",
            event_ticker="KXMLBGAME-25JUL07COL",
            market_title="Colorado Rockies vs Atlanta Braves winner",
            settlement_condition="Official box score from MLB.com determines the winner",
            model_probability=0.72,
            match_type="EXACT",
            normalized_price=self._full_np(age_seconds=5, liquidity_grade="B"),
            inventory_signal="INVENTORY_READY",
            consensus_odds=self._good_consensus(),
            market_type="main_winner",
            trading_active=True,
            final_lock_rechecked_at=self._fresh_final_lock_ts(seconds_ago=60),
            kalshi_orderbook_source="direct_api",
        )

    # ── Test 1: web-price-only (caller_supplied) cannot reach LLP_PLAYABLE ──
    def test_web_price_only_cannot_return_playable(self):
        kwargs = self._base_kwargs()
        kwargs["kalshi_orderbook_source"] = "caller_supplied"
        result = ml_evaluate.evaluate_stub(**kwargs)
        assert result["label"] != "LLP_PLAYABLE", (
            "caller_supplied orderbook source must never reach LLP_PLAYABLE"
        )
        assert "KALSHI_ORDERBOOK_SOURCE_NOT_DIRECT_API" in result["blocker_tags"]
        assert "LLP_WATCH" in result["ceilings_applied"]
        assert result["can_execute"] is False

    # ── Test 2: missing orderbook (fetch_failed) cannot reach LLP_PLAYABLE ──
    def test_missing_orderbook_cannot_return_playable(self):
        kwargs = self._base_kwargs()
        kwargs["kalshi_orderbook_source"] = "fetch_failed"
        # No normalized_price when fetch fails
        kwargs["normalized_price"] = None
        result = ml_evaluate.evaluate_stub(**kwargs)
        assert result["label"] != "LLP_PLAYABLE", (
            "fetch_failed orderbook source must never reach LLP_PLAYABLE"
        )
        assert "KALSHI_ORDERBOOK_SOURCE_NOT_DIRECT_API" in result["blocker_tags"]
        assert result["can_execute"] is False

    # ── Test 3: stale orderbook (age >= 600s) cannot reach LLP_PLAYABLE ──────
    def test_stale_orderbook_cannot_return_playable(self):
        kwargs = self._base_kwargs()
        kwargs["normalized_price"] = self._full_np(age_seconds=600, liquidity_grade="B")
        result = ml_evaluate.evaluate_stub(**kwargs)
        assert result["label"] != "LLP_PLAYABLE", (
            "orderbook aged >= 600s (KALSHI_DATA_UNOBTAINABLE) must never reach LLP_PLAYABLE"
        )
        step3 = next(s for s in result["steps"] if s.get("name") == "staleness_grade")
        assert step3["grade"] == "KALSHI_DATA_UNOBTAINABLE"
        assert result["can_execute"] is False

    # ── Test 4: final-lock not supplied → cap at LLP_WATCH, not LLP_PLAYABLE ─
    def test_final_lock_skipped_cannot_return_playable(self):
        kwargs = self._base_kwargs()
        kwargs["final_lock_rechecked_at"] = None
        result = ml_evaluate.evaluate_stub(**kwargs)
        assert result["label"] != "LLP_PLAYABLE", (
            "missing final_lock_rechecked_at must never reach LLP_PLAYABLE"
        )
        assert "FINAL_LOCK_RECHECK_REQUIRED" in result["blocker_tags"]
        assert "LLP_WATCH" in result["ceilings_applied"]
        assert result["final_lock_fresh"] is False
        assert result["can_execute"] is False

    # ── Test 5: edge below threshold cannot reach LLP_PLAYABLE ───────────────
    def test_edge_below_threshold_cannot_return_playable(self):
        kwargs = self._base_kwargs()
        # Consensus fair probability close to executable price (0.59) → near-zero edge
        kwargs["consensus_odds"] = {
            "status": "AVAILABLE",
            "consensus_fair_probability": 0.595,  # barely above 0.59 ask, below any floor
            "books_used": ["fanduel", "draftkings"],
            "book_count": 2,
            "single_book_fallback": False,
            "max_book_spread": 0.01,
            "oldest_book_age_seconds": 30.0,
            "source": "the_odds_api",
            "blocker_tags": [],
            "detail": "test fixture — near-zero consensus edge",
        }
        result = ml_evaluate.evaluate_stub(**kwargs)
        assert result["label"] != "LLP_PLAYABLE", (
            "post-friction consensus edge below EDGE_FLOOR must never reach LLP_PLAYABLE"
        )
        compare_step = next(s for s in result["steps"] if s["name"] == "compare_to_floor")
        assert compare_step["meets_floor"] is False
        assert result["can_execute"] is False

    # ── Test 6: all gates pass → LLP_PLAYABLE reachable; can_execute always False
    def test_all_gates_pass_returns_playable_with_no_execution(self):
        kwargs = self._base_kwargs()
        # Consensus at 0.70 vs executable 0.59 ask gives ~0.11 raw edge,
        # well above EDGE_FLOOR_MAIN=0.015 even after fee drag.
        result = ml_evaluate.evaluate_stub(**kwargs)
        assert result["label"] == "LLP_PLAYABLE", (
            "all gates passing with strong edge on a main_winner market must reach LLP_PLAYABLE"
        )
        assert result["can_execute"] is False, "can_execute must always be False"
        assert result["dry_run_only"] is True
        assert result["kalshi_orderbook_source"] == "direct_api"
        assert result["trading_active"] is True
        assert result["final_lock_fresh"] is True
        assert result["market_type"] == "main_winner"
        assert result["edge_floor"] == ml_evaluate.EDGE_FLOOR_MAIN
