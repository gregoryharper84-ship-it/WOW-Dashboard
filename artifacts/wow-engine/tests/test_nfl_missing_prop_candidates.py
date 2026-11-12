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


def test_cli_output_hardlink_collision_preserves_frozen_source(tmp_path, weekly_frame, monkeypatch):
    """Hard links must not bypass the frozen-input overwrite guard."""
    import os
    import sys
    from train_nfl_missing_prop_candidates import main

    for season in range(2021, 2026):
        season_rows = weekly_frame.loc[weekly_frame.season == season]
        season_rows.to_csv(tmp_path / f"stats_player_week_{season}.csv", index=False)
    source = tmp_path / "stats_player_week_2024.csv"
    initial = source.read_bytes()
    alias = tmp_path / "candidate_output.json"
    os.link(source, alias)
    assert os.stat(alias).st_ino == os.stat(source).st_ino
    assert alias.resolve() != source.resolve()

    monkeypatch.setattr(sys, "argv", ["trainer", "--input-dir", str(tmp_path), "--out", str(alias)])
    with pytest.raises(ValueError, match="NFL_PROP_SOURCE_OUTPUT_COLLISION:2024"):
        main()
    assert source.read_bytes() == initial

def test_frozen_source_digest_mismatch_is_rejected_before_parsing(tmp_path, weekly_frame):
    """An altered but schema-valid file cannot silently stand in for a pinned release."""
    import hashlib
    from train_nfl_missing_prop_candidates import load_source_files

    for season in range(2021, 2026):
        weekly_frame.loc[weekly_frame.season == season].to_csv(tmp_path / f"stats_player_week_{season}.csv", index=False)
    expected = {
        str(season): hashlib.sha256((tmp_path / f"stats_player_week_{season}.csv").read_bytes()).hexdigest()
        for season in range(2021, 2026)
    }
    frame, provenance = load_source_files(tmp_path, expected_sha256=expected)
    assert len(frame) == len(weekly_frame)
    assert provenance["caller_expected_hashes_matched"] is True
    assert provenance["release_hashes_verified"] is False
    assert provenance["source_identity_quarantine_independently_reviewed"] is False
    assert provenance["assets"]["2025"]["sha256"] == expected["2025"]

    path = tmp_path / "stats_player_week_2025.csv"
    changed = path.read_text().replace("QB 0", "QB changed", 1)
    assert changed != path.read_text()
    path.write_text(changed)
    with pytest.raises(ValueError, match="NFL_PROP_SOURCE_DIGEST_MISMATCH:2025"):
        load_source_files(tmp_path, expected_sha256=expected)


def test_cli_pins_authoritative_release_sha256_without_override():
    """The public-model candidate CLI must not accept arbitrary synthetic CSV hashes."""
    import inspect
    from train_nfl_missing_prop_candidates import EXPECTED_RELEASE_SHA256, main

    assert len(EXPECTED_RELEASE_SHA256) == 5
    assert set(EXPECTED_RELEASE_SHA256) == set(str(y) for y in range(2021, 2026))
    assert all(len(x) == 64 and all(c in "0123456789abcdef" for c in x) for x in EXPECTED_RELEASE_SHA256.values())
    # Avoid passing a user override for public-source integrity in the CLI.
    assert "expected_sha256=EXPECTED_RELEASE_SHA256" in inspect.getsource(main)


def _write_inputs_with_unattributed_rows(tmp_path, weekly_frame, *, count=18, overrides=None):
    """Create the observed *shape*, not a claim about actual nflverse row meaning."""
    for season in range(2021, 2026):
        rows = weekly_frame.loc[weekly_frame.season == season].copy()
        if season == 2021:
            phantom = rows.iloc[0].copy()
            phantom["player_id"] = np.nan
            phantom["player_display_name"] = np.nan
            phantom["position"] = "TEAM"
            for field in ("attempts", "completions", "passing_tds", "receptions", "targets", "carries"):
                phantom[field] = 0
            rows = pd.concat([rows, pd.DataFrame([
                {**phantom.to_dict(), "week": week, "game_id": f"2021_{week:02d}_BAL_KC"}
                for week in range(1, count + 1)
            ])], ignore_index=True)
            if overrides:
                for field, value in overrides.items():
                    rows.loc[rows.index[-1], field] = value
        rows.to_csv(tmp_path / f"stats_player_week_{season}.csv", index=False)


def test_unattributed_18_zero_stat_records_quarantined_with_provenance(tmp_path, weekly_frame):
    """No guessed player IDs, erased source rows, or hidden model eligibility."""
    _write_inputs_with_unattributed_rows(tmp_path, weekly_frame, count=18)
    frame, prov = load_source_files(tmp_path)
    assert len(frame) == len(weekly_frame)
    assert prov["source_identity_quarantine_independently_reviewed"] is False
    assert len(prov["unattributed_identity_quarantine"]) == 18
    assert prov["assets"]["2021"]["rows"] == 180 + 18
    assert prov["assets"]["2021"]["model_rows"] == 180
    assert prov["assets"]["2021"]["unattributed_identity_rows"] == 18
    assert {r["week"] for r in prov["unattributed_identity_quarantine"]} == set(range(1, 19))
    assert all(r["source_identity_unresolved"] and r["count_fields_all_zero"]
               and len(r["audit_sha256"]) == 64 for r in prov["unattributed_identity_quarantine"])
    assert not frame.player_id.isna().any()


@pytest.mark.parametrize("field,value,error", [
    ("attempts", 1, "NFL_PROP_SOURCE_UNATTRIBUTED_COUNT_NONZERO_OR_MISSING"),
    ("targets", float("nan"), "NFL_PROP_SOURCE_UNATTRIBUTED_COUNT_NONZERO_OR_MISSING"),
    ("position", "QB", "NFL_PROP_SOURCE_IDENTITY_INVALID:player_id"),
    ("player_display_name", "Unnamed QB", "NFL_PROP_SOURCE_IDENTITY_INVALID:player_id"),
    ("game_id", float("nan"), "NFL_PROP_SOURCE_IDENTITY_INVALID:player_id"),
    ("week", 19, "NFL_PROP_SOURCE_DATE_OUT_OF_SCOPE"),
    ("game_id", "2021_01_UNATTR", "NFL_PROP_SOURCE_IDENTITY_INVALID:game_id"),
    ("game_id", "2022_01_BAL_KC", "NFL_PROP_SOURCE_IDENTITY_INVALID:game_id"),
    ("game_id", "2021_02_BAL_KC", "NFL_PROP_SOURCE_IDENTITY_INVALID:game_id"),
    ("game_id", "2021_01_BAL_KC_EXTRA", "NFL_PROP_SOURCE_IDENTITY_INVALID:game_id"),
])
def test_unattributed_possible_participant_fails_closed(tmp_path, weekly_frame, field, value, error):
    """Unidentified player exposure must stop fitting rather than be excluded."""
    _write_inputs_with_unattributed_rows(tmp_path, weekly_frame, count=1, overrides={field: value})
    with pytest.raises(ValueError, match=error):
        load_source_files(tmp_path)


def test_pinned_release_only_flag_when_digest_map_matches_authoritative(tmp_path, weekly_frame):
    """An arbitrary matching hash map is not attestation to the official release."""
    import hashlib
    from train_nfl_missing_prop_candidates import EXPECTED_RELEASE_SHA256
    for season in range(2021, 2026):
        weekly_frame.loc[weekly_frame.season == season].to_csv(tmp_path / f"stats_player_week_{season}.csv", index=False)
    synthetic_expected = {
        str(year): hashlib.sha256((tmp_path / f"stats_player_week_{year}.csv").read_bytes()).hexdigest()
        for year in range(2021, 2026)
    }
    assert synthetic_expected != EXPECTED_RELEASE_SHA256
    _, provenance = load_source_files(tmp_path, expected_sha256=synthetic_expected)
    assert provenance["caller_expected_hashes_matched"] is True
    assert provenance["release_hashes_verified"] is False
    with pytest.raises(ValueError, match="NFL_PROP_SOURCE_DIGEST_MISMATCH"):
        load_source_files(tmp_path, expected_sha256=EXPECTED_RELEASE_SHA256)


def _anonymous_weekly_group_receipt_fixture():
    """Representative source-row pointers; synthetic values never prove origin."""
    from train_nfl_missing_prop_candidates import (
        EXPECTED_RELEASE_SHA256, _json_digest,
    )
    events = []
    for year in range(2021, 2026):
        for week in range(1, 19):
            row = {
                "source_line": 100 + week, "source_season": year,
                "game_id": f"{year}_{week:02d}_BAL_KC", "week": week,
                "position_token": "TEAM", "count_fields_all_zero": True,
                "source_identity_unresolved": True,
            }
            row["audit_sha256"] = _json_digest(row)
            events.append(row)
    return {
        "release_hashes_verified": True,
        "assets": {str(y): {"sha256": EXPECTED_RELEASE_SHA256[str(y)]}
                   for y in range(2021, 2026)},
        "unattributed_identity_quarantine": events,
    }


def test_pinned_anonymous_weekly_group_shape_is_never_semantic_approval():
    """A source pattern may be observed without certifying individual events."""
    from train_nfl_missing_prop_candidates import observe_anonymous_weekly_source_shape
    receipt = observe_anonymous_weekly_source_shape(
        _anonymous_weekly_group_receipt_fixture()
    )
    assert receipt["status"] == "HOLD_ANONYMOUS_WEEKLY_SOURCE_SEMANTICS"
    assert receipt["unattributed_rows"] == 90
    assert receipt["by_season"] == {str(y): 18 for y in range(2021, 2026)}
    assert receipt["one_unattributed_group_per_week_shape"] is True
    assert receipt["source_generator_equivalence_proven"] is False
    assert receipt["individual_player_outcomes_verified"] is False
    assert receipt["source_rights_independently_approved"] is False
    assert receipt["fitted_training_authorized"] is False
    assert receipt["can_execute"] is False


@pytest.mark.parametrize("mutation", [
    "digest", "source_year", "event_week", "missing_asset", "unverified",
])
def test_anonymous_group_diagnostic_fails_closed_on_corrupt_provenance(mutation):
    """Do not launder synthetic, malformed, or unpinned row evidence."""
    from train_nfl_missing_prop_candidates import observe_anonymous_weekly_source_shape
    receipt = _anonymous_weekly_group_receipt_fixture()
    row = receipt["unattributed_identity_quarantine"][0]
    if mutation == "digest":
        row["week"] = 9
    elif mutation == "source_year":
        row["source_season"] = 2022
    elif mutation == "event_week":
        row["game_id"] = "2021_02_BAL_KC"
    elif mutation == "missing_asset":
        del receipt["assets"]["2025"]
    else:
        receipt["release_hashes_verified"] = False
    # Re-sign semantic mutations so the test actually reaches identity validation.
    if mutation in {"source_year", "event_week"}:
        from train_nfl_missing_prop_candidates import _json_digest
        row["audit_sha256"] = _json_digest({
            k: v for k, v in row.items() if k != "audit_sha256"
        })
    with pytest.raises(ValueError, match="NFL_PROP_SOURCE_IDENTITY_INVALID:anonymous_group_provenance"):
        observe_anonymous_weekly_source_shape(receipt)



def test_anonymous_observer_rejects_lowercase_eligible_role_even_with_valid_digest():
    """An authenticated row cannot relabel a QB into an anonymous group by case."""
    from train_nfl_missing_prop_candidates import _json_digest, observe_anonymous_weekly_source_shape
    receipt = _anonymous_weekly_group_receipt_fixture()
    row = receipt["unattributed_identity_quarantine"][0]
    row["position_token"] = " qb "
    row["audit_sha256"] = _json_digest({k: v for k, v in row.items() if k != "audit_sha256"})
    with pytest.raises(ValueError, match="NFL_PROP_SOURCE_IDENTITY_INVALID:anonymous_group_provenance"):
        observe_anonymous_weekly_source_shape(receipt)


def test_cli_does_not_fit_or_write_with_unapproved_source(tmp_path, weekly_frame, monkeypatch):
    """Research candidate fitting must not bypass source identity/rights admission."""
    import hashlib
    import sys
    import train_nfl_missing_prop_candidates as trainer

    expected = {}
    for season in range(2021, 2026):
        file = tmp_path / f"stats_player_week_{season}.csv"
        weekly_frame.loc[weekly_frame.season == season].to_csv(file, index=False)
        expected[str(season)] = hashlib.sha256(file.read_bytes()).hexdigest()
    # Synthetic hashes only for the isolated test; no production pin rewritten.
    monkeypatch.setattr(trainer, "EXPECTED_RELEASE_SHA256", expected)
    def forbidden_fit(*args, **kwargs):
        raise AssertionError("source admission guard ran after model fitting")
    monkeypatch.setattr(trainer, "build_candidate", forbidden_fit)
    output = tmp_path / "unapproved_candidates.json"
    monkeypatch.setattr(sys, "argv", [
        "trainer", "--input-dir", str(tmp_path), "--out", str(output),
    ])
    with pytest.raises(ValueError, match="NFL_PROP_FITTING_SOURCE_ADMISSION_HOLD"):
        trainer.main()
    assert not output.exists()
