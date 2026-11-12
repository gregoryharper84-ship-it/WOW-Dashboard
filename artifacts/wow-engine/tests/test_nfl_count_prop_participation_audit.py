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


def test_stratified_participation_gaps_do_not_become_zero_stats(sample_cohort):
    """Diagnose missing source rows by season/role/snap band without imputation."""
    stats, snaps, players = sample_cohort
    # TE: 8 snaps, valid crosswalk, deliberately absent weekly stats row.
    stats = stats.loc[stats.player_id == "00-0032222"].copy()
    receipt = reconcile_player_games(stats, snaps, players)
    strata = receipt["participation_breakdown"]
    assert strata["season"]["mapped_missing_stats"]["2024"] == 1
    assert strata["position"]["mapped_missing_stats"]["TE"] == 1
    assert strata["position"]["matched_stats"]["QB"] == 1
    assert strata["snap_band"]["mapped_missing_stats"]["1-9"] == 1
    assert strata["snap_band"]["matched_stats"]["50+"] == 1
    for dimension, categories in strata.items():
        assert sum(categories["matched_stats"].values()) == receipt["player_games_matched"]
        assert sum(categories["mapped_missing_stats"].values()) == receipt["missing_player_games"]
        assert sum(categories["unmapped_identity"].values()) == receipt["unmapped_pfr_player_games"]
    assert receipt["status"] == "HOLD_UNRESOLVED_PARTICIPATION"
    assert receipt["can_execute"] is False


def test_unmapped_identity_is_stratified_without_guessing_gsis_id(sample_cohort):
    """The 48-class missing crosswalk failure remains distinct from stats gaps."""
    stats, snaps, players = sample_cohort
    players = players.loc[players.pfr_id != "PlayerA01"]
    receipt = reconcile_player_games(stats, snaps, players)
    assert receipt["participation_breakdown"]["position"]["unmapped_identity"]["TE"] == 1
    assert receipt["participation_breakdown"]["snap_band"]["unmapped_identity"]["1-9"] == 1
    assert receipt["missing_player_games"] == 0
    assert receipt["unmapped_pfr_player_games"] == 1
    assert receipt["status"] == "HOLD_UNRESOLVED_PARTICIPATION"


def test_missing_pfr_record_never_becomes_a_gsis_guess(sample_cohort):
    stats, snaps, players = sample_cohort
    players = players.loc[players.pfr_id != "PlayerA01"].copy()
    receipt = reconcile_player_games(stats, snaps, players)
    assert receipt["unmapped_pfr_player_games"] == 1
    assert receipt["unmapped_distinct_pfr_ids"] == 1
    assert receipt["unmapped_player_profiles"] == [{
        "pfr_player_id": "PlayerA01",
        "identity_gap_type": "PFR_NOT_IN_PINNED_PLAYERS",
        "player_games": 1,
        "seasons": [2024],
        "positions": ["TE"],
        "max_offense_snaps": 8,
        "first_game_id": "2024_01_BAL_KC",
    }]
    assert receipt["unmapped_gap_cause_player_games"] == {
        "PFR_NOT_IN_PINNED_PLAYERS": 1,
        "PFR_PRESENT_GSIS_UNAVAILABLE": 0,
    }
    assert receipt["unmapped_examples"][0]["identity_gap_type"] == (
        "PFR_NOT_IN_PINNED_PLAYERS"
    )
    assert receipt["status"] == "HOLD_UNRESOLVED_PARTICIPATION"
    assert receipt["can_execute"] is False


def test_present_pfr_without_gsis_is_separate_unknown_identity(sample_cohort):
    stats, snaps, players = sample_cohort
    players = players.copy()
    players.loc[players.pfr_id.eq("PlayerA01"), "gsis_id"] = None
    receipt = reconcile_player_games(stats, snaps, players)
    assert receipt["unmapped_pfr_player_games"] == 1
    assert receipt["unmapped_gap_cause_player_games"] == {
        "PFR_NOT_IN_PINNED_PLAYERS": 0,
        "PFR_PRESENT_GSIS_UNAVAILABLE": 1,
    }
    assert receipt["unmapped_gap_cause_distinct_ids"][
        "PFR_PRESENT_GSIS_UNAVAILABLE"
    ] == 1
    assert receipt["player_games_matched"] == 1
    assert receipt["status"] == "HOLD_UNRESOLVED_PARTICIPATION"


def test_repeated_pfr_gap_counts_games_and_distinct_ids_separately(sample_cohort):
    stats, snaps, players = sample_cohort
    extra = snaps.loc[snaps.pfr_player_id.eq("PlayerA01")].copy()
    extra["game_id"] = "2024_02_BAL_LV"
    extra["week"] = 2
    snaps = pd.concat([snaps, extra], ignore_index=True)
    players = players.loc[players.pfr_id != "PlayerA01"]
    receipt = reconcile_player_games(stats, snaps, players)
    assert receipt["unmapped_pfr_player_games"] == 2
    assert receipt["unmapped_distinct_pfr_ids"] == 1
    assert receipt["unmapped_gap_cause_distinct_ids"][
        "PFR_NOT_IN_PINNED_PLAYERS"
    ] == 1
    assert receipt["unmapped_player_profiles"][0]["player_games"] == 2
    assert receipt["unmapped_player_profiles"][0]["first_game_id"] == (
        "2024_01_BAL_KC"
    )
    assert receipt["unmapped_player_profiles"][0]["max_offense_snaps"] == 8
    assert receipt["status"] == "HOLD_UNRESOLVED_PARTICIPATION"
    assert receipt["independent_semantic_acceptance"] is False


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

def test_unrelated_historical_pfr_collision_does_not_block_a_valid_cohort(sample_cohort):
    """Only snap-participating PFR identities need one-to-one reconciliation."""
    stats, snaps, players = sample_cohort
    # The upstream player registry may include older namesakes never appearing
    # in the selected five-season offensive participant keyset.
    unrelated = pd.DataFrame([
        {"gsis_id": "00-0010000", "pfr_id": "OlderName01"},
        {"gsis_id": "00-0020000", "pfr_id": "OlderName01"},
    ])
    receipt = reconcile_player_games(
        stats, snaps, pd.concat([players, unrelated], ignore_index=True),
    )
    assert receipt["status"] == "RESEARCH_COVERAGE_RECONCILED_ONLY"
    assert receipt["offensive_participant_games"] == 2
    assert receipt["player_games_matched"] == 2


def test_active_pfr_collision_remains_fail_closed(sample_cohort):
    """A participating PFR ID with two possible GSIS identities is not usable."""
    stats, snaps, players = sample_cohort
    conflict = pd.DataFrame([{"gsis_id": "00-0099999", "pfr_id": "PlayerA01"}])
    with pytest.raises(ValueError, match="NFL_PROP_PARTICIPATION_CROSSWALK_CONFLICT"):
        reconcile_player_games(stats, snaps, pd.concat([players, conflict], ignore_index=True))


@pytest.mark.parametrize("year,kind", [(2023, "empty"), (2024, "zero")])
def test_main_rejects_single_empty_or_zero_offense_snap_season(
    tmp_path, monkeypatch, capsys, year, kind,
):
    """A single unobserved year must HOLD even when the other four are valid."""
    import sys
    import audit_nfl_count_prop_participation as mod

    stats = pd.DataFrame([{
        "game_id": "2021_01_ARI_TEN", "season": 2021, "week": 1,
        "player_id": "00-0031111",
    }])
    def fake_verified_csv(path, expected_sha):
        """Return only synthetic 2021-2025 snapshots to isolate the main guard."""
        name = path.name
        if name == "players.csv":
            raise AssertionError("crosswalk read must not occur after a failed season")
        current = int(name.split("_")[-1].split(".")[0])
        one = pd.DataFrame([{
            "season": current, "game_id": f"{current}_01_ARI_TEN",
            "week": 1, "game_type": "REG", "position": "QB",
            "pfr_player_id": "PlayerA01", "offense_snaps": 12,
        }])
        return (one.iloc[0:0].copy() if kind == "empty" else one.assign(offense_snaps=0)) if current == year else one

    monkeypatch.setattr(mod, "load_source_files", lambda *args, **kwargs: (
        stats, {"bundle_sha256": "a" * 64},
    ))
    monkeypatch.setattr(mod, "_verified_csv", fake_verified_csv)
    monkeypatch.setattr(sys, "argv", [
        "audit", "--stats-dir", str(tmp_path), "--snap-dir", str(tmp_path),
        "--players-csv", str(tmp_path / "players.csv"),
    ])
    with pytest.raises(ValueError, match=f"NFL_PROP_PARTICIPATION_SNAP_EMPTY:{year}"):
        mod.main()
    assert capsys.readouterr().out == ""

