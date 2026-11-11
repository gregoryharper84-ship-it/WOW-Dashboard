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
    assert set(ROUTES) == {
        "COMPLETIONS", "PASSING_TOUCHDOWNS", "PASS_ATTEMPTS", "RECEPTIONS", "RUSH_ATTEMPTS",
    }
    assert len({spec["target"] for spec in ROUTES.values()}) == 5
    assert set(spec["model_kind"] for spec in ROUTES.values()) == {"POISSON_COUNT_CANDIDATE_V1"}


def test_untrusted_missing_columns_fail_closed(weekly_frame):
    with pytest.raises(ValueError, match="NFL_PROP_SOURCE_COLUMNS_MISSING:completions"):
        validate_frame(weekly_frame.drop(columns=["completions"]))


def test_invalid_null_and_fractional_outcomes_fail_closed(weekly_frame):
    for bad in (float("nan"), -1, 1.5):
        altered = weekly_frame.copy()
        altered["completions"] = altered["completions"].astype(float)
        altered.loc[0, "completions"] = bad
        with pytest.raises(ValueError, match="NFL_PROP_SOURCE_COUNT_INVALID:completions"):
            validate_frame(altered)


def test_invalid_completed_vs_attempts_fail_closed(weekly_frame):
    altered = weekly_frame.copy()
    altered.loc[0, "completions"] = 100
    with pytest.raises(ValueError, match="NFL_PROP_SOURCE_COUNT_EXCEEDS_OPPORTUNITY:completions"):
        validate_frame(altered)


def test_duplicate_player_event_fails_closed(weekly_frame):
    altered = pd.concat([weekly_frame, weekly_frame.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="NFL_PROP_SOURCE_DUPLICATE_PLAYER_EVENT"):
        validate_frame(altered)


def test_only_prior_games_appear_in_pregame_features(weekly_frame):
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
    rows = build_feature_rows(validate_frame(weekly_frame), "RECEPTIONS")
    assert set(rows.player_id.str[:2]) == {"WR"}
    assert (rows.l10_opportunity_mean > 0).all()


def test_lack_of_holdout_data_prevents_training(weekly_frame):
    # 2025 is the untouched holdout: cannot synthesize a certification without it.
    frame = validate_frame(weekly_frame.loc[weekly_frame.season < 2025].copy())
    rows = build_feature_rows(frame, "PASS_ATTEMPTS")
    with pytest.raises(ValueError, match="NFL_PROP_HOLDOUT_ROWS_BELOW_MINIMUM"):
        build_candidate(rows, "PASS_ATTEMPTS", {"bundle_sha256": "a" * 64}, min_rows=(10, 10, 10))


def test_candidate_is_never_active_or_publishable(weekly_frame):
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
    rows = build_feature_rows(validate_frame(weekly_frame), stat)
    candidate = build_candidate(rows, stat, {"bundle_sha256": "b" * 64}, min_rows=(10, 10, 10))
    assert candidate["stat_type"] == stat
    assert candidate["model_artifact_version"].startswith("NFL_" + stat + "_")
    assert candidate["lifecycle_state"] == "CANDIDATE"
    assert candidate["certification_eligible"] is False
    assert candidate["probability_publishable"] is False
    assert candidate["can_execute"] is False
    assert candidate["validation_metrics"]["independent_calibration_certified"] is False
