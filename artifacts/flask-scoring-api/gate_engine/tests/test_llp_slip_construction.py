"""
Tests for gate_engine/llp_slip_construction.py
WOW-PATCH-2026-08-02-LLP-SLIP-CONSTRUCTION-INTEGRITY
"""
import pytest
from gate_engine.llp_slip_construction import (
    detect_cross_book_parlay,
    detect_same_game_correlated_stack,
    check_recency_consistency,
    run_slip_construction_integrity,
)


# ─────────────────────────────────────────────────────────────
# can_execute guard
# ─────────────────────────────────────────────────────────────

def test_can_execute_is_false():
    import gate_engine.llp_slip_construction as m
    assert m.can_execute is False


# ─────────────────────────────────────────────────────────────
# Rule 1 — Cross-book parlay detection
# ─────────────────────────────────────────────────────────────

class TestCrossBookParlayDetection:
    WNBA_SLIP = [
        {"book": "polymarket", "market": "Valkyries ML"},
        {"book": "polymarket", "market": "Wings ML"},
        {"book": "hardrock",   "market": "Arike points over"},
        {"book": "draftkings", "market": "Harrison rebounds over"},
    ]

    def test_single_book_no_parlay_language_passes(self):
        legs = [
            {"book": "draftkings", "market": "Celtics ML"},
            {"book": "draftkings", "market": "Tatum points over"},
        ]
        r = detect_cross_book_parlay(legs, "Two-leg DraftKings slip")
        assert r.passed

    def test_cross_book_with_parlay_language_fails(self):
        r = detect_cross_book_parlay(
            self.WNBA_SLIP,
            "Full Four-Leg Ticket — your best parlay from this list",
        )
        assert not r.passed
        assert "CROSS_BOOK_PARLAY_ILLUSION" in r.labels
        assert any("CROSS_BOOK_PARLAY_ILLUSION" in b for b in r.blockers)
        assert "independent single bets" in " ".join(r.blockers)

    def test_cross_book_without_parlay_language_warns_only(self):
        r = detect_cross_book_parlay(self.WNBA_SLIP, "Four picks for tonight")
        assert r.passed
        assert "CROSS_BOOK_PARLAY_ILLUSION" not in r.labels
        assert any("cross_book_slip_no_parlay_language" in w for w in r.warnings)

    def test_single_book_with_parlay_language_passes(self):
        legs = [
            {"book": "fanduel", "market": "Lakers ML"},
            {"book": "fanduel", "market": "LeBron points over"},
        ]
        r = detect_cross_book_parlay(legs, "FanDuel parlay ticket")
        assert r.passed

    def test_polymarket_plus_draftkings_parlay_fails(self):
        legs = [
            {"book": "polymarket", "market": "Team A wins"},
            {"book": "draftkings", "market": "Player X points over"},
        ]
        r = detect_cross_book_parlay(legs, "combined parlay")
        assert not r.passed

    def test_empty_legs_passes(self):
        r = detect_cross_book_parlay([], "multi-leg parlay")
        assert r.passed

    def test_four_leg_ticket_phrase_detected(self):
        r = detect_cross_book_parlay(self.WNBA_SLIP, "Full four-leg ticket")
        assert not r.passed
        assert r.detail["parlay_phrase_detected"] == "four-leg ticket"

    def test_combined_odds_phrase_detected(self):
        r = detect_cross_book_parlay(self.WNBA_SLIP, "combined odds across platforms")
        assert not r.passed

    def test_books_in_detail(self):
        r = detect_cross_book_parlay(self.WNBA_SLIP, "parlay ticket")
        assert "books_found" in r.detail
        assert "polymarket" in r.detail["books_found"]

    def test_exchange_vs_sportsbook_split_in_detail(self):
        r = detect_cross_book_parlay(self.WNBA_SLIP, "parlay")
        assert "polymarket" in r.detail["exchanges_found"]
        assert "draftkings" in r.detail["sportsbooks_found"]


# ─────────────────────────────────────────────────────────────
# Rule 2 — Same-game correlated stack detection
# ─────────────────────────────────────────────────────────────

class TestSameGameCorrelatedStack:
    def test_clean_multi_game_slip_passes(self):
        legs = [
            {"market_type": "ML",          "team": "celtics",  "game_id": "G1", "market": "Celtics ML",    "rationale": ""},
            {"market_type": "PLAYER_PROP", "team": "lakers",   "game_id": "G2", "market": "LeBron pts",    "rationale": "LeBron averages 28 ppg"},
        ]
        r = detect_same_game_correlated_stack(legs)
        assert r.passed

    def test_ml_plus_correlated_prop_same_team_fails(self):
        """Wings ML + Arike points — Arike rationale depends on Wings winning."""
        legs = [
            {
                "market_type": "ML",
                "team": "wings",
                "game_id": "G3",
                "market": "Wings ML",
                "rationale": "",
            },
            {
                "market_type": "PLAYER_PROP",
                "team": "wings",
                "game_id": "G3",
                "market": "Arike points over 22.5",
                "rationale": "Dallas controlling the game as heavy favorite — offense running through Arike",
            },
        ]
        r = detect_same_game_correlated_stack(legs)
        assert not r.passed
        assert "SAME_GAME_CORRELATED_STACK" in r.labels
        assert any("SAME_GAME_CORRELATED_STACK" in b for b in r.blockers)

    def test_ml_plus_correlated_prop_trailing_rationale_fails(self):
        legs = [
            {"market_type": "MONEYLINE", "team": "valkyries", "game_id": "G4", "market": "Valkyries ML", "rationale": ""},
            {
                "market_type": "PLAYER_PROP",
                "team": "valkyries",
                "game_id": "G4",
                "market": "Harrison rebounds over",
                "rationale": "Golden State winning big → Toronto trailing → garbage-time possessions",
            },
        ]
        r = detect_same_game_correlated_stack(legs)
        assert not r.passed

    def test_blowout_rationale_fails(self):
        legs = [
            {"market_type": "ML",          "team": "clippers", "game_id": "G5", "market": "Clippers ML",    "rationale": ""},
            {"market_type": "PLAYER_PROP", "team": "clippers", "game_id": "G5", "market": "Kawhi pts",      "rationale": "Clippers should dominate tonight"},
        ]
        r = detect_same_game_correlated_stack(legs)
        assert not r.passed

    def test_different_game_no_flag(self):
        legs = [
            {"market_type": "ML",          "team": "team_a", "game_id": "G1", "market": "Team A ML",   "rationale": ""},
            {"market_type": "PLAYER_PROP", "team": "team_b", "game_id": "G2", "market": "Player pts",  "rationale": "team_a winning big → player scores more"},
        ]
        r = detect_same_game_correlated_stack(legs)
        # Different game_id — should not flag
        assert r.passed

    def test_neutral_rationale_no_flag(self):
        legs = [
            {"market_type": "ML",          "team": "bucks", "game_id": "G6", "market": "Bucks ML",    "rationale": ""},
            {"market_type": "PLAYER_PROP", "team": "bucks", "game_id": "G6", "market": "Giannis pts", "rationale": "Giannis averages 31 ppg in the last 10 games"},
        ]
        r = detect_same_game_correlated_stack(legs)
        assert r.passed

    def test_single_leg_no_flag(self):
        legs = [{"market_type": "ML", "team": "heat", "game_id": "G7", "market": "Heat ML", "rationale": ""}]
        r = detect_same_game_correlated_stack(legs)
        assert r.passed

    def test_correlated_pairs_in_detail(self):
        legs = [
            {"market_type": "ML",          "team": "wings", "game_id": "G3", "market": "Wings ML",         "rationale": ""},
            {"market_type": "PLAYER_PROP", "team": "wings", "game_id": "G3", "market": "Arike pts over",   "rationale": "heavy favorite — offense running through her"},
        ]
        r = detect_same_game_correlated_stack(legs)
        assert "correlated_pairs" in r.detail
        assert len(r.detail["correlated_pairs"]) == 1


# ─────────────────────────────────────────────────────────────
# Rule 3 — Selective recency consistency
# ─────────────────────────────────────────────────────────────

class TestSelectiveRecencyConsistency:
    def test_no_override_declared_passes(self):
        r = check_recency_consistency({"recency_overrides_history": False})
        assert r.passed

    def test_override_with_full_justification_passes(self):
        candidate = {
            "recency_overrides_history": True,
            "recency_override_reason": {
                "recency_rule_cited": "WOW Rule 14.3: roster change renders prior data stale",
                "stale_data_reason": "Arike's 9-pt game was vs different Connecticut roster",
            },
            "contrary_historical_note": "Arike scored only 9 pts vs Connecticut this season",
        }
        r = check_recency_consistency(candidate)
        assert r.passed

    def test_override_without_rule_citation_fails(self):
        candidate = {
            "recency_overrides_history": True,
            "recency_override_reason": {
                "stale_data_reason": "Different roster now",
            },
        }
        r = check_recency_consistency(candidate)
        assert not r.passed
        assert "SELECTIVE_RECENCY_APPLIED" in r.labels
        assert any("SELECTIVE_RECENCY_APPLIED" in b for b in r.blockers)
        assert "recency_rule_cited" in r.detail["missing_override_fields"]

    def test_override_without_stale_reason_fails(self):
        candidate = {
            "recency_overrides_history": True,
            "recency_override_reason": {
                "recency_rule_cited": "WOW Rule 14.3",
            },
        }
        r = check_recency_consistency(candidate)
        assert not r.passed
        assert "stale_data_reason" in r.detail["missing_override_fields"]

    def test_override_with_empty_reason_dict_fails(self):
        candidate = {
            "recency_overrides_history": True,
            "recency_override_reason": {},
        }
        r = check_recency_consistency(candidate)
        assert not r.passed

    def test_override_with_none_reason_fails(self):
        candidate = {
            "recency_overrides_history": True,
            "recency_override_reason": None,
        }
        r = check_recency_consistency(candidate)
        assert not r.passed

    def test_contrary_note_in_detail(self):
        candidate = {
            "recency_overrides_history": True,
            "recency_override_reason": {},
            "contrary_historical_note": "Arike 9 pts vs Connecticut",
        }
        r = check_recency_consistency(candidate)
        assert r.detail["contrary_historical_note"] == "Arike 9 pts vs Connecticut"

    def test_no_override_key_defaults_to_pass(self):
        r = check_recency_consistency({})
        assert r.passed


# ─────────────────────────────────────────────────────────────
# run_slip_construction_integrity — integration
# ─────────────────────────────────────────────────────────────

class TestRunSlipConstructionIntegrity:
    def test_clean_single_book_no_correlation_passes(self):
        legs = [
            {"book": "draftkings", "market_type": "ML",          "team": "celtics",  "game_id": "G1", "market": "Celtics ML",   "rationale": ""},
            {"book": "draftktkings", "market_type": "PLAYER_PROP", "team": "lakers",  "game_id": "G2", "market": "LeBron pts",  "rationale": "Consistent scorer"},
        ]
        candidate = {"recency_overrides_history": False}
        r = run_slip_construction_integrity(candidate, legs, "Two-leg slip")
        assert r.passed

    def test_wnba_four_leg_cross_book_fails(self):
        """Reproduces the Linemaker WNBA slip from the upload."""
        legs = [
            {"book": "polymarket", "market_type": "ML",          "team": "valkyries", "game_id": "G1", "market": "Valkyries ML",        "rationale": ""},
            {"book": "polymarket", "market_type": "ML",          "team": "wings",     "game_id": "G2", "market": "Wings ML",             "rationale": ""},
            {"book": "hardrock",   "market_type": "PLAYER_PROP", "team": "wings",     "game_id": "G2", "market": "Arike pts over 22.5",  "rationale": "Dallas controlling the game as heavy favorite — offense running through Arike"},
            {"book": "draftkings", "market_type": "PLAYER_PROP", "team": "valkyries", "game_id": "G1", "market": "Harrison reb over",    "rationale": "Golden State winning big → Toronto trailing → garbage-time possessions"},
        ]
        candidate = {"recency_overrides_history": False}
        r = run_slip_construction_integrity(candidate, legs, "Full Four-Leg Ticket — best parlay")
        assert not r.passed
        labels = r.labels
        assert "CROSS_BOOK_PARLAY_ILLUSION" in labels
        assert "SAME_GAME_CORRELATED_STACK" in labels

    def test_no_slip_legs_only_candidate_check(self):
        candidate = {
            "recency_overrides_history": True,
            "recency_override_reason": None,
        }
        r = run_slip_construction_integrity(candidate, slip_legs=None)
        assert not r.passed
        assert "SELECTIVE_RECENCY_APPLIED" in r.labels

    def test_all_three_violations_collected(self):
        legs = [
            {"book": "polymarket", "market_type": "ML",          "team": "wings", "game_id": "G1", "market": "Wings ML",       "rationale": ""},
            {"book": "fanduel",    "market_type": "PLAYER_PROP", "team": "wings", "game_id": "G1", "market": "Arike pts over", "rationale": "heavy favorite, blowout expected"},
        ]
        candidate = {
            "recency_overrides_history": True,
            "recency_override_reason": {},
        }
        r = run_slip_construction_integrity(candidate, legs, "combined parlay")
        assert not r.passed
        assert "CROSS_BOOK_PARLAY_ILLUSION" in r.labels
        assert "SAME_GAME_CORRELATED_STACK" in r.labels
        assert "SELECTIVE_RECENCY_APPLIED" in r.labels
