"""
test_correlation_gate.py — Tests for correlation_gate.py

Covers:
  - Rule 1: existing DIRECT_OVERLAP_PAIRS / STAT_COMPONENT_MAP
  - Rule 3: teammate BLOWOUT_SHARED_RISK / TEAMMATE_USAGE_NEGATIVE
  - Rule 5: Fantasy Score + component stat on same player (new)
  - Power Play blocking on DIRECT_OVERLAP
  - Two-leg vs single-leg edge cases
"""
from __future__ import annotations

import pytest
from gate_engine.correlation_gate import (
    classify_legs,
    run_slip_gate,
    _FANTASY_SCORE_STAT_NAMES,
    _FANTASY_SCORE_COMPONENT_STATS,
)
from gate_engine.labels import CorrelationClass


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _leg(player: str, stat: str, side: str = "MORE", team: str = "") -> dict:
    return {"player": player, "stat": stat, "side": side, "team": team}


# ---------------------------------------------------------------------------
# Smoke — single leg returns LOW_CORRELATION
# ---------------------------------------------------------------------------

class TestSingleLeg:
    def test_single_leg_low_correlation(self):
        result = classify_legs([_leg("LeBron James", "points")])
        assert result["classification"] == CorrelationClass.LOW_CORRELATION.value

    def test_empty_list_low_correlation(self):
        result = classify_legs([])
        assert result["classification"] == CorrelationClass.LOW_CORRELATION.value


# ---------------------------------------------------------------------------
# Rule 1 — existing DIRECT_OVERLAP_PAIRS
# ---------------------------------------------------------------------------

class TestRule1ExistingOverlap:
    def test_points_and_pts_reb_ast(self):
        legs = [_leg("LeBron James", "points"), _leg("LeBron James", "pts+reb+ast")]
        result = classify_legs(legs)
        assert result["classification"] == CorrelationClass.DIRECT_OVERLAP.value
        assert result["block_power_play"] is True

    def test_hits_and_hits_runs_rbi(self):
        legs = [_leg("Shohei Ohtani", "hits"), _leg("Shohei Ohtani", "hits+runs+rbi")]
        result = classify_legs(legs)
        assert result["classification"] == CorrelationClass.DIRECT_OVERLAP.value

    def test_different_players_no_overlap(self):
        legs = [_leg("LeBron James", "points"), _leg("Anthony Davis", "pts+reb+ast")]
        result = classify_legs(legs)
        # Different players — no overlap regardless of stat names
        assert result["classification"] != CorrelationClass.DIRECT_OVERLAP.value


# ---------------------------------------------------------------------------
# Rule 5 — Fantasy Score + component stat, same player
# ---------------------------------------------------------------------------

class TestRule5FantasyScoreComponentOverlap:

    # NBA/WNBA — individual component stats
    def test_nba_points_plus_fantasy_score(self):
        legs = [
            _leg("LeBron James", "points"),
            _leg("LeBron James", "fantasy score"),
        ]
        result = classify_legs(legs)
        assert result["classification"] == CorrelationClass.DIRECT_OVERLAP.value
        assert result["block_power_play"] is True
        assert "Rule5" in result["conflicts"][0]

    def test_nba_rebounds_plus_fantasy_score(self):
        legs = [
            _leg("LeBron James", "rebounds"),
            _leg("LeBron James", "fantasy score"),
        ]
        result = classify_legs(legs)
        assert result["classification"] == CorrelationClass.DIRECT_OVERLAP.value

    def test_nba_assists_plus_fantasy_score(self):
        legs = [
            _leg("LeBron James", "assists"),
            _leg("LeBron James", "fantasy_score"),
        ]
        result = classify_legs(legs)
        assert result["classification"] == CorrelationClass.DIRECT_OVERLAP.value

    def test_nba_steals_plus_fantasy_score(self):
        legs = [
            _leg("Jalen Brunson", "steals"),
            _leg("Jalen Brunson", "fantasy score"),
        ]
        result = classify_legs(legs)
        assert result["classification"] == CorrelationClass.DIRECT_OVERLAP.value

    def test_nba_blocks_plus_fantasy_score(self):
        legs = [
            _leg("Victor Wembanyama", "blocks"),
            _leg("Victor Wembanyama", "fantasy score"),
        ]
        result = classify_legs(legs)
        assert result["classification"] == CorrelationClass.DIRECT_OVERLAP.value

    def test_nba_turnovers_plus_fantasy_score(self):
        legs = [
            _leg("LeBron James", "tov"),
            _leg("LeBron James", "fantasy score"),
        ]
        result = classify_legs(legs)
        assert result["classification"] == CorrelationClass.DIRECT_OVERLAP.value

    # NBA combo stats inside FS
    def test_nba_pts_reb_ast_plus_fantasy_score(self):
        legs = [
            _leg("Nikola Jokic", "pts+reb+ast"),
            _leg("Nikola Jokic", "fantasy score"),
        ]
        result = classify_legs(legs)
        assert result["classification"] == CorrelationClass.DIRECT_OVERLAP.value

    def test_nba_pts_ast_plus_fantasy_score(self):
        legs = [
            _leg("Nikola Jokic", "pts+ast"),
            _leg("Nikola Jokic", "fantasy score"),
        ]
        result = classify_legs(legs)
        assert result["classification"] == CorrelationClass.DIRECT_OVERLAP.value

    # underscore form of stat key (from normalizer output)
    def test_fantasy_score_underscore_form(self):
        legs = [
            _leg("LeBron James", "pts"),
            _leg("LeBron James", "fantasy_score"),
        ]
        result = classify_legs(legs)
        assert result["classification"] == CorrelationClass.DIRECT_OVERLAP.value

    # NFL — passing / rushing / receiving components
    def test_nfl_passing_yards_plus_fantasy_score(self):
        legs = [
            _leg("Patrick Mahomes", "passing yards"),
            _leg("Patrick Mahomes", "fantasy score"),
        ]
        result = classify_legs(legs)
        assert result["classification"] == CorrelationClass.DIRECT_OVERLAP.value

    def test_nfl_rushing_yards_plus_fantasy_score(self):
        legs = [
            _leg("Derrick Henry", "rushing yards"),
            _leg("Derrick Henry", "fantasy score"),
        ]
        result = classify_legs(legs)
        assert result["classification"] == CorrelationClass.DIRECT_OVERLAP.value

    def test_nfl_receptions_plus_fantasy_score(self):
        legs = [
            _leg("Travis Kelce", "receptions"),
            _leg("Travis Kelce", "fantasy score"),
        ]
        result = classify_legs(legs)
        assert result["classification"] == CorrelationClass.DIRECT_OVERLAP.value

    def test_nfl_touchdowns_plus_fantasy_score(self):
        legs = [
            _leg("Patrick Mahomes", "touchdowns"),
            _leg("Patrick Mahomes", "fantasy score"),
        ]
        result = classify_legs(legs)
        assert result["classification"] == CorrelationClass.DIRECT_OVERLAP.value

    def test_nfl_interceptions_plus_fantasy_score(self):
        legs = [
            _leg("Patrick Mahomes", "interceptions"),
            _leg("Patrick Mahomes", "fantasy score"),
        ]
        result = classify_legs(legs)
        assert result["classification"] == CorrelationClass.DIRECT_OVERLAP.value

    # MLB hitter components
    def test_mlb_hits_plus_fantasy_score_hit(self):
        legs = [
            _leg("Shohei Ohtani", "hits"),
            _leg("Shohei Ohtani", "fantasy_score_hit"),
        ]
        result = classify_legs(legs)
        assert result["classification"] == CorrelationClass.DIRECT_OVERLAP.value

    def test_mlb_home_runs_plus_fantasy_score(self):
        legs = [
            _leg("Shohei Ohtani", "home runs"),
            _leg("Shohei Ohtani", "fantasy score"),
        ]
        result = classify_legs(legs)
        assert result["classification"] == CorrelationClass.DIRECT_OVERLAP.value

    def test_mlb_rbi_plus_fantasy_score(self):
        legs = [
            _leg("Shohei Ohtani", "rbi"),
            _leg("Shohei Ohtani", "fantasy_score_hit"),
        ]
        result = classify_legs(legs)
        assert result["classification"] == CorrelationClass.DIRECT_OVERLAP.value

    # MLB pitcher components
    def test_mlb_strikeouts_plus_fantasy_score_pit(self):
        legs = [
            _leg("Spencer Strider", "strikeouts"),
            _leg("Spencer Strider", "fantasy_score_pit"),
        ]
        result = classify_legs(legs)
        assert result["classification"] == CorrelationClass.DIRECT_OVERLAP.value

    def test_mlb_earned_runs_plus_fantasy_score_pit(self):
        legs = [
            _leg("Spencer Strider", "earned runs"),
            _leg("Spencer Strider", "fantasy score pit"),
        ]
        result = classify_legs(legs)
        assert result["classification"] == CorrelationClass.DIRECT_OVERLAP.value

    # Two FS legs on same player (e.g., two-way MLB player)
    def test_fs_hit_and_fs_pit_same_player(self):
        """FANTASY_SCORE_HIT + FANTASY_SCORE_PIT on same player → DIRECT_OVERLAP."""
        legs = [
            _leg("Shohei Ohtani", "fantasy_score_hit"),
            _leg("Shohei Ohtani", "fantasy_score_pit"),
        ]
        result = classify_legs(legs)
        assert result["classification"] == CorrelationClass.DIRECT_OVERLAP.value

    def test_fs_and_fs_same_player(self):
        """Two identical FS legs on same player → DIRECT_OVERLAP."""
        legs = [
            _leg("LeBron James", "fantasy score"),
            _leg("LeBron James", "fantasy_score"),
        ]
        result = classify_legs(legs)
        assert result["classification"] == CorrelationClass.DIRECT_OVERLAP.value


# ---------------------------------------------------------------------------
# Rule 5 NEGATIVE tests — should NOT fire
# ---------------------------------------------------------------------------

class TestRule5NegativeCases:

    def test_different_players_fantasy_score_and_points(self):
        """FS on one player, Points on another → not DIRECT_OVERLAP from Rule 5."""
        legs = [
            _leg("LeBron James", "points"),
            _leg("Anthony Davis", "fantasy score"),
        ]
        result = classify_legs(legs)
        # May be BLOWOUT_SHARED_RISK if same team, but not Rule 5 DIRECT_OVERLAP
        assert "Rule5" not in " ".join(result["conflicts"])

    def test_fantasy_score_with_unrelated_stat(self):
        """FS + a stat not in the component set — falls through to other rules."""
        legs = [
            _leg("LeBron James", "free throws made"),
            _leg("LeBron James", "fantasy score"),
        ]
        result = classify_legs(legs)
        # Free throws made is not in _FANTASY_SCORE_COMPONENT_STATS.
        # Should fall through to the generic same-player same-direction rule
        # (PACE_ENVIRONMENT_POSITIVE), NOT Rule 5 DIRECT_OVERLAP.
        assert "Rule5" not in " ".join(result["conflicts"])
        assert result["classification"] != CorrelationClass.DIRECT_OVERLAP.value

    def test_independent_legs_no_overlap(self):
        """Completely unrelated legs on different players → LOW_CORRELATION."""
        legs = [
            _leg("LeBron James", "points", team="LAL"),
            _leg("Stephen Curry", "assists", team="GSW"),
        ]
        result = classify_legs(legs)
        assert result["classification"] == CorrelationClass.LOW_CORRELATION.value
        assert result["block_power_play"] is False

    def test_fantasy_score_and_non_component_stat_different_sides(self):
        """FS Over + Points Under on same player: not a direct component-overlap pair,
        but same-player different-direction — should not trigger Rule 5."""
        legs = [
            _leg("LeBron James", "free throws made", side="MORE"),
            _leg("LeBron James", "fantasy score", side="LESS"),
        ]
        result = classify_legs(legs)
        assert "Rule5" not in " ".join(result["conflicts"])


# ---------------------------------------------------------------------------
# Power Play blocking
# ---------------------------------------------------------------------------

class TestPowerPlayBlocking:
    def test_rule5_blocks_power_play(self):
        legs = [
            _leg("LeBron James", "points"),
            _leg("LeBron James", "fantasy score"),
        ]
        row = {}
        result = run_slip_gate(row, legs, slip_type="power")
        assert result.get("terminal_label") is not None
        corr = result["gates"]["correlation_gate"]
        assert corr["block_power_play"] is True

    def test_rule5_does_not_block_flex(self):
        """Rule 5 DIRECT_OVERLAP blocks Power Play but does not auto-terminal Flex."""
        legs = [
            _leg("LeBron James", "points"),
            _leg("LeBron James", "fantasy score"),
        ]
        row = {}
        result = run_slip_gate(row, legs, slip_type="flex")
        # terminal_label should NOT be set (Flex is not auto-rejected)
        assert result.get("terminal_label") is None
        corr = result["gates"]["correlation_gate"]
        # But the gate should still flag it
        assert corr["block_power_play"] is True

    def test_already_terminated_row_not_modified(self):
        """run_slip_gate is a no-op when terminal_label is already set."""
        legs = [
            _leg("LeBron James", "points"),
            _leg("LeBron James", "fantasy score"),
        ]
        row = {"terminal_label": "PRIOR_REJECT"}
        result = run_slip_gate(row, legs, slip_type="power")
        assert result["terminal_label"] == "PRIOR_REJECT"


# ---------------------------------------------------------------------------
# Constants sanity
# ---------------------------------------------------------------------------

class TestConstants:
    def test_fantasy_score_stat_names_non_empty(self):
        assert len(_FANTASY_SCORE_STAT_NAMES) >= 4

    def test_fantasy_score_component_stats_non_empty(self):
        assert len(_FANTASY_SCORE_COMPONENT_STATS) >= 10

    def test_points_is_component(self):
        assert "points" in _FANTASY_SCORE_COMPONENT_STATS

    def test_passing_yards_is_component(self):
        assert "passing yards" in _FANTASY_SCORE_COMPONENT_STATS

    def test_strikeouts_is_component(self):
        assert "strikeouts" in _FANTASY_SCORE_COMPONENT_STATS

    def test_fantasy_score_recognized(self):
        assert "fantasy score" in _FANTASY_SCORE_STAT_NAMES

    def test_fantasy_score_underscore_recognized(self):
        assert "fantasy_score" in _FANTASY_SCORE_STAT_NAMES

    def test_fantasy_score_hit_recognized(self):
        assert "fantasy_score_hit" in _FANTASY_SCORE_STAT_NAMES

    def test_fantasy_score_pit_recognized(self):
        assert "fantasy_score_pit" in _FANTASY_SCORE_STAT_NAMES
