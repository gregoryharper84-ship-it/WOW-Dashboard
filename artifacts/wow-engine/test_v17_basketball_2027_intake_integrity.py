"""Adversarial regression for real SportsDataverse season/finality/score intake.

Provenance: source repair stacked after #2476; this does not fit or certify
any basketball model, modify a terminal reducer, or touch a production DB.
"""
from copy import deepcopy

import pytest

import basketball_event_hydration_runtime as hydration


def _final_row():
    # NBA October 2026 belongs to the source release ending in 2027.
    # The upstream CSV 'season' column is optional in the source contract.
    return {
        "game_id": "401", "game_date": "2026-10-27",
        "game_date_time": "2026-10-27T19:00:00-04:00",
        "home_id": "7", "away_id": "8",
        "home_score": "111", "away_score": "109",
        "status_type_completed": "true",
        "status_type_description": "Final",
        "status_type_name": "STATUS_FINAL",
        "season_type": "2",
        "_wow_source_observed_at": "2026-10-28T02:00:00Z",
    }


def _normalized(row, at="2026-10-28T03:00:00Z"):
    return hydration.normalize_sportsdataverse_game(
        row, "NBA", at, source_release_year=2027
    )


def test_missing_season_on_october_final_inherits_source_release_year():
    row = _final_row()
    assert "season" not in row
    result = _normalized(row)
    assert result is not None
    assert result["season"] == 2027
    assert result["game_date"] == "2026-10-27"
    assert result["source_endpoint"].endswith("nba_schedule_2027.csv")
    assert result["source_retrieved_at"] == row["_wow_source_observed_at"]
    assert result["settled"] is True


@pytest.mark.parametrize("season", ["2027", "2027.0"])
def test_explicit_integral_source_season_is_preserved(season):
    row = dict(_final_row(), season=season)
    assert _normalized(row)["season"] == 2027


@pytest.mark.parametrize("season", ["2027.25", "Infinity", "NaN", "not-a-season"])
def test_malformed_or_nonintegral_season_holds(season):
    assert _normalized(dict(_final_row(), season=season)) is None


@pytest.mark.parametrize("field,value", [
    ("status_type_description", "Postponed"),
    ("status_type_description", "Postponement"),
    ("status_type_name", "STATUS_POSTPONED"),
    ("status_type_description", "Cancelled"),
    ("status_type_description", "No Contest"),
    ("status_type_name", "STATUS_NO_CONTEST"),
    ("status_type_description", "No-Contest"),
])
def test_completed_flag_does_not_override_explicit_nonsettled_state(field, value):
    # Both 'completed=true' and a no-result status can coexist in malformed
    # upstream data. It must never train on a postponed/cancelled event.
    row = dict(_final_row(), **{field: value})
    assert row["status_type_completed"] == "true"
    assert _normalized(row) is None


@pytest.mark.parametrize("score", [
    "110.5", "-1", "Infinity", "NaN", "not-a-score", None
])
def test_fractional_negative_nonfinite_or_missing_score_holds(score):
    for side in ("home_score", "away_score"):
        row = dict(_final_row(), **{side: score})
        assert _normalized(row) is None


def test_integer_decimal_scores_are_legitimate_and_not_truncated():
    row = dict(_final_row(), home_score="111.0", away_score="109.000")
    result = _normalized(row)
    assert result is not None
    assert (result["home_score"], result["away_score"], result["home_win"]) == (
        111, 109, True
    )


def test_snapshot_time_and_season_type_fail_closed():
    row = deepcopy(_final_row())
    assert _normalized(row, "2026-10-28T01:59:59Z") is None
    row["season_type"] = "3"  # Postseason cannot enter regular-season model.
    assert _normalized(row) is None
