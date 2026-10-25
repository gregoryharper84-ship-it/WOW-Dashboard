"""NFL ESPN independent final-score research canary: no live network fixtures."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v17.multi_source_historical_collector import CollectionBlocked
from v17.nfl_espn_independent_parity_1933 import compare_espn_date

SCHEDULE = (
    "season,week,game_id,game_type,gameday,gametime,home_team,away_team,home_score,away_score\n"
    "2025,1,2025_01_DAL_PHI,REG,2025-09-07,13:00,PHI,DAL,24,17\n"
)
GAME = {
    "id": "401-test-final",
    "date": "2025-09-07T17:00:00Z",
    "season": {"year": 2025},
    "week": {"number": 1},
    "status": {"type": {"completed": True, "state": "post"}},
    "competitions": [{"competitors": [
        {"homeAway": "home", "team": {"abbreviation": "PHI"}, "score": "24"},
        {"homeAway": "away", "team": {"abbreviation": "DAL"}, "score": "17"},
    ]}],
}


def compare(games=None):
    return compare_espn_date(
        schedule_csv=SCHEDULE.encode(),
        espn_json=json.dumps({"events": games if games is not None else [GAME]}).encode(),
    )


def test_exact_independent_espn_final_game_score_and_time_matches():
    result = compare()
    assert result["status"] == "MATCHED"
    assert result["games_in_scope"] == 1
    assert result["matched_final_games"] == 1
    assert result["score_conflict_games"] == 0
    assert result["matched_event_ids"] == [{"nflverse_game_id": "2025_01_DAL_PHI", "espn_event_id": "401-test-final"}]
    assert not result["full_season_parity_established"]
    assert not result["can_execute"]
    assert not result["probability_publishable"]
    assert len(result["source_sha256"]) == 64


def test_espn_score_conflict_must_remain_unresolved():
    event = json.loads(json.dumps(GAME))
    event["competitions"][0]["competitors"][0]["score"] = "25"
    result = compare([event])
    assert result["status"] == "PARTIAL_HOLD"
    assert result["score_conflict_games"] == 1
    assert result["matched_final_games"] == 0


def test_espn_nonfinal_or_kickoff_mismatch_does_not_match():
    event = json.loads(json.dumps(GAME))
    event["status"]["type"]["completed"] = False
    assert compare([event])["missing_or_unfinalized_games"] == 1
    event["status"]["type"]["completed"] = True
    event["date"] = "2025-09-07T22:00:00Z"
    assert compare([event])["missing_or_unfinalized_games"] == 1


def test_espn_duplicate_event_id_or_pair_is_not_accepted():
    with pytest.raises(CollectionBlocked, match="NFL_ESPN_DUPLICATE_EVENT_IDS"):
        compare([GAME, dict(GAME)])
    different = json.loads(json.dumps(GAME))
    different["id"] = "401-different"
    with pytest.raises(CollectionBlocked, match="NFL_ESPN_EVENT_IDENTITY_AMBIGUOUS"):
        compare([GAME, different])


def test_malformed_espn_event_identity_rejected():
    event = json.loads(json.dumps(GAME))
    event["season"] = {}
    with pytest.raises(CollectionBlocked, match="NFL_ESPN_CHRONOLOGY_INCOMPLETE"):
        compare([event])
    event = json.loads(json.dumps(GAME))
    event["competitions"][0]["competitors"][1]["homeAway"] = "home"
    with pytest.raises(CollectionBlocked, match="NFL_ESPN_SIDES_INVALID"):
        compare([event])


def test_wrong_season_week_or_team_cannot_be_considered_verified():
    for field, val in (("season", {"year": 2024}), ("week", {"number": 2})):
        event = json.loads(json.dumps(GAME))
        event[field] = val
        assert compare([event])["status"] == "PARTIAL_HOLD"
    event = json.loads(json.dumps(GAME))
    event["competitions"][0]["competitors"][0]["team"]["abbreviation"] = "NYG"
    assert compare([event])["status"] == "PARTIAL_HOLD"


def test_unparseable_or_empty_espn_response_fails_closed():
    with pytest.raises(CollectionBlocked, match="NFL_ESPN_JSON_INVALID"):
        compare_espn_date(schedule_csv=SCHEDULE.encode(), espn_json=b"not-json")
    with pytest.raises(CollectionBlocked, match="NFL_ESPN_RESPONSE_SCHEMA_INVALID"):
        compare_espn_date(schedule_csv=SCHEDULE.encode(), espn_json=b"{}")


def test_scoping_cannot_be_changed_to_arbitrary_dates():
    with pytest.raises(CollectionBlocked, match="NFL_ESPN_PILOT_DATE_UNSUPPORTED"):
        compare_espn_date(schedule_csv=SCHEDULE.encode(), espn_json=b"{}", game_day="2025-09-08")


def test_two_additional_allowlisted_week_dates_are_scoped_and_independent():
    import copy
    for week, day in ((2, "2025-09-14"), (3, "2025-09-21")):
        # Synthetic game values are only fixture inputs, not real NFL results.
        schedule = SCHEDULE.replace("2025,1,2025_01_DAL_PHI,REG,2025-09-07",
                                    f"2025,{week},2025_{week:02d}_DAL_PHI,REG,{day}")
        event = copy.deepcopy(GAME)
        event["id"] = f"synthetic-{week}"
        event["date"] = day + "T17:00:00Z"
        event["week"] = {"number": week}
        result = compare_espn_date(schedule_csv=schedule.encode(),
                                   espn_json=json.dumps({"events": [event]}).encode(), game_day=day)
        assert result["status"] == "MATCHED"
        assert result["matched_final_games"] == 1
        assert result["date"] == day
        assert result["source_url"].endswith("dates=" + day.replace("-", ""))
        assert result["full_season_parity_established"] is False


def test_other_dates_never_pass_through_source_allowlist():
    from v17.nfl_espn_independent_parity_1933 import fetch_espn_historical_scoreboard
    for day in ("2025-09-08", "2026-09-14", "../2025-09-14"):
        with pytest.raises(CollectionBlocked, match="NFL_ESPN_PILOT_DATE_UNSUPPORTED"):
            fetch_espn_historical_scoreboard(game_day=day)
