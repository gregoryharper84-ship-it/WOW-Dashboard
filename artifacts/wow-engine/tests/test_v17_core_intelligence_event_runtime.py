from __future__ import annotations

from v17.core_intelligence_event_runtime import _home_result


def test_core_intelligence_event_label_prefers_final_score_over_team_alias():
    assert _home_result(
        {"home_team": "NYY"},
        {
            "official_winner": "New York Yankees",
            "home_score": 5,
            "away_score": 3,
            "void": False,
        },
    ) == "WIN"


def test_core_intelligence_event_label_records_home_loss_from_score():
    assert _home_result(
        {"home_team": "NYY"},
        {
            "official_winner": "Boston Red Sox",
            "home_score": 2,
            "away_score": 4,
            "void": False,
        },
    ) == "LOSS"


def test_core_intelligence_event_label_fails_closed_on_unresolved_alias_without_scores():
    assert _home_result(
        {"home_team": "NYY"},
        {
            "official_winner": "New York Yankees",
            "home_score": None,
            "away_score": None,
            "void": False,
        },
    ) is None


def test_core_intelligence_event_label_preserves_void():
    assert _home_result(
        {"home_team": "NYY"},
        {
            "official_winner": None,
            "home_score": None,
            "away_score": None,
            "void": True,
        },
    ) == "VOID"
