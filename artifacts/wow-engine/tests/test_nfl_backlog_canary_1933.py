"""Backlog #1933 frozen-source canary unit tests — fixtures only; no network."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v17.multi_source_historical_collector import CollectionBlocked
from scripts.nfl_backlog_canary_1933 import reconcile_reference, run, SEASON


def reference(*, sha: str, rows: int, url: str) -> dict:
    snap = {
        "provider": "NFLVERSE_STATS_PLAYER",
        "assets": {str(SEASON): {"sha256": sha, "rows": rows, "url": url}},
    }
    return {"routes": {
        route: {"source": {"source_snapshot": snap}}
        for route in ("PASSING_YARDS", "RUSHING_YARDS", "RECEIVING_YARDS", "ANYTIME_TD")
    }}


def test_reference_matches_all_routes_only_when_exact_hash_rows_and_url_agree():
    expected = {"sha256": "a" * 64, "rows": 2, "url": "https://example.com/stats.csv"}
    result = reconcile_reference(reference(sha=expected["sha256"], rows=2, url=expected["url"]), expected)
    assert result["matches_frozen_source"]
    for changed in ({"sha256": "b" * 64}, {"rows": 3}, {"url": "https://example.com/stale.csv"}):
        mutated = expected | changed
        assert not reconcile_reference(
            reference(sha=expected["sha256"], rows=2, url=expected["url"]), mutated
        )["matches_frozen_source"]


def test_reference_disagreement_across_fitted_routes_stops_canary():
    report = reference(sha="a" * 64, rows=5, url="https://example.com/ok.csv")
    report["routes"]["ANYTIME_TD"]["source"] = {
        "source_snapshot": {
            "provider": "NFLVERSE_STATS_PLAYER",
            "assets": {"2025": {"sha256": "b" * 64, "rows": 5, "url": "https://example.com/ok.csv"}},
        }
    }
    with pytest.raises(CollectionBlocked, match="NFL_CANARY_REFERENCE_ROUTE_CONFLICT"):
        reconcile_reference(report, {"sha256": "a" * 64, "rows": 5, "url": "https://example.com/ok.csv"})


def test_provider_mismatch_fails_closed():
    report = reference(sha="a" * 64, rows=5, url="https://example.com/ok.csv")
    report["routes"]["PASSING_YARDS"]["source"]["source_snapshot"]["provider"] = "SPORTSBOOK"
    with pytest.raises(CollectionBlocked, match="NFL_CANARY_REFERENCE_PROVIDER_MISMATCH"):
        reconcile_reference(report, {"sha256": "a" * 64, "rows": 5, "url": "https://example.com/ok.csv"})


def test_fixture_network_injection_does_not_pretend_matching_existing_frozen_corpus():
    schedule = (
        "season,week,game_id,game_type,gameday,gametime,home_team,away_team,home_score,away_score\n"
        "2025,1,2025_01_DAL_PHI,REG,2025-09-07,13:00,PHI,DAL,24,17\n"
    ).encode()
    player = (
        "season,week,season_type,game_id,player_id,team,opponent_team,passing_yards,"
        "rushing_yards,receiving_yards,passing_tds,rushing_tds,receiving_tds\n"
        "2025,1,REG,2025_01_DAL_PHI,00-0030001,DAL,PHI,271,19,0,2,0,0\n"
    ).encode()
    from v17.nfl_multi_source_pilot import SCHEDULE_URL
    receipt = run(download=lambda url: schedule if url == SCHEDULE_URL else player)
    assert receipt["status"] == "SOURCE_VALIDATION_HOLD"
    assert "SOURCE_REVISION_DRIFT" in receipt["blockers"]
    assert receipt["reference"]["matches_frozen_source"] is False
    assert receipt["research_only"] and receipt["can_execute"] is False
    assert receipt["independent_espn_identity"]["status"] == "NOT_CHECKED"
