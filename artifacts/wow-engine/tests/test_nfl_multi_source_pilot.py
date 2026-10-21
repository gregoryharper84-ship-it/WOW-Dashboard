"""Issue #1933: NFL pilot fixture tests, no external network calls."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v17.multi_source_historical_collector import CollectionBlocked, _allowed_initial_url
from v17.nfl_multi_source_pilot import (
    PLAYER_URL, SCHEDULE_URL, analyze_nfl_pilot, fetch_nfl_pilot,
)

AS_OF = datetime(2026, 10, 21, 20, 0, tzinfo=timezone.utc)
SCHEDULE = (
    "season,week,game_id,game_type,gameday,gametime,home_team,away_team,home_score,away_score\n"
    "2025,1,2025_01_DAL_PHI,REG,2025-09-07,13:00,PHI,DAL,24,17\n"
)
PLAYER = (
    "season,week,season_type,game_id,player_id,team,opponent_team,passing_yards,rushing_yards,"
    "receiving_yards,passing_tds,rushing_tds,receiving_tds\n"
    "2025,1,REG,2025_01_DAL_PHI,00-0030001,DAL,PHI,271,19,0,2,0,0\n"
)
ESPN_EVENT = {
    "season": 2025, "week": 1, "status": "FINAL",
    "home_team": "PHI", "away_team": "DAL",
    "event_start_time": "2025-09-07T17:00:00Z",
}


def pilot(schedule=SCHEDULE, player=PLAYER, espn=None, as_of=AS_OF):
    return analyze_nfl_pilot(
        season=2025, schedule_csv=schedule.encode(), player_csv=player.encode(),
        as_of=as_of, espn_final_events=espn,
    )


def test_nfl_core_routes_normalize_without_touching_model_or_training():
    result = pilot()
    assert result["status"] == "RESEARCH_ONLY"
    assert result["source_rights_state"] == "LICENSE_REVIEW_REQUIRED"
    assert result["license_id"] == "CC-BY-4.0"
    assert result["counts"]["finalized_games_in_scope"] == 1
    assert result["counts"]["joined_player_rows"] == 1
    assert result["counts"]["held_unsettled_or_unjoined"] == 0
    assert set(result["counts"]["normalized_stat_observations"]) == {
        "PASSING_YARDS", "RUSHING_YARDS", "RECEIVING_YARDS",
        "PASSING_TDS", "RUSHING_TDS", "RECEIVING_TDS",
    }
    assert all(count == 1 for count in result["counts"]["normalized_stat_observations"].values())
    assert result["ready_for_offline_training"] is False
    assert result["production_certified"] is False
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
    assert result["independent_espn_identity"]["status"] == "NOT_CHECKED"


def test_exact_espn_final_cross_source_identity_completes_only_with_evidence():
    assert pilot(espn=[ESPN_EVENT])["independent_espn_identity"] == {
        "status": "MATCHED", "matched": 1, "unresolved": 0,
    }
    assert pilot(espn=[{**ESPN_EVENT, "home_team": "NYG"}])["independent_espn_identity"] == {
        "status": "PARTIAL_HOLD", "matched": 0, "unresolved": 1,
    }


def test_duplicate_espn_event_match_fails_closed():
    with pytest.raises(CollectionBlocked, match="NFL_PILOT_INDEPENDENT_EVENT_AMBIGUOUS"):
        pilot(espn=[ESPN_EVENT, dict(ESPN_EVENT)])


def test_duplicate_player_game_fails_closed():
    lines = PLAYER.splitlines()
    duplicate = "\n".join([lines[0], lines[1], lines[1]]) + "\n"
    with pytest.raises(CollectionBlocked, match="NFL_PILOT_DUPLICATE_PLAYER_GAME"):
        pilot(player=duplicate)


def test_unfinished_game_is_not_represented_as_finalized():
    unfinished = SCHEDULE.replace(",24,17\n", ",,\n")
    with pytest.raises(CollectionBlocked, match="NFL_PILOT_NO_FINISHED_GAMES"):
        pilot(schedule=unfinished)


def test_target_future_relative_to_as_of_rejected():
    with pytest.raises(CollectionBlocked, match="NFL_PILOT_NO_FINISHED_GAMES"):
        pilot(as_of=datetime(2025, 9, 1, tzinfo=timezone.utc))


def test_missing_stat_column_rejected_not_silently_padded():
    broken = PLAYER.replace(",receiving_tds\n", "\n")
    with pytest.raises(CollectionBlocked, match="NFL_PILOT_REQUIRED_COLUMNS_MISSING"):
        pilot(player=broken)


def test_injected_market_field_does_not_enter_pilot_stats():
    lines = PLAYER.splitlines()
    modified = lines[0] + ",sportsbook_implied_probability\n" + lines[1] + ",0.90\n"
    report = pilot(player=modified)
    assert "sportsbook_implied_probability" not in report["counts"]["normalized_stat_observations"]


def test_only_allowlisted_https_nflverse_dataset_urls():
    assert _allowed_initial_url(PLAYER_URL.format(season=2025))
    assert _allowed_initial_url(SCHEDULE_URL)
    assert not _allowed_initial_url("http://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_2025.csv")
    assert not _allowed_initial_url("https://github.com/nflverse/nflverse-data/releases/download/ftn/ftn_2025.csv")
    assert not _allowed_initial_url("https://raw.githubusercontent.com/nflverse/nfldata/master/private.csv")
    assert not _allowed_initial_url("https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv?token=x")


def test_fetch_pilot_is_explicit_read_only_network_adapter():
    received = []
    def download(url):
        received.append(url)
        return SCHEDULE.encode() if url == SCHEDULE_URL else PLAYER.encode()
    report = fetch_nfl_pilot(2025, download=download)
    assert received == [SCHEDULE_URL, PLAYER_URL.format(season=2025)]
    assert report["sources"]["nflverse_schedule"]["sha256"]
    assert report["sources"]["nflverse_stats_player"]["sha256"]
    assert report["counts"]["joined_player_rows"] == 1


def test_no_unverified_espn_response_grants_source_parity():
    with pytest.raises(CollectionBlocked, match="NFL_PILOT_INDEPENDENT_SOURCE_EMPTY"):
        pilot(espn=[])


def test_missing_player_identity_is_quarantined_and_counted_without_fake_outcomes():
    lines = PLAYER.splitlines()
    blank = lines[1].replace("00-0030001", "")
    payload = "\n".join([lines[0], lines[1], blank]) + "\n"
    result = pilot(player=payload)
    counts = result["counts"]
    assert counts["player_rows_in_season"] == 2
    assert counts["quarantine_missing_player_id"] == 1
    assert counts["quarantine_missing_game_id"] == 0
    assert counts["quarantined_identity_rows"] == 1
    assert counts["quarantined_nonzero_stat_rows"] == 1
    assert counts["quarantined_zero_stat_rows"] == 0
    assert counts["quarantined_invalid_or_missing_stat_rows"] == 0
    assert counts["joined_player_rows"] == 1
    assert all(count == 1 for count in counts["normalized_stat_observations"].values())
    assert result["ready_for_offline_training"] is False
    assert result["probability_publishable"] is False

def test_quarantine_distinguishes_zero_from_nonzero_and_missing_label_rows():
    header, row = PLAYER.splitlines()
    fields = header.split(",")
    parts = row.split(",")
    blank_index = fields.index("player_id")
    parts[blank_index] = ""
    zero = parts.copy()
    for name in ("passing_yards", "rushing_yards", "receiving_yards",
                 "passing_tds", "rushing_tds", "receiving_tds"):
        zero[fields.index(name)] = "0"
    invalid = zero.copy()
    invalid[fields.index("rushing_tds")] = ""
    payload = "\n".join([header, row, ",".join(parts), ",".join(zero),
                         ",".join(invalid)]) + "\n"
    result = pilot(player=payload)
    counts = result["counts"]
    assert counts["quarantined_identity_rows"] == 3
    assert counts["quarantined_nonzero_stat_rows"] == 1
    assert counts["quarantined_zero_stat_rows"] == 1
    assert counts["quarantined_invalid_or_missing_stat_rows"] == 1
    assert counts["joined_player_rows"] == 1
    assert result["ready_for_offline_training"] is False


def test_opportunity_materiality_on_quarantined_rows_is_separate_from_six_outcomes():
    header = PLAYER.splitlines()[0].split(",")
    row = PLAYER.splitlines()[1].split(",")
    fields = ["attempts", "completions", "carries", "targets", "receptions", "special_teams_tds"]
    enriched = header + fields
    played = row + ["34", "23", "4", "0", "0", "0"]
    missing_id = played.copy()
    missing_id[header.index("player_id")] = ""
    for name in ("passing_yards", "rushing_yards", "receiving_yards",
                 "passing_tds", "rushing_tds", "receiving_tds"):
        missing_id[header.index(name)] = "0"
    missing_id[enriched.index("special_teams_tds")] = "1"
    source = "\n".join([",".join(enriched), ",".join(played), ",".join(missing_id)]) + "\n"
    counts = pilot(player=source)["counts"]
    audit = counts["quarantine_opportunity_audit"]
    assert audit["status"] == "NONZERO_OPPORTUNITY_HOLD"
    assert audit["nonzero_rows"] == 1
    assert audit["zero_rows"] == 0
    assert counts["quarantined_zero_stat_rows"] == 1
    assert counts["quarantined_nonzero_stat_rows"] == 0


def test_opportunity_missing_columns_never_leads_to_clean_claim():
    # Original fixture has six outcome labels only, no opportunity context.
    p = pilot(player=PLAYER)
    assert p["counts"]["quarantine_opportunity_audit"]["status"] == "MISSING_COLUMNS"
    assert "targets" in p["counts"]["quarantine_opportunity_audit"]["missing_columns"]
    assert p["ready_for_offline_training"] is False
