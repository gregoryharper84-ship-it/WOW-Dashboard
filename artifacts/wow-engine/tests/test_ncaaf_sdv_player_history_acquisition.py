"""Fixture-only NCAAF public-release player-game research; no internet/credentials."""
from datetime import datetime, timezone
import pytest

from v17.ncaaf_sdv_player_history_acquisition import (
    CAN_EXECUTE, FIELDS, NCAAFSDVSourceHold, profile_player_rows, release_url,
)


def sample(category="passing", **kw):
    row = {x: None for x in FIELDS}
    row.update({
        "season": 2024, "contest_id": "ncaa-123", "espn_game_id": "401777000",
        "team_id": "99", "name": "Player Display", "category": category,
        "pass_yards": "312", "completions": "24", "pass_attempts": "38",
        "yds_rush": "-2", "rush_attempts": "15", "rec": "7",
        "receiving_yards": "106",
    })
    row.update(kw)
    return row


def profile(rows):
    return profile_player_rows(rows, season=2024, source_sha256="a" * 64,
                               retrieved_at="2026-10-29T12:00:00Z")


def test_seven_stat_families_count_without_authority():
    out = profile([sample(), sample(category="rushing"), sample(category="receiving")])
    assert out["candidate_rows"] == 3 and out["held_rows"] == 0
    assert set(out["stat_observation_counts"]) == {
        "PASSING_YARDS", "COMPLETIONS", "PASS_ATTEMPTS",
        "RUSHING_YARDS", "RUSH_ATTEMPTS", "RECEPTIONS", "RECEIVING_YARDS",
    }
    assert out["distinct_source_game_aliases"] == 1
    for key in ("official_result_verified", "fitted_training_authorized_by_probe",
                "historical_closing_line_verified", "probability_publishable",
                "can_execute", "source_rows_persisted_to_database",
                "upstream_name_is_official_player_identity"):
        assert out[key] is False
    assert out["model_consumption_receipts"] == []
    assert out["model_probability"] is None and out["calibrated_lower_bound"] is None
    assert CAN_EXECUTE is False


def test_bad_rows_are_held_separately_not_silently_filled():
    out = profile([sample(), sample(category="rushing", rush_attempts="-2"),
                   sample(category="receiving", contest_id="")])
    assert out["candidate_rows"] == 1 and out["held_rows"] == 2
    assert out["held_reasons"]["NCAAF_SDV_COUNT_NEGATIVE"] == 1
    assert out["held_reasons"]["NCAAF_SDV_SOURCE_IDENTITY_OR_CATEGORY_MISSING"] == 1


def test_no_stat_fallback_to_wrong_category():
    out = profile([sample(category="passing", pass_yards=None, completions=None,
                          pass_attempts="11")])
    assert out["stat_observation_counts"] == {"PASS_ATTEMPTS": 1}


@pytest.mark.parametrize("data,code", [
    ([sample(season=2025), sample()], None),
    ([sample(pass_yards="12.1")], "NCAAF_SDV_NO_USABLE_PLAYER_HISTORY"),
    ([sample(category="defense")], "NCAAF_SDV_NO_USABLE_PLAYER_HISTORY"),
])
def test_wrong_season_and_invalid_stats_fail_closed(data, code):
    if code:
        with pytest.raises(NCAAFSDVSourceHold, match=code):
            profile(data)
    else:
        result = profile(data)
        assert result["held_reasons"]["NCAAF_SDV_SEASON_MISMATCH"] == 1


def test_schema_drift_is_held_not_reinterpreted():
    row = sample()
    del row["contest_id"]
    with pytest.raises(NCAAFSDVSourceHold, match="NCAAF_SDV_SCHEMA_DRIFT"):
        profile([row])


def test_missing_training_data_does_not_fake_probability():
    with pytest.raises(NCAAFSDVSourceHold, match="NCAAF_SDV_NO_USABLE_PLAYER_HISTORY"):
        profile([])


@pytest.mark.parametrize("season", [None, "2024", 2001, 2031])
def test_release_season_has_finite_bounds(season):
    with pytest.raises(NCAAFSDVSourceHold, match="NCAAF_SDV_SEASON_INVALID"):
        release_url(season)


def test_url_exact_public_asset():
    assert release_url(2024) == (
        "https://github.com/sportsdataverse/sportsdataverse-data/releases/"
        "download/ncaa_mfb_player_stats/ncaa_mfb_player_stats_2024.parquet"
    )


def test_nonspecific_identity_never_promotes_research():
    o = profile([sample(name="Quarterback")])
    assert o["official_result_verified"] is False
    assert o["upstream_name_is_official_player_identity"] is False
    assert o["first_publication_before_game_proven"] is False
