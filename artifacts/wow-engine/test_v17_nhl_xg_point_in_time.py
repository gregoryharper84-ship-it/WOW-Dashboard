"""Deterministic hockey xG feature research: chronology, provenance, and holds."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import pytest

from v17.nhl_xg_point_in_time import NHLXGRecord, NHLXGResearchError, reconstruct_5v5_xg

BASE = datetime(2026, 10, 1, 18, tzinfo=timezone.utc)
SHA = "a" * 64


def game(i, home="BOS", away="NYR", home_xg=3.0, away_xg=1.0):
    dt = BASE + timedelta(days=i)
    return NHLXGRecord(
        official_event_id=f"202602{i:04d}", start_utc=dt.isoformat(),
        settled_utc=(dt + timedelta(hours=3)).isoformat(),
        captured_utc=(dt + timedelta(hours=4)).isoformat(),
        home_team=home, away_team=away,
        home_5v5_xg=home_xg, away_5v5_xg=away_xg,
        source_id="XG_HISTORICAL_RESEARCH", source_sha256=SHA,
        strength="5V5", game_type="REGULAR_SEASON",
    )


def sample():
    return [game(i) for i in range(5)] + [
        game(i + 5, "DAL", "SEA", 1.0, 2.0) for i in range(5)
    ]


def build(rows=None, **kw):
    return reconstruct_5v5_xg(
        target_event_id="2026020999", target_start_utc="2026-11-16T01:00:00+00:00",
        as_of_utc="2026-11-15T20:00:00+00:00", home_team="BOS",
        away_team="DAL", prior_games=sample() if rows is None else rows, **kw,
    )


def test_calculates_correct_symmetric_5v5_xg_deltas_without_authority():
    out = build()
    assert out["status"] == "RESEARCH_FEATURES_READY_NOT_CERTIFIED"
    assert out["features"]["home_5v5_xgf_per_game"] == pytest.approx(3.0)
    assert out["features"]["away_5v5_xgf_per_game"] == pytest.approx(1.0)
    assert out["features"]["xgf_per_game_delta"] == pytest.approx(2.0)
    assert out["features"]["xga_per_game_delta"] == pytest.approx(-1.0)
    assert out["features"]["xg_diff_per_game_delta"] == pytest.approx(3.0)
    assert out["features"]["xg_share_delta"] == pytest.approx(0.75 - 1 / 3)
    assert out["home_prior_games"] == 5 == out["away_prior_games"]
    assert len(out["lineage_digest"]) == 64
    assert out["source_rights_independently_verified"] is False
    assert out["eligible_for_fitted_training"] is False
    assert out["probability_publishable"] is False
    assert out["rank_eligible"] is False
    assert out["can_execute"] is False
    assert out["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert "calibrated_probability" not in out


def test_input_order_does_not_change_reconstruction_receipt():
    assert build() == build(list(reversed(sample())))


def test_most_recent_window_is_used_not_future_games():
    rows = [replace(game(-1, home_xg=20.0, away_xg=0.0), official_event_id="2026029998")] + sample()
    assert build(rows, window=5)["features"]["home_5v5_xgf_per_game"] == pytest.approx(3.0)
    assert build(rows, window=6)["features"]["home_5v5_xgf_per_game"] > 3


def test_missing_history_is_typed_hold_not_zero_imputation():
    out = build(sample()[:4], min_prior_games=5)
    assert out["status"] == "HELD"
    assert out["failure_code"] == "NHL_XG_HISTORY_INSUFFICIENT"
    assert out["features"] is None
    assert out["probability_publishable"] is False


@pytest.mark.parametrize("mutator,code", [
    (lambda rows: rows + [rows[0]], "NHL_XG_DUPLICATE_EVENT"),
    (lambda rows: [replace(rows[0], home_team="NYR")] + rows[1:], "NHL_XG_INVALID_TEAM_ID"),
    (lambda rows: [replace(rows[0], home_5v5_xg=float("nan"))] + rows[1:], "NHL_XG_VALUE_INVALID"),
    (lambda rows: [replace(rows[0], home_5v5_xg=True)] + rows[1:], "NHL_XG_VALUE_INVALID"),
    (lambda rows: [replace(rows[0], source_sha256="bad")] + rows[1:], "NHL_XG_SOURCE_INVALID"),
    (lambda rows: [replace(rows[0], strength="ALL")] + rows[1:], "NHL_XG_STRENGTH_INVALID"),
    (lambda rows: [replace(rows[0], captured_utc="2026-11-15T21:00:00Z")] + rows[1:], "NHL_XG_POST_CUTOFF"),
    (lambda rows: [replace(rows[0], settled_utc="2026-11-15T23:00:00Z")] + rows[1:], "NHL_XG_CHRONOLOGY_INVALID"),
    (lambda rows: [replace(rows[0], start_utc="2026-11-16T01:00:00Z")] + rows[1:], "NHL_XG_NOT_PRIOR"),
    (lambda rows: [replace(rows[0], official_event_id="2026020999")] + rows[1:], "NHL_XG_TARGET_LEAKAGE"),
    (lambda rows: [replace(rows[0], source_id="")] + rows[1:], "NHL_XG_SOURCE_INVALID"),
    (lambda rows: [replace(rows[0], game_type="PLAYOFF")] + rows[1:], "NHL_XG_GAME_TYPE_INVALID"),
])
def test_untrusted_or_leaking_historical_data_fails_closed(mutator, code):
    with pytest.raises(NHLXGResearchError) as e:
        build(mutator(sample()))
    assert e.value.code == code


@pytest.mark.parametrize("key,value,code", [
    ("as_of_utc", "2026-11-16T01:00:00Z", "NHL_XG_CUTOFF_INVALID"),
    ("as_of_utc", "2026-11-15T20:00:00", "NHL_XG_TIME_INVALID"),
    ("window", 0, "NHL_XG_WINDOW_INVALID"),
    ("min_prior_games", 11, "NHL_XG_WINDOW_INVALID"),
])
def test_cutoff_or_window_contracts(key, value, code):
    with pytest.raises(NHLXGResearchError) as e:
        if key == "as_of_utc":
            reconstruct_5v5_xg(target_event_id="2026020999",
                target_start_utc="2026-11-16T01:00:00Z", as_of_utc=value,
                home_team="BOS", away_team="DAL", prior_games=sample())
        else:
            build(**{key: value})
    assert e.value.code == code


def test_zero_xg_denominator_held_no_imputed_share():
    rows = [replace(r, home_5v5_xg=0.0, away_5v5_xg=0.0) for r in sample()]
    out = build(rows)
    assert out["status"] == "HELD"
    assert out["failure_code"] == "NHL_XG_SHARE_DENOMINATOR_ZERO"
    assert out["features"] is None


def test_source_byte_or_observation_update_changes_fingerprint():
    old = build()
    updated = [replace(sample()[0], source_sha256="b" * 64)] + sample()[1:]
    assert build(updated)["lineage_digest"] != old["lineage_digest"]


def test_excluded_teams_do_not_change_selected_digest():
    rows = sample() + [game(12, "FLA", "TBL", 7.0, 0.5)]
    assert build(rows) == build()


def test_accepted_sequence_types_beyond_builtin_list_tuple():
    from collections import UserList
    assert build(UserList(sample())) == build(sample())


def test_settlement_timestamp_is_part_of_replay_fingerprint():
    rows = sample()
    first = rows[0]
    changed = [replace(first, settled_utc=(BASE + timedelta(hours=3, minutes=1)).isoformat())] + rows[1:]
    assert build(changed)["lineage_digest"] != build()["lineage_digest"]


def test_whitespace_padded_duplicate_event_cannot_inflate_history():
    rows = sample()[:4] + [replace(sample()[0], official_event_id=sample()[0].official_event_id + " ")] + sample()[5:]
    with pytest.raises(NHLXGResearchError) as e:
        build(rows)
    assert e.value.code == "NHL_XG_EVENT_ID_INVALID"


@pytest.mark.parametrize("event_id", ["2026020001 ", " 2026020001", "202602001", "202602000a", "2026030001", "", None])
def test_reject_noncanonical_historical_event_ids(event_id):
    rows = [replace(sample()[0], official_event_id=event_id)] + sample()[1:]
    with pytest.raises(NHLXGResearchError) as e:
        build(rows)
    assert e.value.code == "NHL_XG_EVENT_ID_INVALID"


@pytest.mark.parametrize("target", ["2026020999 ", "202602099", "202602O999", "2026030999", None])
def test_reject_noncanonical_target_event_ids(target):
    with pytest.raises(NHLXGResearchError) as e:
        reconstruct_5v5_xg(
            target_event_id=target, target_start_utc="2026-11-16T01:00:00Z",
            as_of_utc="2026-11-15T20:00:00Z", home_team="BOS",
            away_team="DAL", prior_games=sample(),
        )
    assert e.value.code == "NHL_XG_TARGET_INVALID"
