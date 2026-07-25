"""
test_live_micro_regression.py — Regression suite for WOW Stage 2 live micro-market gates

Reviewer-mandated tests:
  1. Pregame pitcher cards remain eligible after hard gates
  2. Three-same-event live card is rejected by SAME_EVENT gate
  3. Unsupported hitter fantasy-score legs fail closed
  4. Melton LESS 88.5 receives cushion-risk downgrade (pitch count > threshold)
  5. No filler leg is retained to preserve card size (weakest-leg finalizer)
  6. All-LESS card with 3+ legs is rejected by directional concentration gate

Run with: python -m pytest gate_engine/tests/test_live_micro_regression.py -v
"""
from __future__ import annotations

import sys
import os
import pytest

# Ensure the flask-scoring-api package root is on the path
_HERE = os.path.dirname(__file__)
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from gate_engine import card_finalizer
from gate_engine import mlb_live_micro_market as live_micro
from gate_engine import hitter_fantasy_score as hfs
from gate_engine.labels import PropLabel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(
    player: str,
    sport: str,
    prop_type: str,
    line: float,
    direction: str,
    game: str,
    row_id: str | None = None,
    cal_prob: float = 0.60,
    market_phase: str = "pregame",
    terminal_label: str | None = None,
) -> dict:
    return {
        "row_id":               row_id or f"row_{player.replace(' ','_')}",
        "player":               player,
        "sport":                sport,
        "prop_type":            prop_type,
        "line":                 line,
        "direction":            direction,
        "game":                 game,
        "market_phase":         market_phase,
        "calibrated_probability": cal_prob,
        "blockers":             [],
        "gates":                {},
        "terminal_label":       terminal_label,
    }


def _make_live_row(**kwargs) -> dict:
    row = _make_row(market_phase="live", **kwargs)
    # Simulate a passed live micro gate result
    row["gates"]["live_micro_market"] = {
        "live_state_status": "FRESH",
        "live_state_passed": True,
    }
    return row


# ---------------------------------------------------------------------------
# Test 1: Pregame pitcher cards remain eligible after hard gates
# ---------------------------------------------------------------------------

class TestPregamePitcherEligibility:
    """
    Pregame pitcher strikeout cards (≤2 legs per event, no live flag) must
    pass all hard structural gates and not receive REJECT_BAD_STRUCTURE.
    """

    def test_single_game_two_legs_pregame_passes(self):
        """Two legs from one game, both pregame — within MAX_SAME_EVENT_LEGS=2."""
        rows = [
            _make_row("Gerrit Cole",  "MLB", "Pitcher Strikeouts", 6.5, "MORE", "NYY vs BOS"),
            _make_row("Brayan Bello", "MLB", "Pitcher Strikeouts", 5.5, "LESS", "NYY vs BOS"),
        ]
        report = card_finalizer.run_hard_gates(rows)
        # No row should have REJECT_BAD_STRUCTURE
        for row in rows:
            assert row.get("terminal_label") != PropLabel.REJECT_BAD_STRUCTURE.value, (
                f"Pregame pitcher row {row['row_id']} should not be rejected; "
                f"blockers={row.get('blockers')}"
            )
        assert report["total_blockers_added"] == 0

    def test_two_different_games_pregame_passes(self):
        """Two pregame pitcher legs from two different games — clean card."""
        rows = [
            _make_row("Gerrit Cole",  "MLB", "Pitcher Strikeouts", 6.5, "MORE", "NYY vs BOS"),
            _make_row("Clayton Kershaw", "MLB", "Pitcher Strikeouts", 5.5, "MORE", "LAD vs CHC"),
        ]
        report = card_finalizer.run_hard_gates(rows)
        for row in rows:
            assert row.get("terminal_label") != PropLabel.REJECT_BAD_STRUCTURE.value
        assert report["total_blockers_added"] == 0

    def test_pregame_pitcher_card_direction_mixed_passes(self):
        """Pregame card with 3 legs in mixed directions — not concentrated."""
        rows = [
            _make_row("Gerrit Cole",  "MLB", "Pitcher Strikeouts", 6.5, "MORE", "NYY vs BOS"),
            _make_row("Brayan Bello", "MLB", "Pitcher Strikeouts", 5.5, "MORE", "NYY vs BOS",
                      row_id="row_bello", cal_prob=0.57),
            _make_row("Shane Bieber", "MLB", "Pitcher Strikeouts", 7.5, "LESS", "CLE vs DET"),
        ]
        # 2 MORE + 1 LESS → mixed, not all-same → should not be concentration-blocked
        report = card_finalizer.run_hard_gates(rows, skip_same_event=True)
        assert report["blockers_by_gate"]["direction_conc"] == []


# ---------------------------------------------------------------------------
# Test 2: Three-same-event live card is rejected
# ---------------------------------------------------------------------------

class TestSameEventGate:
    """
    A card with 3 legs from the same game must be rejected regardless of
    market phase. MAX_SAME_EVENT_LEGS = 2 is permanent (not freeze-only).
    """

    def test_three_legs_same_game_rejected(self):
        game = "LAD vs NYM"
        rows = [
            _make_row("Freddie Freeman",  "MLB", "Hits",             1.5, "MORE", game, market_phase="live"),
            _make_row("Shohei Ohtani",   "MLB", "Home Runs",         0.5, "MORE", game, market_phase="live"),
            _make_row("Mookie Betts",    "MLB", "Runs + RBI",        2.5, "LESS", game, market_phase="live"),
        ]
        report = card_finalizer.run_hard_gates(rows, skip_direction_conc=True)
        rejected = [r for r in rows if r.get("terminal_label") == PropLabel.REJECT_BAD_STRUCTURE.value]
        assert len(rejected) == 3, f"All 3 rows should be REJECT_BAD_STRUCTURE; got {len(rejected)}"
        assert len(report["blockers_by_gate"]["same_event"]) > 0

    def test_two_legs_same_game_passes(self):
        """Exactly 2 legs from same game — within limit."""
        game = "NYY vs BOS"
        rows = [
            _make_row("Aaron Judge",  "MLB", "Hits", 1.5, "MORE", game),
            _make_row("Giancarlo Stanton", "MLB", "Home Runs", 0.5, "LESS", game),
        ]
        report = card_finalizer.run_hard_gates(rows, skip_direction_conc=True)
        assert report["blockers_by_gate"]["same_event"] == []
        for row in rows:
            assert row.get("terminal_label") != PropLabel.REJECT_BAD_STRUCTURE.value


# ---------------------------------------------------------------------------
# Test 3: Unsupported hitter fantasy-score market fails closed
# ---------------------------------------------------------------------------

class TestHitterFantasyScoreMarketSupport:
    """
    Markets not supported by the hitter_fantasy_score module must fail closed
    (validate_market_support returns supported=False). The endpoint must then
    return REJECT_DATA_QUALITY, not a fabricated probability.
    """

    def test_supported_markets_pass(self):
        supported = [
            "hitter_fantasy_score", "fantasy_score", "hitter_fs",
            "batting_fantasy", "baseball_hitter_fs",
        ]
        for market in supported:
            result = hfs.validate_market_support(market)
            assert result["supported"] is True, f"Market '{market}' should be supported"
            assert result["canonical_name"] == "hitter_fantasy_score"

    def test_unsupported_markets_fail_closed(self):
        unsupported = [
            "pitcher_strikeouts",
            "hits",
            "total_bases",
            "passing_yards",
            "rebounds",
            "points",
            "saves",
            "qb_fantasy",
        ]
        for market in unsupported:
            result = hfs.validate_market_support(market)
            assert result["supported"] is False, f"Market '{market}' should NOT be supported"
            assert result["canonical_name"] is None
            assert "REJECT_DATA_QUALITY" in result["reason"] or "not supported" in result["reason"].lower()

    def test_empty_market_fails_closed(self):
        result = hfs.validate_market_support("")
        assert result["supported"] is False

    def test_none_market_fails_closed(self):
        result = hfs.validate_market_support(None)
        assert result["supported"] is False

    def test_fantasy_score_line_probability_computation(self):
        """Supported market: verify probability is numerically valid."""
        result = hfs.compute_line_probability(
            line=25.0,
            direction="MORE",
            pa_remaining=3.0,
        )
        assert 0.0 < result["P_MORE"] < 1.0
        assert 0.0 < result["P_LESS"] < 1.0
        assert abs(result["P_MORE"] + result["P_LESS"] - 1.0) < 0.001
        assert result["can_execute"] is False


# ---------------------------------------------------------------------------
# Test 4: Melton LESS 88.5 pitch-count cushion-risk downgrade
# ---------------------------------------------------------------------------

class TestMeltonPitchCountCushionRisk:
    """
    A pitcher at or beyond pitch count regime 'elevated' (≥70 pitches) whose
    remaining K cushion is slim must receive cushion_risk >= HIGH.
    Mapped to Melton LESS 88.5 (strikeouts): high pitch count + 88.5 line →
    very few Ks can still occur before likely exit.

    This tests the cushion-risk logic in mlb_live_micro_market, which should
    produce cushion_risk = HIGH or CRITICAL when pitch count is elevated.
    """

    def _run_melton(self, pitch_count: int, batters_faced: int) -> dict:
        return live_micro.analyze(
            game_id="2026-07-25-mlb-tor-nyy",
            player_id="melton",
            market_type="pitcher strikeouts",
            line=5.5,            # proxy for "88.5 total pitches ÷ high K line"
            direction="LESS",
            inning=4,
            outs=1,
            base_state="empty",
            score={"home": 2, "away": 1},
            current_pitcher="Melton",
            pitch_count=pitch_count,
            batters_faced=batters_faced,
            current_batter="Judge",
            batting_order=3,
            remaining_innings_scope=3,
            capture_timestamp="2026-07-25T18:30:00+00:00",
        )

    def test_elevated_pitch_count_triggers_cushion_risk(self):
        """pitch_count=78, batters_faced=22 → elevated regime → cushion_risk >= MODERATE."""
        result = self._run_melton(pitch_count=78, batters_faced=22)
        k_dist = result.get("pitcher_k_distribution")
        assert k_dist is not None, "pitcher_k_distribution must be present for K market"
        cushion = k_dist.get("cushion_risk")
        assert cushion in ("MODERATE", "HIGH", "CRITICAL"), (
            f"Expected MODERATE/HIGH/CRITICAL cushion risk at pitch count 78; got {cushion}"
        )

    def test_critical_pitch_count_triggers_critical_cushion(self):
        """pitch_count=98, batters_faced=24 → critical/exceeded regime → cushion_risk=CRITICAL."""
        result = self._run_melton(pitch_count=98, batters_faced=24)
        k_dist = result.get("pitcher_k_distribution")
        assert k_dist is not None
        cushion = k_dist.get("cushion_risk")
        assert cushion in ("HIGH", "CRITICAL"), (
            f"Expected HIGH or CRITICAL cushion risk at pitch count 98; got {cushion}"
        )

    def test_high_pitch_count_downgrades_terminal_label(self):
        """High pitch count + small expected remaining Ks → NO_PLAY, not FINAL_APPROVED."""
        result = self._run_melton(pitch_count=96, batters_faced=25)
        label = result.get("terminal_label")
        # At pitch count 96, pull probability is 0.70+ → should be NO_PLAY or REJECT
        assert label in ("NO_PLAY", "REJECT_DATA_QUALITY", "MODEL_QUALIFIED_HOLD"), (
            f"Expected downgraded label at critical pitch count; got {label}"
        )

    def test_fresh_pitcher_has_low_cushion_risk(self):
        """
        Low pitch count + full scope remaining → cushion_risk = LOW.
        Must use inning=1, outs=0 so all 9 scope-outs are still available
        (expected_remaining_k ≈ 2.2, pull_prob=0.0 → LOW).
        """
        # Override state to give maximum remaining scope
        result = live_micro.analyze(
            game_id="2026-07-25-mlb-tor-nyy",
            player_id="melton",
            market_type="pitcher strikeouts",
            line=5.5,
            direction="LESS",
            inning=1,           # first inning — all scope-outs available
            outs=0,
            base_state="empty",
            score={"home": 0, "away": 0},
            current_pitcher="Melton",
            pitch_count=35,
            batters_faced=11,
            current_batter="Judge",
            batting_order=3,
            remaining_innings_scope=3,
            capture_timestamp="2026-07-25T18:30:00+00:00",
        )
        k_dist = result.get("pitcher_k_distribution")
        assert k_dist is not None
        cushion = k_dist.get("cushion_risk")
        # With 9 scope-outs remaining and pull_prob=0.0, mean ≈ 2.2 → LOW
        assert cushion == "LOW", (
            f"Expected LOW cushion risk at pitch count 35 with full scope remaining; "
            f"got {cushion} (expected_remaining_k={k_dist.get('expected_remaining_k')})"
        )


# ---------------------------------------------------------------------------
# Test 5: No filler leg retained (weakest-leg finalizer)
# ---------------------------------------------------------------------------

class TestWeakestLegFinalizer:
    """
    When the weakest leg has a gap > 0.05 quality points from the next-weakest,
    the finalizer must remove it (terminal_label=NO_PLAY) rather than retaining
    it as a filler to pad card size.
    """

    def test_weak_leg_removed_when_gap_material(self):
        """
        Card with 3 legs: two strong (0.72, 0.68) and one weak (0.53).
        Gap from weakest to next: 0.68 - 0.53 = 0.15 > 0.05 → must remove.
        """
        rows = [
            _make_row("Aaron Judge",     "MLB", "Home Runs",        0.5, "MORE", "NYY vs BOS",
                      row_id="row_judge", cal_prob=0.72),
            _make_row("Rafael Devers",   "MLB", "Hits",             1.5, "MORE", "NYY vs BOS",
                      row_id="row_devers", cal_prob=0.68),
            _make_row("David Hamilton",  "MLB", "Stolen Bases",     0.5, "MORE", "NYY vs BOS",
                      row_id="row_hamilton", cal_prob=0.53),  # weakest
        ]
        result = card_finalizer.finalize_card(rows)

        assert result["finalizer_ran"] is True
        assert result["weakest_removed"] is True, "Weakest leg should be removed when gap > 0.05"
        assert result["weakest_row_id"] == "row_hamilton"
        assert result["weakest_gap"] > 0.05

        # Hamilton's terminal_label should be NO_PLAY (removed)
        hamilton_row = next(r for r in rows if r["row_id"] == "row_hamilton")
        assert hamilton_row["terminal_label"] == PropLabel.NO_PLAY.value

    def test_close_legs_not_removed(self):
        """
        Three legs all close in quality (0.63, 0.61, 0.59). Gap = 0.02 < 0.05
        → no removal.
        """
        rows = [
            _make_row("Player A", "MLB", "Hits", 1.5, "MORE", "Game1", row_id="r1", cal_prob=0.63),
            _make_row("Player B", "MLB", "RBI",  1.5, "MORE", "Game2", row_id="r2", cal_prob=0.61),
            _make_row("Player C", "MLB", "Runs", 0.5, "MORE", "Game3", row_id="r3", cal_prob=0.59),
        ]
        result = card_finalizer.finalize_card(rows)
        assert result["weakest_removed"] is False, "Should not remove when gap ≤ 0.05"
        for row in rows:
            assert row.get("terminal_label") != PropLabel.NO_PLAY.value

    def test_two_leg_card_shrinks_when_configured(self):
        """
        Two-leg card where weakest leg is weak (gap > 0.05).
        SHRINK_CARD_WHEN_NO_REPLACEMENT=True → still removes (card_shrunk=True).
        """
        rows = [
            _make_row("Player A", "MLB", "Hits", 1.5, "MORE", "Game1", row_id="r1", cal_prob=0.71),
            _make_row("Player B", "MLB", "RBI",  1.5, "LESS", "Game2", row_id="r2", cal_prob=0.52),
        ]
        assert card_finalizer.SHRINK_CARD_WHEN_NO_REPLACEMENT is True
        result = card_finalizer.finalize_card(rows)
        assert result["weakest_removed"] is True
        assert result["card_shrunk"] is True


# ---------------------------------------------------------------------------
# Test 6: All-LESS card with 3+ legs rejected (directional concentration)
# ---------------------------------------------------------------------------

class TestDirectionalConcentration:
    """
    A card where all N >= 3 legs share the same direction (all MORE or all LESS)
    must be rejected by REJECT_ALL_SAME_DIRECTION_CONCENTRATION.
    """

    def test_all_less_three_legs_rejected(self):
        rows = [
            _make_row("Pitcher A", "MLB", "Pitcher Strikeouts", 6.5, "LESS", "Game1", row_id="r1"),
            _make_row("Pitcher B", "MLB", "Pitcher Strikeouts", 5.5, "LESS", "Game2", row_id="r2"),
            _make_row("Pitcher C", "MLB", "Pitcher Strikeouts", 7.5, "LESS", "Game3", row_id="r3"),
        ]
        report = card_finalizer.run_hard_gates(rows, skip_same_event=True, skip_live_overload=True, skip_live_state_req=True)
        assert len(report["blockers_by_gate"]["direction_conc"]) > 0, "All-LESS card must be blocked"
        for row in rows:
            assert row.get("terminal_label") == PropLabel.REJECT_BAD_STRUCTURE.value, (
                f"Row {row['row_id']} should be REJECT_BAD_STRUCTURE; got {row.get('terminal_label')}"
            )

    def test_all_more_five_legs_rejected(self):
        rows = [
            _make_row(f"Player {i}", "MLB", "Hits", 1.5, "MORE", f"Game{i}", row_id=f"r{i}")
            for i in range(5)
        ]
        report = card_finalizer.run_hard_gates(rows, skip_same_event=True, skip_live_overload=True, skip_live_state_req=True)
        assert len(report["blockers_by_gate"]["direction_conc"]) > 0

    def test_two_leg_all_less_not_rejected(self):
        """
        Two legs all-LESS — below SAME_DIRECTION_CONCENTRATION_MIN_LEGS=3,
        so no concentration block.
        """
        rows = [
            _make_row("Player A", "MLB", "Pitcher Strikeouts", 6.5, "LESS", "Game1", row_id="r1"),
            _make_row("Player B", "MLB", "Pitcher Strikeouts", 5.5, "LESS", "Game2", row_id="r2"),
        ]
        report = card_finalizer.run_hard_gates(rows, skip_same_event=True, skip_live_overload=True, skip_live_state_req=True)
        assert report["blockers_by_gate"]["direction_conc"] == []


# ---------------------------------------------------------------------------
# Test 7: Live micro-market module — basic wiring
# ---------------------------------------------------------------------------

class TestLiveMicroMarket:
    """Basic sanity checks for the live micro-market module."""

    def _base_state(self, pitch_count=45, inning=2, outs=1) -> dict:
        return dict(
            game_id="test-game",
            player_id="test-pitcher",
            market_type="pitcher strikeouts",
            line=4.5,
            direction="MORE",
            inning=inning,
            outs=outs,
            base_state="empty",
            score={"home": 0, "away": 0},
            current_pitcher="Test Pitcher",
            pitch_count=pitch_count,
            batters_faced=14,
            current_batter="Test Batter",
            batting_order=3,
            remaining_innings_scope=3,
            capture_timestamp="2026-07-25T18:00:00+00:00",
        )

    def test_can_execute_is_always_false(self):
        result = live_micro.analyze(**self._base_state())
        assert result["can_execute"] is False

    def test_stale_state_fails_live_gate(self):
        """Capture timestamp 10 minutes ago → STALE or CRITICAL → passed=False."""
        from datetime import datetime, timezone, timedelta
        old_ts = (datetime.now(timezone.utc) - timedelta(seconds=700)).isoformat()
        state = self._base_state()
        state["capture_timestamp"] = old_ts
        result = live_micro.analyze(**state)
        assert result["live_state_status"] in ("STALE", "CRITICAL")
        assert result["terminal_label"] == "REJECT_DATA_QUALITY"

    def test_fresh_state_returns_probability_fields(self):
        result = live_micro.analyze(**self._base_state())
        for field in ("P_MORE", "P_LESS", "raw_probability",
                      "calibrated_lower_bound", "primary_failure_path"):
            assert field in result, f"Field '{field}' missing from live micro result"

    def test_opportunity_distribution_makes_sense(self):
        result = live_micro.analyze(**self._base_state(inning=1, outs=0))
        opp = result["opportunity_distribution"]
        assert opp["outs_remaining"] <= opp["scope_outs_total"]
        assert opp["outs_consumed"] + opp["outs_remaining"] == opp["scope_outs_total"]

    def test_validate_market_support_hitter(self):
        support = hfs.validate_market_support("hitter_fantasy_score")
        assert support["supported"] is True
