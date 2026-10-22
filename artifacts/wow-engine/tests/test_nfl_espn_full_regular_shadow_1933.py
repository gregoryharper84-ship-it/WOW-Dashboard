"""Fixture tests for complete-2025 ESPN vs pinned-nflverse score parity.

Synthetic schedule/game scores here are only test data; they do not assert
that any particular real team won a game.
"""
from __future__ import annotations

import copy
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v17.multi_source_historical_collector import CollectionBlocked
from v17.nfl_espn_full_regular_shadow_1933 import (
    compare_2025_regular_season, fetch_espn_regular_week, WEEKS,
    ESPN_WEEK_URLS,
)

TEAMS = (
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE",
    "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC",
    "LA", "LAC", "LV", "MIA", "MIN", "NE", "NO", "NYG",
    "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS",
)
FIELDS = (
    "season,week,game_id,game_type,gameday,gametime,"
    "home_team,away_team,home_score,away_score"
)


def fixture_corpus():
    schedule_rows = [FIELDS]
    weeks = {}
    new_york = ZoneInfo("America/New_York")
    for week in WEEKS:
        game_date = date(2025, 9, 7) + timedelta(days=7 * (week - 1))
        kickoff = datetime.combine(game_date, datetime.min.time()).replace(
            hour=13, tzinfo=new_york
        ).astimezone(ZoneInfo("UTC")).isoformat()
        events = []
        for n in range(16 if week in (1, 2) else 15):
            away, home = TEAMS[2 * n], TEAMS[2 * n + 1]
            gid = f"2025_{week:02d}_{away}_{home}"
            schedule_rows.append(
                f"2025,{week},{gid},REG,{game_date.isoformat()},13:00,"
                f"{home},{away},24,17"
            )
            events.append({
                "id": f"synthetic-{week}-{n}", "date": kickoff,
                "season": {"year": 2025}, "week": {"number": week},
                "status": {"type": {"completed": True}},
                "competitions": [{"competitors": [
                    {"homeAway": "home", "team": {"abbreviation": home}, "score": "24"},
                    {"homeAway": "away", "team": {"abbreviation": away}, "score": "17"},
                ]}],
            })
        weeks[week] = json.dumps({"events": events}).encode("utf-8")
    return ("\n".join(schedule_rows) + "\n").encode(), weeks


def test_complete_research_parity_exactly_272_and_only_18_regular_weeks():
    schedule, weekly = fixture_corpus()
    report = compare_2025_regular_season(schedule_csv=schedule, weekly_payloads=weekly)
    assert report["status"] == "MATCHED"
    assert report["schedule_games"] == 272
    assert report["matched_games"] == 272
    assert report["unresolved_games"] == 0
    assert len(report["crosswalk"]) == 272
    assert len(report["weeks"]) == 18
    assert report["independent_full_regular_season_results_checked"] is True
    assert all(w["matched"] == w["schedule_games"] for w in report["weeks"])
    for flag in ("training_ready", "storage_write_authorized", "model_certified",
                 "probability_publishable", "attestation_verified", "can_execute"):
        assert report[flag] is False


def test_one_mismatched_score_fails_closed_and_preserves_other_valid_games():
    schedule, weekly = fixture_corpus()
    weekly = dict(weekly)
    body = json.loads(weekly[8])
    body["events"][0]["competitions"][0]["competitors"][0]["score"] = "25"
    weekly[8] = json.dumps(body).encode()
    report = compare_2025_regular_season(schedule_csv=schedule, weekly_payloads=weekly)
    assert report["status"] == "PARTIAL_HOLD"
    assert report["matched_games"] == 271
    assert report["unresolved_games"] == 1
    assert report["problems"][0]["issue"] == "FINAL_SCORE_CONFLICT"
    assert report["model_certified"] is False


def test_unfinalized_game_rejected_not_promoted_as_settled():
    schedule, weekly = fixture_corpus()
    weekly = dict(weekly)
    body = json.loads(weekly[4])
    body["events"][0]["status"]["type"]["completed"] = False
    weekly[4] = json.dumps(body).encode()
    report = compare_2025_regular_season(schedule_csv=schedule, weekly_payloads=weekly)
    assert report["status"] == "PARTIAL_HOLD"
    assert report["matched_games"] == 271
    assert report["problems"][0]["issue"] == "MISSING_OR_UNFINALIZED"


def test_duplicate_espn_event_across_weeks_is_typed_failure():
    schedule, weekly = fixture_corpus()
    weekly = dict(weekly)
    body = json.loads(weekly[10])
    body["events"][0]["id"] = "synthetic-9-0"
    weekly[10] = json.dumps(body).encode()
    with pytest.raises(CollectionBlocked, match="NFL_ESPN_FULL_EVENT_REUSED_ACROSS_WEEKS"):
        compare_2025_regular_season(schedule_csv=schedule, weekly_payloads=weekly)


def test_all_18_payloads_required_and_unapproved_week_url_blocked():
    schedule, weekly = fixture_corpus()
    del weekly[18]
    with pytest.raises(CollectionBlocked, match="NFL_ESPN_FULL_18_WEEK_PAYLOADS_REQUIRED"):
        compare_2025_regular_season(schedule_csv=schedule, weekly_payloads=weekly)
    for bad in (0, 19, "1", True):
        with pytest.raises(CollectionBlocked, match="NFL_ESPN_FULL_WEEK_NOT_ALLOWLISTED"):
            fetch_espn_regular_week(bad)
    assert len(ESPN_WEEK_URLS) == 18
    assert all("seasontype=2" in url and "limit=100" in url
               for url in ESPN_WEEK_URLS.values())


def test_all_nflverse_2025_regular_season_results_required():
    schedule, weekly = fixture_corpus()
    lines = schedule.decode().splitlines()
    # An incomplete one-game schedule must not claim "full-season" even if the
    # independent provider happens to return all other dates and games.
    incomplete = ("\n".join(lines[:-1]) + "\n").encode()
    with pytest.raises(CollectionBlocked, match="NFL_ESPN_FULL_SCHEDULE_EXPECTED_272"):
        compare_2025_regular_season(schedule_csv=incomplete, weekly_payloads=weekly)


def test_wrong_or_ambiguous_event_identity_does_not_count_as_match():
    schedule, weekly = fixture_corpus()
    weekly = dict(weekly)
    body = json.loads(weekly[7])
    body["events"][0]["competitions"][0]["competitors"][0]["team"]["abbreviation"] = "WAS"
    weekly[7] = json.dumps(body).encode()
    report = compare_2025_regular_season(schedule_csv=schedule, weekly_payloads=weekly)
    assert report["status"] == "PARTIAL_HOLD"
    assert report["matched_games"] == 271
    assert report["problems"][0]["issue"] == "MISSING_OR_UNFINALIZED"
