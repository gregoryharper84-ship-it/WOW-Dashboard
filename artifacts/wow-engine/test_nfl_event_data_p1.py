from __future__ import annotations

import csv
import io
from pathlib import Path

import httpx
import pytest

from nfl_event_data_p1 import (
    DATASET_INJURIES,
    DATASET_PBP,
    DATASET_SCHEDULES,
    DATASET_WEEKLY_ROSTERS,
    SourceAsset,
    SourceHostRejected,
    SourceSchemaChanged,
    download_asset,
    manifest_record,
    schedules_asset,
    season_assets,
    source_bundle,
)
from nfl_event_training_p1 import build_game_team_summaries, build_training_games


def _csv_bytes(fieldnames: list[str], rows: list[dict]) -> bytes:
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _mock_client(content: bytes, *, etag: str = '"test-etag"') -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=content,
            headers={"etag": etag, "last-modified": "Tue, 01 Sep 2026 06:00:00 GMT"},
            request=request,
        )

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_source_bundle_uses_canonical_nflverse_csv_endpoints():
    assets = source_bundle([2024, 2025])
    assert assets[0] == schedules_asset()
    assert len(assets) == 7
    urls = {asset.dataset_name: asset.source_url for asset in season_assets(2025)}
    assert urls[DATASET_PBP].endswith("/pbp/play_by_play_2025.csv")
    assert urls[DATASET_WEEKLY_ROSTERS].endswith("/weekly_rosters/roster_weekly_2025.csv")
    assert urls[DATASET_INJURIES].endswith("/injuries/injuries_2025.csv")
    assert schedules_asset().source_url.endswith("/nflverse/nfldata/master/data/games.csv")


def test_source_asset_rejects_non_github_supply_chain_host():
    with pytest.raises(SourceHostRejected):
        SourceAsset(DATASET_PBP, "https://example.com/play_by_play_2025.csv", "x.csv", 2025)


def test_download_hashes_and_schema_checks_schedule_csv(tmp_path: Path):
    fields = [
        "game_id", "season", "game_type", "week", "gameday",
        "away_team", "away_score", "home_team", "home_score", "roof",
    ]
    content = _csv_bytes(fields, [{
        "game_id": "2025_01_DAL_PHI", "season": 2025, "game_type": "REG",
        "week": 1, "gameday": "2025-09-04", "away_team": "DAL", "away_score": 20,
        "home_team": "PHI", "home_score": 24, "roof": "outdoors",
    }])
    with _mock_client(content) as client:
        capture = download_asset(schedules_asset(), tmp_path, client=client)
    assert capture.dataset_name == DATASET_SCHEDULES
    assert capture.row_count == 1
    assert capture.byte_count == len(content)
    assert len(capture.content_sha256) == 64
    assert capture.source_status == "CAPTURED"
    assert Path(capture.local_path).exists()
    assert capture.etag == '"test-etag"'

    manifest = manifest_record(capture, object_uri="s3://immutable-test/games.csv")
    assert manifest["content_sha256"] == capture.content_sha256
    assert manifest["raw_object_uri"].startswith("s3://")
    assert manifest["probability_publishable"] is False
    assert manifest["can_execute"] is False


def test_download_fails_closed_when_schema_changes(tmp_path: Path):
    content = b"game_id,season\n2025_01_DAL_PHI,2025\n"
    with _mock_client(content) as client:
        with pytest.raises(SourceSchemaChanged):
            download_asset(schedules_asset(), tmp_path, client=client)
    assert list(tmp_path.glob("*.csv")) == []
    assert list(tmp_path.glob("*.part")) == []


def test_training_game_normalizer_uses_schedule_outcome_and_skips_unplayed():
    schedule_rows = [
        {
            "game_id": "2025_01_DAL_PHI", "season": "2025", "game_type": "REG", "week": "1",
            "gameday": "2025-09-04", "away_team": "DAL", "away_score": "20",
            "home_team": "PHI", "home_score": "24", "roof": "outdoors", "temp": "76", "wind": "8",
        },
        {
            "game_id": "2025_02_PHI_KC", "season": "2025", "game_type": "REG", "week": "2",
            "gameday": "2025-09-14", "away_team": "PHI", "away_score": "",
            "home_team": "KC", "home_score": "",
        },
    ]
    games = build_training_games(
        schedule_rows,
        schedule_snapshot_id="11111111-1111-1111-1111-111111111111",
        schedule_content_sha256="a" * 64,
    )
    assert len(games) == 1
    game = games[0]
    assert game["game_id"] == "2025_01_DAL_PHI"
    assert game["home_win"] is True
    assert game["tie"] is False
    assert game["temp_f"] == 76.0
    assert len(game["row_inputs_hash"]) == 64
    assert game["probability_publishable"] is False
    assert game["can_execute"] is False


def test_pbp_normalizer_emits_exactly_two_team_rows_and_aggregates_without_probability():
    games = build_training_games([
        {
            "game_id": "2025_01_DAL_PHI", "season": 2025, "game_type": "REG", "week": 1,
            "gameday": "2025-09-04", "away_team": "DAL", "away_score": 20,
            "home_team": "PHI", "home_score": 24,
        }
    ], schedule_snapshot_id="11111111-1111-1111-1111-111111111111", schedule_content_sha256="a" * 64)

    pbp = [
        {
            "game_id": "2025_01_DAL_PHI", "posteam": "PHI", "defteam": "DAL", "epa": "0.6",
            "success": "1", "play_type": "pass", "pass": "1", "rush": "0",
            "interception": "0", "fumble_lost": "0", "sack": "0", "special_teams_play": "0",
            "passer_player_id": "QB-PHI",
        },
        {
            "game_id": "2025_01_DAL_PHI", "posteam": "DAL", "defteam": "PHI", "epa": "-0.4",
            "success": "0", "play_type": "pass", "pass": "1", "rush": "0",
            "interception": "1", "fumble_lost": "0", "sack": "0", "special_teams_play": "0",
            "passer_player_id": "QB-DAL",
        },
        {
            "game_id": "2025_01_DAL_PHI", "posteam": "PHI", "defteam": "DAL", "epa": "0.2",
            "success": "1", "play_type": "run", "pass": "0", "rush": "1",
            "interception": "0", "fumble_lost": "0", "sack": "0", "special_teams_play": "0",
            "passer_player_id": "",
        },
    ]
    rows = build_game_team_summaries(
        pbp,
        training_games=games,
        pbp_snapshot_id="22222222-2222-2222-2222-222222222222",
        pbp_content_sha256="b" * 64,
    )
    assert len(rows) == 2
    by_team = {row["team"]: row for row in rows}
    assert by_team["PHI"]["offensive_plays"] == 2
    assert by_team["PHI"]["offensive_epa_sum"] == 0.8
    assert by_team["PHI"]["success_rate"] == 1.0
    assert by_team["PHI"]["qb_gsis_ids"] == ["QB-PHI"]
    assert by_team["DAL"]["turnovers"] == 1
    assert by_team["DAL"]["defensive_epa_sum"] == 0.8
    for row in rows:
        assert len(row["row_inputs_hash"]) == 64
        assert row["probability_publishable"] is False
        assert row["can_execute"] is False
        assert not any("probability" in key and key != "probability_publishable" for key in row)


def test_p1_migration_preserves_nfl_capability_unavailable():
    sql = (Path(__file__).parent / "migrations" / "20260901_nfl_event_data_p1.sql").read_text()
    normalized = " ".join(sql.split()).lower()
    assert "wow_nfl_source_snapshots" in sql
    assert "wow_nfl_training_games" in sql
    assert "wow_nfl_game_team_summaries" in sql
    assert "capability_status = 'unavailable'" in normalized
    assert "probability_publishable', false" in normalized
    assert "can_execute', false" in normalized
    assert "insert into public.wow_nfl_event_fitted_model_artifacts" not in normalized
