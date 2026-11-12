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
        {"game_id": game, "season": 2024, "week": 1, "player_id": "00-0032222",
         "attempts": 23, "targets": 0, "carries": 1},
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
    ({"week": 2}, "NFL_PROP_PBP_EVENT_SCHEMA_INVALID:2024:game_id"),
    ({"game_id": "2024_02_BAL_KC"}, "NFL_PROP_PBP_EVENT_SCHEMA_INVALID:2024:game_id"),
    ({"game_id": "2023_01_BAL_KC"}, "NFL_PROP_PBP_EVENT_SCHEMA_INVALID:2024:game_id"),
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
        *source_cohort, {game}, {(game, "00-0031111"), (game, "00-0032222")},
    )
    assert output["pbp_presence_counts"]["pbp_actor_observed"] == 1
    assert output["pbp_matched_positive_control"]["pbp_actor_observed"] == 1
    assert output["pbp_positive_opportunity_control"]["pbp_actor_observed"] == 1
    assert output["pbp_actor_id_alignment_observed"] is True
    assert output["mapped_missing_player_games"] == 1
    assert output["status"] == "HOLD_PBP_PRESENCE_ONLY"
    assert output["missing_actor_means_zero"] is False
    assert output["fitted_specialist_certified"] is False
    assert output["can_execute"] is False


def test_no_positive_control_keeps_identity_alignment_unproven(source_cohort):
    """A wrong actor ID domain cannot masquerade as evidence for zeros."""
    game = "2024_01_BAL_KC"
    output = mod.classify_missing_pbp_presence(
        *source_cohort, {game}, {(game, "GSIS_UNRELATED")},
    )
    assert output["pbp_matched_positive_control"]["pbp_actor_observed"] == 0
    assert output["pbp_positive_opportunity_control"]["pbp_actor_not_observed"] == 1
    assert output["pbp_actor_id_alignment_observed"] is False
    assert output["pbp_presence_counts"]["pbp_actor_not_observed"] == 1
    assert output["missing_actor_means_zero"] is False
    assert output["status"] == "HOLD_PBP_PRESENCE_ONLY"


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


def test_invalid_positive_opportunity_count_rejects_without_zero_imputation(source_cohort):
    """A corrupt weekly opportunity cannot silently enter the PBP diagnostic."""
    stats, snaps, players = source_cohort
    stats = stats.copy()
    stats.loc[0, "attempts"] = float("nan")
    with pytest.raises(ValueError, match="NFL_PROP_SOURCE_COUNT_INVALID:attempts"):
        mod.classify_missing_pbp_presence(
            stats, snaps, players, {"2024_01_BAL_KC"}, set(),
        )


def test_pbp_all_zero_weekly_opportunities_do_not_prove_identity_alignment(source_cohort):
    """Zero-opportunity actor presence must not activate the positive control."""
    stats, snaps, players = source_cohort
    stats = stats.copy()
    stats[["attempts", "targets", "carries"]] = 0
    game = "2024_01_BAL_KC"
    result = mod.classify_missing_pbp_presence(
        stats, snaps, players, {game}, {(game, "00-0032222")},
    )
    assert result["pbp_matched_positive_control"]["pbp_actor_observed"] == 1
    assert result["pbp_positive_opportunity_control"]["pbp_actor_observed"] == 0
    assert result["pbp_actor_id_alignment_observed"] is False
    assert result["status"] == "HOLD_PBP_PRESENCE_ONLY"
    assert result["missing_actor_means_zero"] is False
    assert result["can_execute"] is False


class FTNResponse(__import__("io").BytesIO):
    def __init__(self, data, url="https://release-assets.githubusercontent.com/ftn"):
        super().__init__(data)
        self.url = url

    def geturl(self):
        return self.url


def _ftn_source(monkeypatch, **changes):
    import io
    game = "2025_01_DAL_PHI"
    row = {
        "nflverse_game_id": game,
        "play_id": "108",
        "offense_players": "00-0032222;00-0031111",
    }
    row.update(changes)
    payload = pd.DataFrame([row]).to_csv(index=False).encode()
    monkeypatch.setattr(mod, "FTN_PARTICIPATION_2025_SHA256", hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(mod, "FTN_PARTICIPATION_2025_BYTES", len(payload))
    return payload


def test_ftn_2025_offensive_participants_are_presence_not_stat_labels(monkeypatch):
    content = _ftn_source(monkeypatch)
    games, participants, receipt = mod.read_verified_ftn_2025_offense_presence(
        open_url=lambda *a, **k: FTNResponse(content),
    )
    assert games == {"2025_01_DAL_PHI"}
    assert participants == {
        ("2025_01_DAL_PHI", "00-0032222"),
        ("2025_01_DAL_PHI", "00-0031111"),
    }
    assert receipt["status"] == "HOLD_FTN_OFFENSE_PRESENCE_RESEARCH_ONLY"
    assert receipt["license"] == "CC-BY-SA-4.0"
    assert receipt["attribution"] == "FTN Data via nflverse"
    assert receipt["participation_proves_missing_count_zero"] is False
    assert receipt["fitted_specialist_certified"] is False
    assert receipt["can_execute"] is False


def test_ftn_source_mutated_bytes_fail_digest_before_participant_extraction(monkeypatch):
    original = _ftn_source(monkeypatch)
    modified = original.replace(b"00-0031111", b"00-0039999")
    assert len(modified) == len(original)
    with pytest.raises(ValueError, match="NFL_PROP_FTN_PARTICIPATION_DIGEST_MISMATCH"):
        mod.read_verified_ftn_2025_offense_presence(
            open_url=lambda *a, **k: FTNResponse(modified),
        )


@pytest.mark.parametrize("changed,code", [
    ({"nflverse_game_id": "2024_01_DAL_PHI"}, "game_id"),
    ({"nflverse_game_id": "2025_01_DAL_PHI_EXTRA"}, "game_id"),
    ({"play_id": "unknown"}, "play_id"),
    ({"offense_players": "00-0031111;WRONG_ID"}, "offense_players"),
    ({"offense_players": "00-0031111;00-0031111"}, "offense_players"),
])
def test_ftn_source_malformed_event_or_player_identity_fails_closed(monkeypatch, changed, code):
    content = _ftn_source(monkeypatch, **changed)
    with pytest.raises(ValueError, match=f"NFL_PROP_FTN_PARTICIPATION_SCHEMA_INVALID:{code}"):
        mod.read_verified_ftn_2025_offense_presence(
            open_url=lambda *a, **k: FTNResponse(content),
        )


def test_ftn_source_redirect_outside_approved_release_hosts_is_rejected(monkeypatch):
    content = _ftn_source(monkeypatch)
    with pytest.raises(ValueError, match="NFL_PROP_FTN_PARTICIPATION_REDIRECT_UNTRUSTED"):
        mod.read_verified_ftn_2025_offense_presence(
            open_url=lambda *a, **k: FTNResponse(content, "https://evil.example.com/data"),
        )


def test_ftn_source_missing_offense_field_fails_closed(monkeypatch):
    content = _ftn_source(monkeypatch)
    corrupt = pd.DataFrame([{"nflverse_game_id": "2025_01_DAL_PHI", "play_id": "1"}]).to_csv(index=False).encode()
    monkeypatch.setattr(mod, "FTN_PARTICIPATION_2025_SHA256", hashlib.sha256(corrupt).hexdigest())
    monkeypatch.setattr(mod, "FTN_PARTICIPATION_2025_BYTES", len(corrupt))
    with pytest.raises(ValueError, match="NFL_PROP_FTN_PARTICIPATION_SCHEMA_INVALID:columns"):
        mod.read_verified_ftn_2025_offense_presence(
            open_url=lambda *a, **k: FTNResponse(corrupt),
        )


def test_ftn_default_opener_untrusted_redirect_is_scoped_to_ftn():
    """Failure before any body is received uses the FTN-specific reason."""
    def untrusted(*args, **kwargs):
        raise ValueError("NFL_PROP_ACQUIRE_REDIRECT_UNTRUSTED")

    with pytest.raises(ValueError, match="^NFL_PROP_FTN_PARTICIPATION_REDIRECT_UNTRUSTED$"):
        mod.read_verified_ftn_2025_offense_presence(open_url=untrusted)


def test_ftn_default_opener_unrelated_value_error_keeps_original_cause():
    """A nonredirect source validation failure must not be mislabeled."""
    def other(*args, **kwargs):
        raise ValueError("NFL_PROP_ANOTHER_SOURCE_FAILURE")

    with pytest.raises(ValueError, match="^NFL_PROP_ANOTHER_SOURCE_FAILURE$"):
        mod.read_verified_ftn_2025_offense_presence(open_url=other)


def test_ftn_stream_fails_after_partial_read_with_typed_network_hold(monkeypatch):
    """Network termination after valid prefix bytes never becomes a source row."""
    content = _ftn_source(monkeypatch)

    class BrokenFTNStream(FTNResponse):
        def __init__(self, payload):
            super().__init__(payload)
            self.calls = 0

        def read(self, size=-1):
            self.calls += 1
            if self.calls == 2:
                raise OSError("connection reset after first chunk")
            return super().read(size)

    with pytest.raises(ValueError, match="^NFL_PROP_FTN_PARTICIPATION_NETWORK_UNAVAILABLE$"):
        mod.read_verified_ftn_2025_offense_presence(
            open_url=lambda *a, **k: BrokenFTNStream(content),
        )


@pytest.mark.parametrize("observation", [
    {"pbp_actor_id_alignment_observed": False,
     "pbp_positive_opportunity_control": {"pbp_actor_observed": 0}},
    {"pbp_actor_id_alignment_observed": True,
     "pbp_positive_opportunity_control": {"pbp_actor_observed": 0}},
    {"pbp_actor_id_alignment_observed": True,
     "pbp_positive_opportunity_control": None},
    {"pbp_actor_id_alignment_observed": True,
     "pbp_positive_opportunity_control": {"pbp_actor_observed": True}},
    {},
])
def test_ftn_negative_presence_reporting_requires_positive_identity_alignment(observation):
    """No negative-presence report is permitted without an observed active control."""
    with pytest.raises(ValueError, match="HOLD_FTN_PARTICIPATION_SOURCE_OR_IDENTITY"):
        mod.require_ftn_positive_identity_control(observation)


def test_ftn_verified_positive_identity_control_only_enables_research_report():
    """Identity validation is necessary but never a count-label clearance."""
    mod.require_ftn_positive_identity_control({
        "pbp_actor_id_alignment_observed": True,
        "pbp_positive_opportunity_control": {"pbp_actor_observed": 1},
        "missing_actor_means_zero": False,
        "can_execute": False,
    })
    assert mod.CAN_EXECUTE is False


def test_ftn_review_examples_are_role_stratified_not_zero_labels(source_cohort):
    game = "2024_01_BAL_KC"
    result = mod.classify_missing_pbp_presence(
        *source_cohort, {game},
        {(game, "00-0031111"), (game, "00-0032222")},
    )
    assert result["missing_player_game_review_examples"] == [{
        "position": "TE", "season": 2024, "week": 1,
        "game_id": game, "pfr_player_id": "PlayerA01",
        "gsis_id": "00-0031111", "offense_snaps": 8,
        "research_presence": "on_field",
        "source_count_label": "UNKNOWN",
    }]
