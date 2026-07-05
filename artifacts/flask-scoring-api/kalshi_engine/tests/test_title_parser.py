"""
Regression tests for kalshi_engine.llp_bridge.title_parser.parse_opponent_team.

Root-cause context: Kalshi's `no_sub_title` field on the MLB/WNBA
winner-market series duplicates `yes_sub_title` on every ticker observed
live on 2026-07-05 — it does NOT name the opposing team. This was
discovered live-testing /wow/kalshi/sports/live-board, where every row's
sportsbook consensus lookup was FAILED because team_a==team_b was being
passed to the Odds API. These tests lock in the title-parsing fix and
its safe-fallback (never guess) behavior.
"""
from kalshi_engine.llp_bridge.title_parser import parse_opponent_team


class TestParseOpponentTeam:
    def test_standard_title_yes_is_first_team(self):
        assert parse_opponent_team("Toronto vs San Francisco Winner?", "Toronto") == "San Francisco"

    def test_standard_title_yes_is_second_team(self):
        assert parse_opponent_team("Toronto vs San Francisco Winner?", "San Francisco") == "Toronto"

    def test_multi_word_team_names(self):
        assert parse_opponent_team("Colorado vs Los Angeles D Winner?", "Los Angeles D") == "Colorado"
        assert parse_opponent_team("Colorado vs Los Angeles D Winner?", "Colorado") == "Los Angeles D"

    def test_case_insensitive_vs_and_winner(self):
        assert parse_opponent_team("Boston VS Chicago WS winner?", "Boston") == "Chicago WS"

    def test_missing_title_returns_none(self):
        assert parse_opponent_team(None, "Toronto") is None
        assert parse_opponent_team("", "Toronto") is None

    def test_missing_yes_team_returns_none(self):
        assert parse_opponent_team("Toronto vs San Francisco Winner?", None) is None
        assert parse_opponent_team("Toronto vs San Francisco Winner?", "") is None

    def test_title_not_matching_pattern_returns_none_never_guesses(self):
        assert parse_opponent_team("Total runs scored: Over 8.5", "Toronto") is None
        assert parse_opponent_team("Some other market title", "Toronto") is None

    def test_yes_team_not_in_title_returns_none_never_guesses(self):
        # yes_team doesn't match either parsed team exactly -> never guess.
        assert parse_opponent_team("Toronto vs San Francisco Winner?", "Chicago") is None

    def test_never_returns_same_team_as_yes_team(self):
        # Regression guard for the root-cause bug: opponent must never equal
        # the yes-side team (which is what the broken no_sub_title field did).
        result = parse_opponent_team("Toronto vs San Francisco Winner?", "Toronto")
        assert result != "Toronto"
        assert result == "San Francisco"
