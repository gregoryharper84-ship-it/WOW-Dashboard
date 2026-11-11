"""Negative-first synthetic checks for non-serving, pinned PBP actor-presence evidence."""
from __future__ import annotations

import gzip
import hashlib
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import audit_nfl_count_prop_pbp_presence as mod
from acquire_nfl_count_prop_sources import OFFICIAL_SIZE_BYTES, acquire_one


@pytest.fixture
def source_cohort():
    """Two eligible snap participants; only QB has a weekly-stat source row."""
    game = "2024_01_BAL_KC"
    stats = pd.DataFrame([
        {"game_id": game, "season": 2024, "week": 1, "player_id": "00-0032222"},
    ])
    snaps = pd.DataFrame([
        {"game_id": game, "season": 2024, "week": 1, "game_type": "REG",
         "position": "TE", "pfr_player_id": "PlayerA01", "offense_snaps": 8},
        {"game_id": game, "season": 2024, "week": 1, "game_type": "REG",
         "position": "QB", "pfr_player_id": "PlayerB01", "offense_snaps": 56},
    ])
    players = pd.DataFrame([
        {"gsis_id": "00-0031111", "pfr_id": "PlayerA01"},
        {"gsis_id": "00-0032222", "pfr_id": "PlayerB01"},
    ])
    return stats, snaps, players


def _verified_synthetic_gzip(tmp_path, monkeypatch, frame, year=2024):
    """Only mock the digest/size to exercise parser safety on synthetic bytes."""
    blob = gzip.compress(frame.to_csv(index=False).encode(), mtime=0)
    path = tmp_path / f"play_by_play_{year}.csv.gz"
    path.write_bytes(blob)
    monkeypatch.setitem(mod.PBP_GZ_SHA256, year, hashlib.sha256(blob).hexdigest())
    monkeypatch.setitem(OFFICIAL_SIZE_BYTES["pbp"], year, len(blob))
    return path


def _pbp_rows(**changes):
    """Construct a complete minimal PBP schema without modeling any prop."""
    row = {
        "game_id": "2024_01_BAL_KC", "season": 2024, "week": 1,
        "season_type": "REG", "passer_player_id": "00-0032222",
        "receiver_player_id": "00-0031111", "rusher_player_id": "",
    }
    row.update(changes)
    return pd.DataFrame([row])


def test_five_pinned_pbp_assets_have_expected_source_size():
    assets = mod.pbp_assets()
    assert len(assets) == 5
    assert len(set(x[0] for x in assets)) == 5
    assert set(mod.PBP_GZ_SHA256) == set(range(2021, 2026))
    assert sum(x[2] for x in assets) == 95753616
    assert all(len(x[1]) == 64 for x in assets)
    assert all(x[0].startswith("pbp/play_by_play_") for x in assets)


def test_official_pbp_path_uses_existing_trusted_downloader(tmp_path):
    """A new dataset path must not weaken the 11 original frozen asset pins."""
    from io import BytesIO
    class Response(BytesIO):
        def geturl(self):
            return "https://release-assets.githubusercontent.com/synthetic-test"
    blob = b"synthetic gzip fixture bytes"
    digest = hashlib.sha256(blob).hexdigest()
    receipt = acquire_one("pbp/play_by_play_2024.csv.gz", digest, len(blob), tmp_path,
                          open_url=lambda *args, **kwargs: Response(blob))
    assert receipt["action"] == "downloaded_verified"
    assert (tmp_path / "pbp/play_by_play_2024.csv.gz").read_bytes() == blob
    from acquire_nfl_count_prop_sources import frozen_assets
    assert len(frozen_assets()) == 11


def test_pbp_verified_bytes_produce_game_and_actor_presence(tmp_path, monkeypatch):
    path = _verified_synthetic_gzip(tmp_path, monkeypatch, _pbp_rows())
    games, actors = mod.read_verified_pbp_presence(path, 2024)
    assert games == {"2024_01_BAL_KC"}
    assert ("2024_01_BAL_KC", "00-0032222") in actors
    assert ("2024_01_BAL_KC", "00-0031111") in actors


def test_pbp_wrong_digest_rejects_before_parsing(tmp_path):
    path = tmp_path / "pbp.csv.gz"
    path.write_bytes(b"not official bytes")
    with pytest.raises(ValueError, match="NFL_PROP_PBP_SOURCE_DIGEST_MISMATCH"):
        mod.read_verified_pbp_presence(path, 2024)


def test_pbp_truncated_compressed_input_rejects_after_hash(tmp_path, monkeypatch):
    path = _verified_synthetic_gzip(tmp_path, monkeypatch, _pbp_rows())
    corrupt = path.read_bytes()[:-8]
    path.write_bytes(corrupt)
    monkeypatch.setitem(mod.PBP_GZ_SHA256, 2024, hashlib.sha256(corrupt).hexdigest())
    monkeypatch.setitem(OFFICIAL_SIZE_BYTES["pbp"], 2024, len(corrupt))
    with pytest.raises(ValueError, match="NFL_PROP_PBP_SOURCE_UNREADABLE"):
        mod.read_verified_pbp_presence(path, 2024)


@pytest.mark.parametrize("change,expected", [
    ({"week": 19}, "NFL_PROP_PBP_EVENT_SCHEMA_INVALID:2024:week"),
    ({"season": 2023}, "NFL_PROP_PBP_EVENT_SCHEMA_INVALID:2024:season"),
])
def test_pbp_bad_scope_is_fail_closed(tmp_path, monkeypatch, change, expected):
    path = _verified_synthetic_gzip(tmp_path, monkeypatch, _pbp_rows(**change))
    with pytest.raises(ValueError, match=expected):
        mod.read_verified_pbp_presence(path, 2024)


def test_pbp_missing_actor_column_is_fail_closed(tmp_path, monkeypatch):
    path = _verified_synthetic_gzip(
        tmp_path, monkeypatch, _pbp_rows().drop(columns=["receiver_player_id"]),
    )
    with pytest.raises(ValueError, match="NFL_PROP_PBP_EVENT_SCHEMA_INVALID"):
        mod.read_verified_pbp_presence(path, 2024)


def test_observed_pbp_actor_still_does_not_authorize_zero_or_model(source_cohort):
    game = "2024_01_BAL_KC"
    output = mod.classify_missing_pbp_presence(
        *source_cohort, {game}, {(game, "00-0031111")},
    )
    assert output["pbp_presence_counts"]["pbp_actor_observed"] == 1
    assert output["mapped_missing_player_games"] == 1
    assert output["status"] == "HOLD_PBP_PRESENCE_ONLY"
    assert output["missing_actor_means_zero"] is False
    assert output["fitted_specialist_certified"] is False
    assert output["can_execute"] is False


def test_absent_pbp_actor_never_becomes_zero(source_cohort):
    game = "2024_01_BAL_KC"
    output = mod.classify_missing_pbp_presence(
        *source_cohort, {game}, {(game, "00-0032222")},
    )
    assert output["pbp_presence_counts"]["pbp_actor_not_observed"] == 1
    assert output["status"] == "HOLD_PBP_PRESENCE_ONLY"
    assert output["missing_actor_means_zero"] is False


def test_missing_pbp_game_is_its_own_incomplete_evidence_class(source_cohort):
    output = mod.classify_missing_pbp_presence(
        *source_cohort, {"2024_01_OTHER"}, set(),
    )
    assert output["pbp_presence_counts"]["pbp_game_absent"] == 1
    assert output["can_execute"] is False


def test_unmapped_player_is_not_assigned_a_convenient_gsis(source_cohort):
    stats, snaps, players = source_cohort
    output = mod.classify_missing_pbp_presence(
        stats, snaps, players.loc[players.pfr_id != "PlayerA01"],
        {"2024_01_BAL_KC"}, set(),
    )
    assert output["mapped_missing_player_games"] == 0
    assert output["unmapped_pfr_player_games"] == 1
    assert sum(output["pbp_presence_counts"].values()) == 0
    assert output["status"] == "HOLD_PBP_PRESENCE_ONLY"


def test_active_crosswalk_conflict_remains_fail_closed(source_cohort):
    stats, snaps, players = source_cohort
    conflict = pd.concat([
        players,
        pd.DataFrame([{"gsis_id": "00-0099999", "pfr_id": "PlayerA01"}]),
    ], ignore_index=True)
    with pytest.raises(ValueError, match="NFL_PROP_PARTICIPATION_CROSSWALK_CONFLICT"):
        mod.classify_missing_pbp_presence(stats, snaps, conflict, set(), set())
