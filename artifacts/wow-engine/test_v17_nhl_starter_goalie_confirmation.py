"""NHL starting-goalie two-provider confirmation contract: negative-first tests."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from v17.nhl_candidate_preflight import NHLCandidatePreflightError
from v17.nhl_starter_goalie_confirmation import confirm_starting_goalies

START = datetime(2026, 11, 11, 0, 0, tzinfo=timezone.utc)
NOW = START - timedelta(minutes=30)


def _receipt(source, family, snap, digit, minutes_before=45, status="CONFIRMED"):
    return {
        "source_authority": "OFFICIAL", "source_id": source, "provider_family": family,
        "source_snapshot_id": snap, "source_payload_sha256": digit * 64,
        "official_event_id": "2026020500", "home_team_id": "BUF", "away_team_id": "MIN",
        "event_start_time_utc": START.isoformat(),
        "as_of": (START - timedelta(minutes=minutes_before)).isoformat(),
        "home": {"goalie_id": "8480000", "starting_status": status},
        "away": {"goalie_id": "8481111", "starting_status": status},
    }


def _pair():
    return [_receipt("NHL_GAMECENTER", "NHL_COM", "snap-a", "a"),
            _receipt("TEAM_OFFICIAL", "CLUB_SITE", "snap-b", "b", 40)]


def _code(receipts, now=NOW):
    with pytest.raises(NHLCandidatePreflightError) as err:
        confirm_starting_goalies(receipts, now=now)
    return err.value.code


def test_two_independent_official_confirmations_derive_confirmed_boundary():
    out = confirm_starting_goalies(_pair(), now=NOW)
    assert out["goalie_status"] == "CONFIRMED"
    assert out["goalie_identity_snapshot_id"].startswith("sha256:")
    assert out["derived_server_side"] is True
    assert out["can_execute"] is False and out["probability_publishable"] is False
    assert out == confirm_starting_goalies(list(reversed(_pair())), now=NOW)  # order independent


def test_caller_supplied_status_string_is_ignored():
    pair = _pair()
    for r in pair:
        r["goalie_status"] = "CONFIRMED"
        r["home"]["starting_status"] = "PROJECTED"
    assert _code(pair) == "NHL_STARTING_GOALIE_NOT_CONFIRMED"


@pytest.mark.parametrize("receipts", [[], None, "x", [_receipt("A", "F", "s", "a")]])
def test_not_exactly_two_receipts_hold(receipts):
    assert _code(receipts) == "NHL_STARTER_RECEIPTS_INCOMPLETE"


def test_three_receipts_hold():
    assert _code(_pair() + [_receipt("C", "G", "snap-c", "c")]) == "NHL_STARTER_RECEIPTS_INCOMPLETE"


@pytest.mark.parametrize("field,value", [
    ("source_id", "NHL_GAMECENTER"), ("provider_family", "nhl_com"),
    ("source_snapshot_id", "snap-a"), ("source_payload_sha256", "a" * 64),
])
def test_syndicated_or_duplicated_evidence_is_not_independent(field, value):
    pair = _pair()
    pair[1][field] = value
    assert _code(pair) == "NHL_STARTER_PROVIDERS_NOT_INDEPENDENT"


def test_non_official_source_holds():
    pair = _pair()
    pair[0]["source_authority"] = "AGGREGATOR"
    assert _code(pair) == "NHL_STARTER_SOURCE_NOT_OFFICIAL"


@pytest.mark.parametrize("key", ["official_event_id", "home_team_id", "away_team_id"])
def test_event_identity_mismatch_holds(key):
    pair = _pair()
    pair[1][key] = "OTHER"
    assert _code(pair) == "NHL_STARTER_EVENT_IDENTITY_MISMATCH"


def test_start_time_tolerance_boundary():
    pair = _pair()
    pair[1]["event_start_time_utc"] = (START + timedelta(seconds=60)).isoformat()
    confirm_starting_goalies(pair, now=NOW)
    pair[1]["event_start_time_utc"] = (START + timedelta(seconds=61)).isoformat()
    assert _code(pair) == "NHL_STARTER_START_TIME_DISAGREEMENT"


def test_projected_or_participation_only_status_holds():
    for status in ("PROJECTED", "EXPECTED", "PARTICIPATED", ""):
        pair = _pair()
        pair[0]["home"]["starting_status"] = status
        assert _code(pair) == "NHL_STARTING_GOALIE_NOT_CONFIRMED"


def test_conflicting_goalie_identity_holds():
    pair = _pair()
    pair[1]["away"]["goalie_id"] = "9999999"
    assert _code(pair) == "NHL_STARTER_GOALIE_CONFLICT"


def test_missing_goalie_holds():
    pair = _pair()
    del pair[0]["home"]
    assert _code(pair) == "NHL_STARTER_GOALIE_MISSING"


def test_verification_window_90_minutes_and_after_start():
    outside = [_receipt("A", "F1", "s1", "a", 91), _receipt("B", "F2", "s2", "b", 40)]
    assert _code(outside, now=START - timedelta(minutes=60)) == "NHL_STARTER_VERIFICATION_WINDOW"
    inside = [_receipt("A", "F1", "s1", "a", 90), _receipt("B", "F2", "s2", "b", 40)]
    confirm_starting_goalies(inside, now=START - timedelta(minutes=30))
    after = [_receipt("A", "F1", "s1", "a", -5), _receipt("B", "F2", "s2", "b", 40)]
    assert _code(after) == "NHL_STARTER_VERIFICATION_WINDOW"
    assert _code(_pair(), now=START + timedelta(minutes=1)) == "NHL_STARTER_VERIFICATION_WINDOW"


def test_stale_future_or_naive_timestamps_hold():
    future = [_receipt("A", "F1", "s1", "a", 20), _receipt("B", "F2", "s2", "b", 40)]
    assert _code(future, now=START - timedelta(minutes=30)) == "NHL_STARTER_EVIDENCE_STALE"
    naive = _pair()
    naive[0]["as_of"] = "2026-11-10T23:15:00"
    assert _code(naive) == "NHL_STARTER_RECEIPTS_INCOMPLETE"
    with pytest.raises(NHLCandidatePreflightError):
        confirm_starting_goalies(_pair(), now=datetime(2026, 11, 10, 23, 30))


def test_malformed_hash_and_missing_fields_hold():
    bad = _pair()
    bad[0]["source_payload_sha256"] = "xyz"
    assert _code(bad) == "NHL_STARTER_RECEIPTS_INCOMPLETE"
    for key in ("source_id", "provider_family", "source_snapshot_id", "official_event_id"):
        missing = deepcopy(_pair())
        missing[1][key] = ""
        assert _code(missing) == "NHL_STARTER_RECEIPTS_INCOMPLETE"


def test_module_has_no_io_scoring_or_execution_surface():
    import v17.nhl_starter_goalie_confirmation as m
    text = open(m.__file__).read()
    for banned in ("requests", "httpx", "supabase", "urllib", "open(", "score", "predict"):
        assert banned not in text.replace("never scores", "").replace("scorer", "")
    assert m.CAN_EXECUTE is False and m.PROBABILITY_PUBLISHABLE is False
