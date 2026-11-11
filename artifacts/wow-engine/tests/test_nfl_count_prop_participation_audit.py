"""Offline participant-coverage reconciliation: no silent missing-game zeros."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_nfl_count_prop_participation import (
    PLAYERS_SHA256, SNAP_SHA256, _verified_csv, reconcile_player_games,
)


@pytest.fixture
def sample_cohort():
    """Two offensive participants across one 2024 game, with distinct ID domains."""
    stats = pd.DataFrame([
        {"game_id": "2024_01_BAL_KC", "season": 2024, "week": 1, "player_id": "00-0031111"},
        {"game_id": "2024_01_BAL_KC", "season": 2024, "week": 1, "player_id": "00-0032222"},
    ])
    snaps = pd.DataFrame([
        {"game_id": "2024_01_BAL_KC", "season": 2024, "week": 1, "game_type": "REG",
         "pfr_player_id": "PlayerA01", "position": "TE", "offense_snaps": 8},
        {"game_id": "2024_01_BAL_KC", "season": 2024, "week": 1, "game_type": "REG",
         "pfr_player_id": "PlayerB01", "position": "QB", "offense_snaps": 56},
    ])
    players = pd.DataFrame([
        {"gsis_id": "00-0031111", "pfr_id": "PlayerA01"},
        {"gsis_id": "00-0032222", "pfr_id": "PlayerB01"},
    ])
    return stats, snaps, players


def test_fully_matched_participant_cohort_stays_noncertifying(sample_cohort):
    """Coverage reconciliation never certifies a prop specialist or grants execution."""
    receipt = reconcile_player_games(*sample_cohort)
    assert receipt["offensive_participant_games"] == 2
    assert receipt["player_games_matched"] == 2
    assert receipt["missing_player_games"] == 0
    assert receipt["unmapped_pfr_player_games"] == 0
    assert receipt["status"] == "RESEARCH_COVERAGE_RECONCILED_ONLY"
    assert receipt["fitted_specialist_certified"] is False
    assert receipt["independent_semantic_acceptance"] is False
    assert receipt["can_execute"] is False


def test_missing_eight_snap_participant_game_stays_unknown(sample_cohort):
    """A player who logged eight offensive snaps cannot disappear as a zero."""
    stats, snaps, players = sample_cohort
    stats = stats.loc[stats.player_id != "00-0031111"].copy()
    before = len(stats)
    receipt = reconcile_player_games(stats, snaps, players)
    assert receipt["status"] == "HOLD_UNRESOLVED_PARTICIPATION"
    assert receipt["missing_player_games"] == 1
    assert receipt["player_games_matched"] == 1
    assert receipt["missing_examples"][0]["offense_snaps"] == 8
    assert receipt["missing_examples"][0]["pfr_player_id"] == "PlayerA01"
    assert len(stats) == before  # no inferred zero-game appended


def test_mismatching_game_id_is_not_a_match(sample_cohort):
    """Player plus week alone cannot excuse a mismatched official game identity."""
    stats, snaps, players = sample_cohort
    stats = stats.copy()
    stats.loc[stats.player_id == "00-0031111", "game_id"] = "2024_01_OTHER"
    receipt = reconcile_player_games(stats, snaps, players)
    assert receipt["missing_player_games"] == 1


def test_missing_crosswalk_player_is_held_not_name_matched(sample_cohort):
    """Names and uniforms do not replace a verified PFR-to-GSIS ID mapping."""
    stats, snaps, players = sample_cohort
    receipt = reconcile_player_games(stats, snaps, players.loc[players.gsis_id == "00-0032222"])
    assert receipt["unmapped_pfr_player_games"] == 1
    assert receipt["missing_player_games"] == 0
    assert receipt["status"] == "HOLD_UNRESOLVED_PARTICIPATION"


def test_conflicting_crosswalk_fails_closed(sample_cohort):
    """A PFR ID mapping to two GSIS IDs must never select whichever comes first."""
    stats, snaps, players = sample_cohort
    conflicted = pd.concat([
        players,
        pd.DataFrame([{"gsis_id": "00-0039999", "pfr_id": "PlayerA01"}]),
    ], ignore_index=True)
    with pytest.raises(ValueError, match="NFL_PROP_PARTICIPATION_CROSSWALK_CONFLICT"):
        reconcile_player_games(stats, snaps, conflicted)


@pytest.mark.parametrize("bad", [-1, float("nan"), "oops"])
def test_invalid_or_missing_snap_counts_fail_closed(sample_cohort, bad):
    """Unreliable participation cannot justify source completeness."""
    stats, snaps, players = sample_cohort
    snaps = snaps.copy()
    snaps.loc[0, "offense_snaps"] = bad
    with pytest.raises(ValueError, match="NFL_PROP_PARTICIPATION_SNAP_INVALID:offense_snaps"):
        reconcile_player_games(stats, snaps, players)


def test_zero_offense_snaps_are_outside_the_specific_audit_scope(sample_cohort):
    """Do not infer a stat zero or participant zero from no offensive snaps."""
    stats, snaps, players = sample_cohort
    snaps = snaps.copy()
    snaps.loc[0, "offense_snaps"] = 0
    receipt = reconcile_player_games(stats, snaps, players)
    assert receipt["offensive_participant_games"] == 1
    assert receipt["status"] == "RESEARCH_COVERAGE_RECONCILED_ONLY"


def test_duplicate_participant_game_fails_closed(sample_cohort):
    """An ambiguous duplicated snap-player/game identity is not auditable."""
    stats, snaps, players = sample_cohort
    snaps = pd.concat([snaps, snaps.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="NFL_PROP_PARTICIPATION_SNAP_DUPLICATE"):
        reconcile_player_games(stats, snaps, players)


def test_required_source_fields_cannot_be_omitted(sample_cohort):
    """The player ID crosswalk must be explicit rather than name-inferred."""
    stats, snaps, players = sample_cohort
    with pytest.raises(ValueError, match="NFL_PROP_PARTICIPATION_COLUMNS_MISSING:PLAYERS:pfr_id"):
        reconcile_player_games(stats, snaps, players.drop(columns=["pfr_id"]))


def test_pinned_source_checksum_mismatch_is_rejected_before_parsing(tmp_path):
    """Frozen digests are enforceable source evidence, not filenames alone."""
    path = tmp_path / "players.csv"
    path.write_text("gsis_id,pfr_id\n00-0031111,PlayerA01\n", encoding="utf-8")
    with pytest.raises(ValueError, match="NFL_PROP_PARTICIPATION_SOURCE_DIGEST_MISMATCH:players.csv"):
        _verified_csv(path, PLAYERS_SHA256)
    assert len(SNAP_SHA256) == 5
    assert set(SNAP_SHA256) == set(str(year) for year in range(2021, 2026))


def test_empty_offensive_snap_cohort_cannot_pass_coverage(sample_cohort):
    """An empty participant source must never turn an empty diff into a green receipt."""
    stats, snaps, players = sample_cohort
    with pytest.raises(ValueError, match="NFL_PROP_PARTICIPATION_SNAP_EMPTY"):
        reconcile_player_games(stats, snaps.iloc[0:0], players)


def test_all_zero_offensive_snaps_cannot_pass_coverage(sample_cohort):
    """Zero tracked offensive participation is unknown source coverage, not success."""
    stats, snaps, players = sample_cohort
    snaps = snaps.copy()
    snaps["offense_snaps"] = 0
    with pytest.raises(ValueError, match="NFL_PROP_PARTICIPATION_SNAP_EMPTY"):
        reconcile_player_games(stats, snaps, players)
