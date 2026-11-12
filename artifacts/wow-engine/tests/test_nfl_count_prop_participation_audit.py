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


@pytest.mark.parametrize("earlier_source_rows,expected_upper_bound", [
    (9, 0), (10, 1),
])
def test_missing_label_history_uses_earlier_rows_without_zero_imputation(
    sample_cohort, earlier_source_rows, expected_upper_bound,
):
    """Prior source-game threshold is an upper bound, never a fitted label."""
    stats, snaps, players = sample_cohort
    one = stats.loc[stats.player_id.eq("00-0031111")].copy()
    before = []
    for week in range(1, earlier_source_rows + 1):
        row = one.iloc[0].copy()
        row["game_id"] = f"2023_{week:02d}_BAL_KC"
        row["season"] = 2023
        row["week"] = week
        before.append(row)
    stats = pd.concat([
        stats.loc[stats.player_id.eq("00-0032222")],
        pd.DataFrame(before),
    ], ignore_index=True)
    result = reconcile_player_games(stats, snaps, players)
    receptions = result["missing_weekly_label_history_exposure"]["RECEPTIONS"]
    carries = result["missing_weekly_label_history_exposure"]["RUSH_ATTEMPTS"]
    attempts = result["missing_weekly_label_history_exposure"]["PASS_ATTEMPTS"]
    assert receptions["upper_bound_history_eligible_missing_rows"] == expected_upper_bound
    assert carries["upper_bound_history_eligible_missing_rows"] == expected_upper_bound
    assert attempts["upper_bound_history_eligible_missing_rows"] == 0
    assert receptions["count_label_inferred"] is False
    assert result["missing_player_games"] == 1
    assert result["status"] == "HOLD_UNRESOLVED_PARTICIPATION"
    assert result["can_execute"] is False


def test_future_source_rows_never_count_toward_a_missing_pregame_label(sample_cohort):
    stats, snaps, players = sample_cohort
    later = []
    for week in range(1, 11):
        row = stats.loc[stats.player_id.eq("00-0031111")].iloc[0].copy()
        row["game_id"] = f"2025_{week:02d}_BAL_KC"
        row["season"] = 2025
        row["week"] = week
        later.append(row)
    stats = pd.concat([
        stats.loc[stats.player_id.eq("00-0032222")],
        pd.DataFrame(later),
    ], ignore_index=True)
    result = reconcile_player_games(stats, snaps, players)
    assert result["missing_weekly_label_history_exposure"]["RECEPTIONS"][
        "upper_bound_history_eligible_missing_rows"
    ] == 0
    assert result["status"] == "HOLD_UNRESOLVED_PARTICIPATION"


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



def _synthetic_recorded_history_gap(*, gap_season=2023, gap_week=11, include_position=True):
    """Create 10 recorded prior games, one missing positive-snap game, one target."""
    stats = pd.DataFrame([
        {"player_id": "00-0000001", "game_id": f"2023_{week:02d}_A_B",
         "season": 2023, "week": week,
         **({"position": "WR"} if include_position else {})}
        for week in [*range(1, 11), 12]
    ])
    missing = pd.DataFrame([{
        "gsis_id": "00-0000001",
        "game_id": f"{gap_season}_{gap_week:02d}_A_B",
        "season": gap_season, "week": gap_week, "position": "WR",
    }])
    return stats, missing


def test_recorded_l10_source_skips_missing_positive_snap_game():
    """A missing eligible prior appearance must not be called clean L10 history."""
    from audit_nfl_count_prop_participation import (
        audit_observed_l10_history_gap_exposure,
    )
    stats, missing = _synthetic_recorded_history_gap()
    result = audit_observed_l10_history_gap_exposure(stats, missing)
    receptions = result["by_route"]["RECEPTIONS"]
    assert receptions["eligible_recorded_l10_rows"] == 1
    assert receptions["observed_l10_windows_with_unrecorded_snap_positive_game"] == 1
    assert receptions["by_season"]["2023"] == {"eligible": 1, "exposed": 1}
    assert result["unknown_outcomes_filled"] is False
    assert result["candidate_training_authorized"] is False
    assert result["can_execute"] is False


@pytest.mark.parametrize("season,week", [(2022, 5), (2024, 1)])
def test_prior_l10_window_excludes_gap_outside_chronological_window(season, week):
    """Older-than-L10 and future observations never contaminate this window."""
    from audit_nfl_count_prop_participation import (
        audit_observed_l10_history_gap_exposure,
    )
    stats, missing = _synthetic_recorded_history_gap(
        gap_season=season, gap_week=week,
    )
    result = audit_observed_l10_history_gap_exposure(stats, missing)
    assert result["by_route"]["RECEPTIONS"][
        "observed_l10_windows_with_unrecorded_snap_positive_game"
    ] == 0
    assert result["status"] == "HOLD_UNRESOLVED_PARTICIPATION"


def test_still_fewer_than_ten_recorded_priors_has_no_eligible_l10():
    from audit_nfl_count_prop_participation import (
        audit_observed_l10_history_gap_exposure,
    )
    stats, missing = _synthetic_recorded_history_gap()
    result = audit_observed_l10_history_gap_exposure(stats.iloc[:-1], missing)
    assert result["by_route"]["RECEPTIONS"]["eligible_recorded_l10_rows"] == 0


def test_audit_without_role_evidence_does_not_claim_clean_history():
    from audit_nfl_count_prop_participation import (
        audit_observed_l10_history_gap_exposure,
    )
    stats, missing = _synthetic_recorded_history_gap(include_position=False)
    result = audit_observed_l10_history_gap_exposure(stats, missing)
    assert result["by_route"] == {}
    assert result["unavailable_reason"] == "NFL_PROP_PARTICIPATION_COLUMNS_MISSING"
    assert result["can_execute"] is False


def test_missing_other_role_not_silently_assumed_as_eligible_target():
    from audit_nfl_count_prop_participation import (
        audit_observed_l10_history_gap_exposure,
    )
    stats, missing = _synthetic_recorded_history_gap()
    missing.loc[0, "position"] = "QB"
    result = audit_observed_l10_history_gap_exposure(stats, missing)
    assert result["by_route"]["RECEPTIONS"][
        "observed_l10_windows_with_unrecorded_snap_positive_game"
    ] == 0
    assert result["unmapped_identity_audited"] is False


def test_duplicate_observed_history_week_fails_closed():
    from audit_nfl_count_prop_participation import (
        audit_observed_l10_history_gap_exposure,
    )
    stats, missing = _synthetic_recorded_history_gap()
    duplicated = pd.concat([stats, stats.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="NFL_PROP_PARTICIPATION_STATS_DUPLICATE"):
        audit_observed_l10_history_gap_exposure(duplicated, missing)


def test_duplicate_missing_history_week_fails_closed():
    from audit_nfl_count_prop_participation import (
        audit_observed_l10_history_gap_exposure,
    )
    stats, missing = _synthetic_recorded_history_gap()
    doubled = pd.concat([missing, missing], ignore_index=True)
    with pytest.raises(ValueError, match="NFL_PROP_PARTICIPATION_SNAP_DUPLICATE"):
        audit_observed_l10_history_gap_exposure(stats, doubled)


@pytest.mark.parametrize("source", ["stats", "missing"])
def test_l10_history_game_id_season_week_mismatch_fails_closed(source):
    """An embedded game year must agree with the source row chronology."""
    from audit_nfl_count_prop_participation import (
        audit_observed_l10_history_gap_exposure,
    )
    stats, missing = _synthetic_recorded_history_gap()
    if source == "stats":
        stats.loc[0, "game_id"] = "2024_01_A_B"
    else:
        missing.loc[0, "game_id"] = "2024_11_A_B"
    with pytest.raises(ValueError, match="NFL_PROP_PARTICIPATION_SNAP_INVALID:history_game_id"):
        audit_observed_l10_history_gap_exposure(stats, missing)


@pytest.mark.parametrize("source", ["stats", "missing"])
def test_l10_history_game_id_without_teams_fails_closed(source):
    """A year/week prefix alone is not an official event identity."""
    from audit_nfl_count_prop_participation import (
        audit_observed_l10_history_gap_exposure,
    )
    stats, missing = _synthetic_recorded_history_gap()
    if source == "stats":
        stats.loc[0, "game_id"] = "2023_01_"
    else:
        missing.loc[0, "game_id"] = "2023_11_"
    with pytest.raises(ValueError, match="NFL_PROP_PARTICIPATION_SNAP_INVALID:history_game_id"):
        audit_observed_l10_history_gap_exposure(stats, missing)


def _synthetic_route_split_exposure():
    """Synthetic identity-only counts; no outcome or fitted probability inputs."""
    from train_nfl_missing_prop_candidates import ROUTES
    annual = {
        "2021": {"eligible": 400, "exposed": 5},
        "2022": {"eligible": 400, "exposed": 10},
        "2023": {"eligible": 250, "exposed": 20},
        "2024": {"eligible": 325, "exposed": 20},
        "2025": {"eligible": 320, "exposed": 25},
    }
    return {
        "status": "HOLD_UNRESOLVED_PARTICIPATION",
        "by_route": {
            name: {
                "by_season": {year: dict(counts) for year, counts in annual.items()},
                "eligible_recorded_l10_rows": 1695,
                "observed_l10_windows_with_unrecorded_snap_positive_game": 80,
            }
            for name in ROUTES
        },
    }


def test_known_gap_exclusion_split_receipt_never_grants_model_authority():
    from audit_nfl_count_prop_participation import (
        summarize_known_gap_excluded_candidate_splits,
    )
    receipt = summarize_known_gap_excluded_candidate_splits(
        _synthetic_route_split_exposure())
    assert receipt["status"] == "RESEARCH_ONLY_KNOWN_GAP_EXCLUSION_NOT_SOURCE_CERTIFICATION"
    assert receipt["holdout_outcomes_read"] is False
    assert receipt["model_probabilities_fitted"] is False
    assert receipt["source_identity_gaps_reconciled"] is False
    assert receipt["candidate_training_authorized"] is False
    assert receipt["can_execute"] is False
    for route in receipt["by_route"].values():
        splits = route["splits"]
        assert splits["TRAIN"]["not_flagged_by_mapped_gap_audit"] == 1015
        assert splits["CALIBRATION"]["not_flagged_by_mapped_gap_audit"] == 305
        assert splits["HOLDOUT"]["not_flagged_by_mapped_gap_audit"] == 295
        assert splits["TRAIN"]["count_minimum_met_only"] is True
        assert splits["CALIBRATION"]["count_minimum_met_only"] is True
        assert splits["HOLDOUT"]["count_minimum_met_only"] is False
        assert route["known_gap_count_floor_met_all_splits"] is False
        assert route["fitted_candidate_authorized"] is False


def test_known_gap_exclusion_all_count_floors_do_not_certify_training():
    from audit_nfl_count_prop_participation import (
        summarize_known_gap_excluded_candidate_splits,
    )
    source = _synthetic_route_split_exposure()
    for record in source["by_route"].values():
        record["by_season"]["2025"]["exposed"] = 10
        record["observed_l10_windows_with_unrecorded_snap_positive_game"] = 65
    receipt = summarize_known_gap_excluded_candidate_splits(source)
    assert all(route["known_gap_count_floor_met_all_splits"]
               for route in receipt["by_route"].values())
    assert receipt["candidate_training_authorized"] is False
    assert receipt["holdout_outcomes_read"] is False


@pytest.mark.parametrize("malformation,reason", [
    ("negative", "cohort_counts"),
    ("over_exposed", "cohort_counts"),
    ("bool_count", "cohort_counts"),
    ("missing_year", "cohort_years"),
    ("extra_year", "cohort_years"),
    ("wrong_total", "cohort_totals"),
    ("missing_route", "cohort_route"),
    ("missing_count", "cohort_counts"),
])
def test_known_gap_exclusion_inconsistent_receipt_fails_closed(
    malformation, reason,
):
    from audit_nfl_count_prop_participation import (
        summarize_known_gap_excluded_candidate_splits,
    )
    source = _synthetic_route_split_exposure()
    one = source["by_route"]["RECEPTIONS"]
    if malformation == "negative":
        one["by_season"]["2025"]["eligible"] = -1
    elif malformation == "over_exposed":
        one["by_season"]["2025"]["exposed"] = 321
    elif malformation == "bool_count":
        one["by_season"]["2025"]["exposed"] = True
    elif malformation == "missing_year":
        one["by_season"].pop("2025")
    elif malformation == "extra_year":
        one["by_season"]["2026"] = {"eligible": 0, "exposed": 0}
    elif malformation == "wrong_total":
        one["eligible_recorded_l10_rows"] = 1696
    elif malformation == "missing_route":
        source["by_route"].pop("RECEPTIONS")
    else:
        one["by_season"]["2025"].pop("exposed")
    with pytest.raises(ValueError, match="NFL_PROP_PARTICIPATION_SNAP_INVALID:" + reason):
        summarize_known_gap_excluded_candidate_splits(source)


def test_known_gap_exclusion_no_position_data_stays_held_not_green():
    from audit_nfl_count_prop_participation import (
        summarize_known_gap_excluded_candidate_splits,
    )
    report = summarize_known_gap_excluded_candidate_splits({
        "status": "HOLD_UNRESOLVED_PARTICIPATION",
        "unavailable_reason": "NFL_PROP_PARTICIPATION_COLUMNS_MISSING",
        "by_route": {},
    })
    assert report["status"] == "HOLD_FEATURE_COHORT_POSITION_UNAVAILABLE"
    assert report["by_route"] == {}
    assert report["candidate_training_authorized"] is False
    assert report["can_execute"] is False


def test_known_gap_exclusion_invalid_upstream_status_is_not_accepted():
    from audit_nfl_count_prop_participation import (
        summarize_known_gap_excluded_candidate_splits,
    )
    fake = _synthetic_route_split_exposure()
    fake["status"] = "RESEARCH_COVERAGE_RECONCILED_ONLY"
    with pytest.raises(ValueError, match="NFL_PROP_PARTICIPATION_SNAP_INVALID:cohort_status"):
        summarize_known_gap_excluded_candidate_splits(fake)
