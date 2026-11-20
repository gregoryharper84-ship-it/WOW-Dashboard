"""Fixture-only contract tests. No live provider calls from CI."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v17.multi_source_historical_collector import (
    CAN_EXECUTE, CollectionBlocked, _allowed_initial_url, _approved_source,
    _redirect_allowed, collect_mlb_game, collect_wnba_season,
)


def mlb_fixture(status='F', game_pk=12345):
    return json.dumps({
        'gameData': {
            'game': {'pk': game_pk}, 'status': {'statusCode': status},
            'datetime': {'dateTime': '2026-05-01T23:10:00Z'},
            'teams': {'home': {'id': 121}, 'away': {'id': 141}},
        },
        'liveData': {'boxscore': {'teams': {
            'home': {'players': {'ID1001': {'person': {'id': 1001, 'fullName': 'Fixture Batter'},
                      'stats': {'batting': {'hits': 2, 'runs': 1, 'strikeOuts': 0}}}}},
            'away': {'players': {'ID2002': {'person': {'id': 2002, 'fullName': 'Fixture Pitcher'},
                      'stats': {'pitching': {'strikeOuts': 8, 'earnedRuns': 2}}}}},
        }}},
    }).encode()


def test_mlb_ingests_settled_exact_game_identity_without_scoring():
    batch = collect_mlb_game(12345, download=lambda _: mlb_fixture())
    assert batch.provider == 'MLB_STATS_API' and len(batch.rows) == 5
    assert batch.probability_publishable is False
    assert batch.training_materialized is False
    assert batch.can_execute is False and CAN_EXECUTE is False
    assert {r['stat_type'] for r in batch.rows} == {
        'BATTING_HITS', 'BATTING_RUNS', 'BATTING_STRIKEOUTS',
        'PITCHING_STRIKEOUTS', 'PITCHING_EARNED_RUNS',
    }
    assert all(r['official_event_id'] == '12345' for r in batch.rows)
    assert all(r['record_class'] == 'SETTLED_OUTCOME' for r in batch.rows)


def test_mlb_rejects_unfinalized_game_and_incorrect_identity():
    with pytest.raises(CollectionBlocked, match='COLLECTOR_EVENT_NOT_FINAL'):
        collect_mlb_game(12345, download=lambda _: mlb_fixture('I'))
    with pytest.raises(CollectionBlocked, match='COLLECTOR_EVENT_IDENTITY_MISMATCH'):
        collect_mlb_game(12345, download=lambda _: mlb_fixture(game_pk=12346))


def test_wnba_aliases_minutes_and_nonplayed_reconciliation():
    data = ('player_id,game_id,game_date,min,pts,reb,ast,3PM\n'
            '15,001,2026-05-01,30:30,21,6,4,3\n'
            '16,001,2026-05-01,,0,0,0,0\n')
    batch = collect_wnba_season(2026, download=lambda _: data.encode())
    assert batch.license_id == 'CC-BY-4.0'
    assert len(batch.rows) == 1 and batch.skipped_nonplayed == 1
    assert batch.rows[0]['stats'] == {'MIN': 30.5, 'PTS': 21, 'REB': 6, 'AST': 4, 'FG3M': 3}
    assert batch.rows[0]['role_evidence_status'] == 'UNRESOLVED'
    assert batch.rows[0]['training_materialized'] is False
    assert batch.as_dict()['row_count'] + batch.as_dict()['skipped_nonplayed'] == 2


def test_wnba_fails_closed_on_duplicate_or_malformed_played_row():
    duplicate = ('player_id,game_id,game_date,min,pts,reb,ast,3PM\n'
                 '15,001,2026-05-01,30,21,6,4,3\n'
                 '15,001,2026-05-01,30,21,6,4,3\n')
    with pytest.raises(CollectionBlocked, match='COLLECTOR_DUPLICATE_SOURCE_ROW'):
        collect_wnba_season(2026, download=lambda _: duplicate.encode())
    invalid = 'player_id,game_id,game_date,min,pts,reb,ast,3PM\n15,001,2026-05-01,30,INVALID,6,4,3\n'
    with pytest.raises(CollectionBlocked, match='COLLECTOR_WNBA_PLAYED_ROW_INVALID'):
        collect_wnba_season(2026, download=lambda _: invalid.encode())


def test_url_restrictions_and_market_rights():
    assert _allowed_initial_url('https://statsapi.mlb.com/api/v1.1/game/12345/feed/live')
    assert not _allowed_initial_url('http://statsapi.mlb.com/api/v1.1/game/12345/feed/live')
    assert not _allowed_initial_url('https://evil.example/anything')
    assert not _allowed_initial_url('https://github.com/other/repo/releases/download/data/test.csv')
    assert _redirect_allowed('https://github.com/sportsdataverse/sportsdataverse-data/releases/download/wnba_stats_player_game_logs/player_game_logs_2026.csv',
                             'https://release-assets.githubusercontent.com/abc?token=x')
    assert not _redirect_allowed('https://github.com/a/b', 'https://169.254.169.254/')
    with pytest.raises(CollectionBlocked, match='HISTORICAL_SOURCE_NOT_APPROVED'):
        _approved_source('NFL', 'NFLVERSE')
    with pytest.raises(CollectionBlocked, match='MARKET_EVIDENCE_NOT_ALLOWED_IN_SPORTING_MODEL'):
        _approved_source('MULTISPORT', 'SPORTSDATAIO_VAULT')


def test_no_gambling_execution_or_fake_probability_in_collected_envelope():
    batch = collect_mlb_game(12345, download=lambda _: mlb_fixture())
    result = batch.as_dict()
    assert not result['can_execute']
    assert result['collection_status'] == 'BRONZE_OUTCOMES_ONLY'
    assert not result['training_materialized']
    assert not result['probability_publishable']
    assert 'calibrated_probability' not in result
