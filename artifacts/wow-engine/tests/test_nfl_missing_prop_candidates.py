"""Offline regression tests for NFL missing fitted-count prop candidate builder."""
from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import numpy as np
import pandas as pd
import pytest

from train_nfl_missing_prop_candidates import (
    ROUTES,
    build_feature_rows,
    build_candidate,
    load_source_files,
    validate_frame,
)


@pytest.fixture
def weekly_frame():
    """Regression: weekly frame."""
    records = []
    for player_number in range(5):
        for season in range(2021, 2026):
            for week in range(1, 19):
                k = player_number + week + season
                records.append({
                    "player_id": f"QB{player_number}", "player_display_name": f"QB {player_number}",
                    "position": "QB", "season": season, "week": week,
                    "season_type": "REG", "game_id": f"{season}_{week}_{player_number}",
                    "attempts": 25 + k % 12, "completions": 14 + k % 10,
                    "passing_tds": k % 4, "carries": 2 + k % 8,
                    "receptions": 0, "targets": 0,
                })
                records.append({
                    "player_id": f"WR{player_number}", "player_display_name": f"WR {player_number}",
                    "position": "WR", "season": season, "week": week,
                    "season_type": "REG", "game_id": f"{season}_{week}_{player_number}",
                    "attempts": 0, "completions": 0, "passing_tds": 0,
                    "carries": 1 + k % 6, "receptions": 3 + k % 7,
                    "targets": 12 + k % 9,
                })
    return pd.DataFrame.from_records(records)


def test_five_distinct_exact_count_routes():
    """Regression: five distinct exact count routes."""
    assert set(ROUTES) == {
        "COMPLETIONS", "PASSING_TOUCHDOWNS", "PASS_ATTEMPTS", "RECEPTIONS", "RUSH_ATTEMPTS",
    }
    assert len({spec["target"] for spec in ROUTES.values()}) == 5
    assert set(spec["model_kind"] for spec in ROUTES.values()) == {"POISSON_COUNT_CANDIDATE_V1"}


def test_untrusted_missing_columns_fail_closed(weekly_frame):
    """Regression: untrusted missing columns fail closed."""
    with pytest.raises(ValueError, match="NFL_PROP_SOURCE_COLUMNS_MISSING:completions"):
        validate_frame(weekly_frame.drop(columns=["completions"]))


def test_invalid_null_and_fractional_outcomes_fail_closed(weekly_frame):
    """Regression: invalid null and fractional outcomes fail closed."""
    for bad in (float("nan"), -1, 1.5):
        altered = weekly_frame.copy()
        altered["completions"] = altered["completions"].astype(float)
        altered.loc[0, "completions"] = bad
        with pytest.raises(ValueError, match="NFL_PROP_SOURCE_COUNT_INVALID:completions"):
            validate_frame(altered)


def test_invalid_completed_vs_attempts_fail_closed(weekly_frame):
    """Regression: invalid completed vs attempts fail closed."""
    altered = weekly_frame.copy()
    altered.loc[0, "completions"] = 100
    with pytest.raises(ValueError, match="NFL_PROP_SOURCE_COUNT_EXCEEDS_OPPORTUNITY:completions"):
        validate_frame(altered)


def test_duplicate_player_event_fails_closed(weekly_frame):
    """Regression: duplicate player event fails closed."""
    altered = pd.concat([weekly_frame, weekly_frame.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="NFL_PROP_SOURCE_DUPLICATE_PLAYER_EVENT"):
        validate_frame(altered)


def test_only_prior_games_appear_in_pregame_features(weekly_frame):
    """Regression: only prior games appear in pregame features."""
    rows = build_feature_rows(validate_frame(weekly_frame), "COMPLETIONS")
    qb = rows.loc[rows.player_id.eq("QB0")].sort_values(["season", "week"])
    assert qb.iloc[0].season == 2021 and qb.iloc[0].week == 11
    expected = weekly_frame.loc[(weekly_frame.player_id == "QB0") & (weekly_frame.season == 2021) & (weekly_frame.week <= 10)]
    assert qb.iloc[0].l10_stat_mean == pytest.approx(expected.completions.mean())
    # Current outcome does not affect any features computed before its kickoff.
    modified = weekly_frame.copy()
    modified.loc[(modified.player_id == "QB0") & (modified.season == 2021) & (modified.week == 11), "completions"] = 0
    other = build_feature_rows(validate_frame(modified), "COMPLETIONS")
    assert other.loc[(other.player_id == "QB0") & (other.week == 11) & (other.season == 2021), "l10_stat_mean"].iloc[0] == qb.iloc[0].l10_stat_mean


def test_role_specific_receptions_require_opportunities(weekly_frame):
    """Regression: role specific receptions require opportunities."""
    rows = build_feature_rows(validate_frame(weekly_frame), "RECEPTIONS")
    assert set(rows.player_id.str[:2]) == {"WR"}
    assert (rows.l10_opportunity_mean > 0).all()


def test_lack_of_holdout_data_prevents_training(weekly_frame):
    """Regression: lack of holdout data prevents training."""
    # 2025 is the untouched holdout: cannot synthesize a certification without it.
    frame = validate_frame(weekly_frame.loc[weekly_frame.season < 2025].copy())
    rows = build_feature_rows(frame, "PASS_ATTEMPTS")
    with pytest.raises(ValueError, match="NFL_PROP_HOLDOUT_ROWS_BELOW_MINIMUM"):
        build_candidate(rows, "PASS_ATTEMPTS", {"bundle_sha256": "a" * 64}, min_rows=(10, 10, 10))


def test_candidate_is_never_active_or_publishable(weekly_frame):
    """Regression: candidate is never active or publishable."""
    rows = build_feature_rows(validate_frame(weekly_frame), "RECEPTIONS")
    artifact = build_candidate(rows, "RECEPTIONS", {"bundle_sha256": "a" * 64}, min_rows=(10, 10, 10))
    assert artifact["stat_type"] == "RECEPTIONS"
    assert artifact["lifecycle_state"] == "CANDIDATE"
    for key in ("active", "promoted", "probability_publishable", "certification_eligible", "can_execute"):
        assert artifact[key] is False
    assert artifact["validation_metrics"]["train_rows"] > 0
    assert artifact["validation_metrics"]["holdout_season"] == 2025
    assert artifact["feature_schema_version"]
    assert len(artifact["artifact_checksum"]) == 64


def test_source_files_sha_and_seasons_checked(tmp_path, weekly_frame):
    """Regression: source files sha and seasons checked."""
    for season in range(2021, 2026):
        weekly_frame.loc[weekly_frame.season == season].to_csv(tmp_path / f"stats_player_week_{season}.csv", index=False)
    frame, provenance = load_source_files(tmp_path)
    assert len(frame) == len(weekly_frame)
    assert provenance["provider"] == "NFLVERSE_STATS_PLAYER"
    assert len(provenance["bundle_sha256"]) == 64
    assert len(provenance["assets"]) == 5
    (tmp_path / "stats_player_week_2024.csv").unlink()
    with pytest.raises(ValueError, match="NFL_PROP_SOURCE_FILE_MISSING:2024"):
        load_source_files(tmp_path)


@pytest.mark.parametrize("stat", sorted(ROUTES))
def test_every_missing_lane_produces_independent_held_candidate(weekly_frame, stat):
    """Regression: every missing lane produces independent held candidate."""
    rows = build_feature_rows(validate_frame(weekly_frame), stat)
    candidate = build_candidate(rows, stat, {"bundle_sha256": "b" * 64}, min_rows=(10, 10, 10))
    assert candidate["stat_type"] == stat
    assert candidate["model_artifact_version"].startswith("NFL_" + stat + "_")
    assert candidate["lifecycle_state"] == "CANDIDATE"
    assert candidate["certification_eligible"] is False
    assert candidate["probability_publishable"] is False
    assert candidate["can_execute"] is False
    assert candidate["validation_metrics"]["independent_calibration_certified"] is False


def test_week_19_marked_regular_season_is_rejected(weekly_frame):
    """Postseason week numbers cannot silently enter a REG training cohort."""
    altered = weekly_frame.copy()
    altered.loc[0, "week"] = 19
    with pytest.raises(ValueError, match="NFL_PROP_SOURCE_DATE_OUT_OF_SCOPE"):
        validate_frame(altered)


def test_two_game_ids_for_same_player_week_are_rejected(weekly_frame):
    """Changing the game ID cannot make a second outcome pregame evidence."""
    duplicate = weekly_frame.iloc[[0]].copy()
    duplicate.loc[duplicate.index[0], "game_id"] = "other_game_id"
    with pytest.raises(ValueError, match="NFL_PROP_SOURCE_DUPLICATE_PLAYER_EVENT"):
        validate_frame(pd.concat([weekly_frame, duplicate], ignore_index=True))


def test_valid_zero_opportunity_game_enters_outcome_and_following_l10(weekly_frame):
    """Zero observed opportunity is not automatically a missing observation."""
    changed = weekly_frame.copy()
    mask = (changed.player_id == "QB0") & (changed.season == 2021) & (changed.week == 12)
    changed.loc[mask, ["attempts", "completions", "passing_tds"]] = 0
    featured = build_feature_rows(validate_frame(changed), "COMPLETIONS")
    zero = featured[(featured.player_id == "QB0") & (featured.season == 2021) & (featured.week == 12)]
    assert len(zero) == 1
    assert zero.iloc[0].target == 0
    next_event = featured[(featured.player_id == "QB0") & (featured.season == 2021) & (featured.week == 13)].iloc[0]
    assert next_event.last_opportunity == 0
    assert next_event.last_stat == 0


def test_hash_and_parser_share_same_byte_snapshot(tmp_path, weekly_frame, monkeypatch):
    """A changing path must not cause fitted data to disagree with hash provenance."""
    import io
    import hashlib
    from train_nfl_missing_prop_candidates import _json_digest
    for season in range(2021, 2026):
        weekly_frame.loc[weekly_frame.season == season].to_csv(tmp_path / f"stats_player_week_{season}.csv", index=False)
    original = pd.read_csv
    observed = []

    def recording_read_csv(source, *args, **kwargs):
        """Capture exactly the bytes read by the CSV decoder."""
        assert isinstance(source, io.BytesIO), "parser reopened a mutable file instead of frozen bytes"
        observed.append(hashlib.sha256(source.getvalue()).hexdigest())
        return original(source, *args, **kwargs)

    monkeypatch.setattr("train_nfl_missing_prop_candidates.pd.read_csv", recording_read_csv)
    _, provenance = load_source_files(tmp_path)
    assert observed == [provenance["assets"][str(y)]["sha256"] for y in range(2021, 2026)]
    assert provenance["bundle_sha256"] == _json_digest(provenance["assets"])


@pytest.mark.parametrize("failure", ["wrong_season", "no_regular_season"])
def test_named_season_file_needs_its_own_regular_season_data(tmp_path, weekly_frame, failure):
    """Training coverage must not be inferred from filenames alone."""
    for season in range(2021, 2026):
        rows = weekly_frame.loc[weekly_frame.season == season].copy()
        if season == 2022 and failure == "wrong_season":
            rows["season"] = 2021
        if season == 2022 and failure == "no_regular_season":
            rows["season_type"] = "POST"
        rows.to_csv(tmp_path / f"stats_player_week_{season}.csv", index=False)
    with pytest.raises(ValueError, match=("NFL_PROP_SOURCE_SEASON_MISMATCH" if failure == "wrong_season"
                                           else "NFL_PROP_SOURCE_REGULAR_SEASON_EMPTY")):
        load_source_files(tmp_path)


def test_cli_output_must_not_clobber_a_frozen_input_file(tmp_path, weekly_frame, monkeypatch):
    """An output symlink must not overwrite any source evidence file."""
    from train_nfl_missing_prop_candidates import main
    import sys
    for season in range(2021, 2026):
        weekly_frame.loc[weekly_frame.season == season].to_csv(tmp_path / f"stats_player_week_{season}.csv", index=False)
    source = tmp_path / "stats_player_week_2021.csv"
    initial = source.read_bytes()
    alias = tmp_path / "alias.json"
    alias.symlink_to(source)
    monkeypatch.setattr(sys, "argv", ["trainer", "--input-dir", str(tmp_path), "--out", str(alias)])
    with pytest.raises(ValueError, match="NFL_PROP_SOURCE_OUTPUT_COLLISION"):
        main()
    assert source.read_bytes() == initial


def test_default_minimum_rejects_too_small_training_cohort(weekly_frame):
    """Default training minimum must not be accidentally lowered."""
    rows = build_feature_rows(validate_frame(weekly_frame), "PASS_ATTEMPTS")
    with pytest.raises(ValueError, match="NFL_PROP_TRAIN_ROWS_BELOW_MINIMUM"):
        build_candidate(rows, "PASS_ATTEMPTS", {"bundle_sha256": "a" * 64})


def test_default_minimum_rejects_too_small_calibration_cohort(weekly_frame):
    """Calibration-size minimum remains independent of train-size minimum."""
    rows = build_feature_rows(validate_frame(weekly_frame), "PASS_ATTEMPTS")
    train = rows.loc[rows.season.isin((2021, 2022, 2023))]
    added = pd.concat([train] * 6, ignore_index=True)
    candidates = pd.concat([added, rows.loc[rows.season.isin((2024, 2025))]], ignore_index=True)
    assert len(added) >= 1000
    with pytest.raises(ValueError, match="NFL_PROP_CAL_ROWS_BELOW_MINIMUM"):
        build_candidate(candidates, "PASS_ATTEMPTS", {"bundle_sha256": "a" * 64})

