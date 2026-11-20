"""All 13 sports have named script research hypotheses, never fitted likelihoods."""
import pytest

from v17.nightly_multiscout import (
    _team_event_candidate,
    game_script_evidence_status,
    game_scripts,
)


@pytest.mark.parametrize(("sport_key", "required"), [
    ("americanfootball_nfl", "TURNOVER_SHORT_FIELDS"),
    ("americanfootball_ncaaf", "BACKUP_QB"),
    ("basketball_nba", "STAR_FOUL_TROUBLE"),
    ("basketball_wnba", "HOT_THREE_POINT_VARIANCE"),
    ("basketball_ncaab", "PACE_DOWN"),
    ("baseball_mlb", "BULLPEN_COLLAPSE"),
    ("icehockey_nhl", "GOALIE_DOMINANCE"),
    ("soccer_epl", "DRAW_SCRIPT"),
    ("tennis_atp", "TIEBREAK_HEAVY"),
    ("golf_pga", "WEATHER_WAVE_ADVANTAGE"),
    ("mma_mixed_martial_arts", "EARLY_FINISH"),
    ("boxing_heavyweight", "JUDGES_DECISION"),
    ("cricket_international", "POWERPLAY_WICKETS"),
])
def test_13_sports_have_applicable_research_scenarios_not_probabilities(sport_key, required):
    scripts = game_scripts(sport_key)
    assert required in scripts
    assert len(scripts) >= 3
    assert game_script_evidence_status(sport_key) == "HYPOTHESES_ONLY_FREQUENCIES_UNFITTED"


@pytest.mark.parametrize("sport_key", ("golf_pga", "mma_mixed_martial_arts", "boxing_heavyweight", "tennis_wta", "cricket_international"))
def test_individual_and_non_overtime_sports_skip_generic_football_assumptions(sport_key):
    scripts = game_scripts(sport_key)
    assert "ONE_SCORE_OR_ONE_POSSESSION_CLOSE_GAME" not in scripts
    assert "OVERTIME_OR_EXTRA_PERIOD_EXTENSION" not in scripts


def test_discovery_handoff_explicitly_requires_future_evaluation_not_certification():
    event = {"sport_key": "soccer_epl", "official_event_id": "fixture-1"}
    candidate = _team_event_candidate(event, game_scripts("soccer_epl"), [], [])
    assert candidate["game_script_evaluation_required"] is True
    assert candidate["game_script_evaluation_status"] == "HYPOTHESES_ONLY_FREQUENCIES_UNFITTED"
    assert candidate["game_script_frequency_estimated"] is False
    assert candidate["script_probability_publishable"] is False
    assert candidate["game_scripts_are_evidence_only"] is True
    assert candidate["research_ceiling"] == "RESEARCH_INTEREST"


def test_unknown_sport_is_explicitly_uncovered_without_probability():
    assert game_script_evidence_status("unrecognized_sport") == "NO_SPORT_SPECIFIC_GAME_SCRIPTS"
    assert "FAVORITE_CONTROL" in game_scripts("unrecognized_sport")
