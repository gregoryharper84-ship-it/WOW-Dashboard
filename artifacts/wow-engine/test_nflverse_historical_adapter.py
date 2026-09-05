from __future__ import annotations

from datetime import datetime, timezone

import pytest

from historical_data_backbone import HistoricalDataContractError
from nflverse_historical_adapter import (
    NFLVerseHistoricalAdapterError,
    build_schedule_index,
    load_nflverse_dataset_policy,
    normalize_player_stats_corpus,
    normalize_player_stats_row,
    normalize_schedule_row,
    require_allowed_dataset,
)


UTC = timezone.utc


def _schedule(**overrides):
    row = {
        "game_id": "2025_01_DAL_PHI",
        "season": 2025,
        "game_type": "REG",
        "week": 1,
        "gameday": "2025-09-07",
        "gametime": "13:00",
        "away_team": "DAL",
        "home_team": "PHI",
        # Market columns can exist in nflverse schedules but are not sporting labels.
        "away_moneyline": 130,
        "home_moneyline": -150,
        "spread_line": 3.0,
        "total_line": 47.5,
    }
    row.update(overrides)
    return row


def _player(**overrides):
    row = {
        "player_id": "00-0030001",
        "player_name": "Q.Back",
        "player_display_name": "Quarter Back",
        "position": "QB",
        "season": 2025,
        "week": 1,
        "season_type": "REG",
        "game_id": "2025_01_DAL_PHI",
        "team": "DAL",
        "opponent_team": "PHI",
        "completions": 23,
        "attempts": 34,
        "passing_yards": 271,
        "passing_tds": 2,
        "passing_interceptions": 1,
        "carries": 4,
        "rushing_yards": 19,
        "rushing_tds": 0,
        "targets": 0,
        "receptions": 0,
        "receiving_yards": 0,
        "receiving_tds": 0,
        # An injected market-looking field must be ignored by normalization.
        "sportsbook_implied_probability": 0.71,
    }
    row.update(overrides)
    return row


def test_dataset_policy_allows_core_and_excludes_ftn_sources() -> None:
    policy = load_nflverse_dataset_policy()
    assert require_allowed_dataset("player_stats", policy=policy).production_training_allowed is True
    assert require_allowed_dataset("schedules", policy=policy).production_training_allowed is True
    assert require_allowed_dataset("players", policy=policy).production_training_allowed is True

    with pytest.raises(NFLVerseHistoricalAdapterError) as participation:
        require_allowed_dataset("participation", policy=policy)
    assert participation.value.code == "NFLVERSE_DATASET_EXCLUDED"

    with pytest.raises(NFLVerseHistoricalAdapterError) as ftn:
        require_allowed_dataset("ftn_charting", policy=policy)
    assert ftn.value.code == "NFLVERSE_DATASET_EXCLUDED"


def test_schedule_kickoff_is_parsed_from_documented_eastern_time() -> None:
    game = normalize_schedule_row(_schedule())
    assert game.event_start_time == datetime(2025, 9, 7, 17, 0, tzinfo=UTC)
    assert game.can_execute is False


def test_schedule_duplicate_game_id_fails_closed() -> None:
    with pytest.raises(NFLVerseHistoricalAdapterError) as exc:
        build_schedule_index([_schedule(), _schedule()])
    assert exc.value.code == "NFLVERSE_SCHEDULE_DUPLICATE_GAME"


def test_player_stats_normalize_core_primitives_with_stable_ids() -> None:
    index = build_schedule_index([_schedule()])
    outcomes = normalize_player_stats_row(
        _player(),
        schedule_index=index,
        source_retrieved_at=datetime(2025, 9, 8, 12, 0, tzinfo=UTC),
        source_payload_hash="a" * 64,
        stat_types=[
            "PASS_ATTEMPTS",
            "PASS_COMPLETIONS",
            "PASSING_YARDS",
            "RUSH_ATTEMPTS",
            "RUSHING_YARDS",
            "RECEPTIONS",
            "RECEIVING_YARDS",
        ],
    )
    values = {row.stat_type: row.actual_value for row in outcomes}
    assert values == {
        "PASS_ATTEMPTS": 34.0,
        "PASS_COMPLETIONS": 23.0,
        "PASSING_YARDS": 271.0,
        "RUSH_ATTEMPTS": 4.0,
        "RUSHING_YARDS": 19.0,
        "RECEPTIONS": 0.0,
        "RECEIVING_YARDS": 0.0,
    }
    assert all(row.identity.event_id == "2025_01_DAL_PHI" for row in outcomes)
    assert all(row.identity.participant_id == "00-0030001" for row in outcomes)
    assert all(row.identity.team_id == "DAL" for row in outcomes)
    assert all(row.identity.opponent_id == "PHI" for row in outcomes)
    assert all(row.source_provider == "NFLVERSE" for row in outcomes)
    assert all(row.can_execute is False for row in outcomes)


def test_market_columns_are_not_normalized_as_sporting_outcomes() -> None:
    outcomes = normalize_player_stats_corpus(
        [_player()],
        schedule_rows=[_schedule()],
        source_retrieved_at=datetime(2025, 9, 8, 12, 0, tzinfo=UTC),
        player_stats_payload_hash="b" * 64,
        stat_types=["PASSING_YARDS"],
    )
    assert len(outcomes) == 1
    assert outcomes[0].stat_type == "PASSING_YARDS"
    assert outcomes[0].actual_value == 271.0


def test_team_opponent_must_match_schedule_identity() -> None:
    index = build_schedule_index([_schedule()])
    with pytest.raises(NFLVerseHistoricalAdapterError) as exc:
        normalize_player_stats_row(
            _player(opponent_team="NYG"),
            schedule_index=index,
            source_retrieved_at=datetime(2025, 9, 8, 12, 0, tzinfo=UTC),
            source_payload_hash="c" * 64,
        )
    assert exc.value.code == "NFLVERSE_PLAYER_GAME_TEAM_MISMATCH"


def test_missing_schedule_join_fails_closed() -> None:
    with pytest.raises(NFLVerseHistoricalAdapterError) as exc:
        normalize_player_stats_row(
            _player(),
            schedule_index={},
            source_retrieved_at=datetime(2025, 9, 8, 12, 0, tzinfo=UTC),
            source_payload_hash="d" * 64,
        )
    assert exc.value.code == "NFLVERSE_SCHEDULE_GAME_UNRESOLVED"


def test_missing_player_identity_fails_closed() -> None:
    index = build_schedule_index([_schedule()])
    with pytest.raises(NFLVerseHistoricalAdapterError) as exc:
        normalize_player_stats_row(
            _player(player_id=""),
            schedule_index=index,
            source_retrieved_at=datetime(2025, 9, 8, 12, 0, tzinfo=UTC),
            source_payload_hash="e" * 64,
        )
    assert exc.value.code == "NFLVERSE_IDENTITY_INCOMPLETE"


def test_unsupported_stat_type_cannot_sneak_in() -> None:
    index = build_schedule_index([_schedule()])
    with pytest.raises(NFLVerseHistoricalAdapterError) as exc:
        normalize_player_stats_row(
            _player(),
            schedule_index=index,
            source_retrieved_at=datetime(2025, 9, 8, 12, 0, tzinfo=UTC),
            source_payload_hash="f" * 64,
            stat_types=["SPORTSBOOK_IMPLIED_PROBABILITY"],
        )
    assert exc.value.code == "NFLVERSE_STAT_TYPE_UNSUPPORTED"


def test_pre_event_source_timestamp_cannot_publish_settled_outcome() -> None:
    index = build_schedule_index([_schedule()])
    with pytest.raises(HistoricalDataContractError) as exc:
        normalize_player_stats_row(
            _player(),
            schedule_index=index,
            source_retrieved_at=datetime(2025, 9, 7, 16, 59, tzinfo=UTC),
            source_payload_hash="1" * 64,
            stat_types=["PASSING_YARDS"],
        )
    assert exc.value.code == "HISTORICAL_OUTCOME_PREMATURE"


def test_null_stat_field_is_not_fabricated() -> None:
    index = build_schedule_index([_schedule()])
    outcomes = normalize_player_stats_row(
        _player(targets=None),
        schedule_index=index,
        source_retrieved_at=datetime(2025, 9, 8, 12, 0, tzinfo=UTC),
        source_payload_hash="2" * 64,
        stat_types=["TARGETS", "RECEPTIONS"],
    )
    assert [row.stat_type for row in outcomes] == ["RECEPTIONS"]


def test_policy_objects_are_non_executable() -> None:
    assert all(entry.can_execute is False for entry in load_nflverse_dataset_policy().values())
