"""Synthetic cross-source audit contracts: no provider network or DB credentials."""
from copy import deepcopy
import pytest

from v17.ncaaf_sdv_espn_crosswalk import (
    CAN_EXECUTE, CrosswalkHold, NCAA_COLUMNS, ESPN_COLUMNS, _passing_pair,
    _stats, espn_release_url, reconcile,
)

H = "a" * 64
J = "b" * 64


def ncaa(category="passing", **changes):
    x = dict.fromkeys(NCAA_COLUMNS)
    x.update({
        "season": 2024, "contest_id": "123456", "espn_game_id": "401677189",
        "team_id": "324", "name": "Riley Leonard", "category": category,
        "pass_yards": "273", "completions": "15", "pass_attempts": "23",
        "yds_rush": "-2", "rush_attempts": "14",
        "rec": "5", "receiving_yards": "60",
    })
    x.update(changes)
    return x


def espn(category="passing", **changes):
    x = dict.fromkeys(ESPN_COLUMNS)
    x.update({
        "season": 2024, "game_id": 401677189,
        "team_id": 87, "athlete_id": 4683423,
        "athlete_name": "Riley Leonard", "category": category,
        "passing_yards": "273", "completions_passing_attempts": "15/23",
        "rushing_yards": "-2", "rushing_attempts": "14",
        "receptions": "5", "receiving_yards": "60",
    })
    x.update(changes)
    return x


def audit(left, right):
    return reconcile(left, right, season=2024, ncaa_sha256=H, espn_sha256=J)


def test_exact_event_name_category_and_three_stat_values_are_candidate_only():
    out = audit([ncaa()], [espn()])
    assert out["candidate_stat_category_rows"] == 1
    assert out["candidate_distinct_espn_game_player_ids"] == 1
    assert out["candidate_stat_parity_counts"] == {
        "COMPLETIONS": 1, "PASS_ATTEMPTS": 1, "PASSING_YARDS": 1}
    assert out["shared_event_ids"] == 1
    for field in ("espn_id_is_verified_cfbd_player_id_in_this_run",
                  "ncaa_team_id_crosswalk_verified", "source_vendor_independence_proven",
                  "model_training_authorized_by_crosswalk", "probability_publishable",
                  "can_execute", "pregame_line_verified"):
        assert out[field] is False
    assert out["model_probability"] is None
    assert CAN_EXECUTE is False


def test_name_only_match_with_wrong_game_is_held():
    o = audit([ncaa()], [espn(game_id=401677190)])
    assert o["candidate_stat_category_rows"] == 0
    assert o["hold_reason_counts"]["NCAA_KEY_UNMATCHED"] == 1
    assert o["shared_event_ids"] == 0


def test_two_exact_stats_with_one_source_metric_missing_still_candidate():
    o = audit([ncaa(pass_yards="not available")], [espn()])
    assert o["candidate_stat_category_rows"] == 1
    assert set(o["candidate_stat_parity_counts"]) == {"COMPLETIONS", "PASS_ATTEMPTS"}


def test_conflicting_stat_cannot_be_ignored_for_positive_matching():
    o = audit([ncaa(pass_yards="200")], [espn()])
    assert o["candidate_stat_category_rows"] == 0
    assert o["hold_reason_counts"]["STAT_VALUE_CONFLICT"] == 1


def test_same_display_name_twice_same_game_category_is_ambiguous():
    o = audit([ncaa(), ncaa(contest_id="different")], [espn()])
    assert o["candidate_stat_category_rows"] == 0
    assert o["hold_reason_counts"]["AMBIGUOUS_NAME_EVENT_CATEGORY"] == 2


def test_same_name_dual_espn_ids_is_ambiguous():
    o = audit([ncaa()], [espn(), espn(athlete_id=7777)])
    assert o["candidate_stat_category_rows"] == 0
    assert o["hold_reason_counts"]["AMBIGUOUS_NAME_EVENT_CATEGORY"] == 1


def test_weak_all_zero_statistics_are_not_sufficient_for_identity():
    o = audit([ncaa(category="receiving", rec="0", receiving_yards="0")],
              [espn(category="receiving", receptions="0", receiving_yards="0")])
    assert o["candidate_stat_category_rows"] == 0
    assert o["hold_reason_counts"]["WEAK_ZERO_ONLY_PARITY"] == 1


def test_rushing_negative_yards_count_parity_is_admitted():
    o = audit([ncaa(category="rushing", yds_rush="-2", rush_attempts="14")],
              [espn(category="rushing", rushing_yards="-2", rushing_attempts="14")])
    assert o["candidate_stat_parity_counts"]["RUSHING_YARDS"] == 1
    assert o["candidate_stat_parity_counts"]["RUSH_ATTEMPTS"] == 1


def test_invalid_attempts_never_default_to_zero():
    o = audit([ncaa(category="rushing", rush_attempts="nan")],
              [espn(category="rushing")])
    assert o["candidate_stat_category_rows"] == 0
    assert o["hold_reason_counts"]["INSUFFICIENT_SHARED_STATS"] == 1
    assert _stats(ncaa(category="rushing", rush_attempts="nan"), "rushing", "NCAA") == {
        "RUSHING_YARDS": -2}


def test_missing_athlete_id_is_not_promoted():
    o = audit([ncaa()], [espn(athlete_id=None)])
    assert o["candidate_stat_category_rows"] == 0
    assert o["hold_reason_counts"]["ESPN_ATHLETE_ID_MISSING"] == 1


def test_passing_pair_requires_valid_integer_completion_le_attempts():
    assert _passing_pair("15/23") == (15, 23)
    assert _passing_pair("24/23") == (None, None)
    assert _passing_pair("nan/25") == (None, None)


@pytest.mark.parametrize("bad", [None, "2024", 2031, 1999])
def test_season_scope_fail_closed(bad):
    with pytest.raises(CrosswalkHold, match="NCAAF_ESPN_SEASON_INVALID"):
        espn_release_url(bad)


def test_missing_schema_column_fails_closed():
    invalid = ncaa()
    invalid.pop("espn_game_id")
    with pytest.raises(CrosswalkHold, match="NCAAF_NCAA_SCHEMA_DRIFT"):
        audit([invalid], [espn()])


def test_bad_hash_fails_closed():
    with pytest.raises(CrosswalkHold, match="NCAAF_CROSSWALK_SOURCE_HASH_INVALID"):
        reconcile([ncaa()], [espn()], season=2024, ncaa_sha256="bad", espn_sha256=J)


def test_espn_release_url_is_pinned_to_public_source_family():
    assert espn_release_url(2024) == (
        "https://github.com/sportsdataverse/sportsdataverse-data/"
        "releases/download/espn_cfb_player_box/player_box_2024.parquet")
