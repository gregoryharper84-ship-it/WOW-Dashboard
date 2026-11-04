"""Offline negative and candidate-only source-reconciliation checks."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "soccer_tennis_identity_source_preflight",
    Path(__file__).resolve().parents[1] / "v17" / "soccer_tennis_identity_source_preflight.py",
)
assert _SPEC and _SPEC.loader
module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(module)

AT = "2026-11-05T01:00:00Z"
SOCCER_XWALK = {"start_time_utc": AT, "competition_id": 2021, "home_team_id": 64, "away_team_id": 65}
TENNIS_XWALK = {"start_time_utc": AT, "tour": "ATP", "tournament_id": 21357, "player1_id": 22807, "player2_id": 67535, "singles": True}
SOCCER = {"resultSet": {"count": 1}, "matches": [
    {"id": 10023, "utcDate": AT, "status": "TIMED", "competition": {"id": 2021}, "homeTeam": {"id": 64}, "awayTeam": {"id": 65}}
]}
TENNIS = {"data": [{"matchId": 100123, "startTime": AT, "status": "SCHEDULED", "tournament": {"id": 21357}, "player1": {"id": 22807}, "player2": {"id": 67535}}], "hasNextPage": False, "pageNo": 1}


def run(sport, payload, crosswalk):
    return module.preflight_fixture_source(sport=sport, source_id=module.SOURCES[sport], source_payload=payload, crosswalk=crosswalk, as_of_utc="2026-11-04T20:00:00Z")


def test_fixture_match_stays_alias_only_and_cannot_score():
    for sport, payload, crosswalk, source_match in (
        ("SOCCER", SOCCER, SOCCER_XWALK, "10023"),
        ("TENNIS", TENNIS, TENNIS_XWALK, "100123"),
    ):
        out = run(sport, payload, crosswalk)
        assert out["status"] == "SOURCE_MATCH_CANDIDATE_ONLY"
        assert out["candidate_source_match_id"] == source_match
        assert out["official_event_id"] is None
        assert out["canonical_identity_status"] == "ALIAS_ONLY_UNRESOLVED"
        assert out["probability_publishable"] is False and out["rank_eligible"] is False
        assert out["model_probability"] is None and out["can_execute"] is False
        assert out["source_rights_independently_attested"] is False
        assert out["canonicalization_permitted"] is False


def test_market_only_alias_never_claims_identity():
    for sport, payload in (("SOCCER", SOCCER), ("TENNIS", TENNIS)):
        result = run(sport, payload, {"provider_event_id": "sportsbook-1", "start_time_utc": AT})
        assert result["status"] == "HOLD"
        assert result["code"] == "SOURCE_EVIDENCE_UNVERIFIED"


def test_unknown_source_is_rejected():
    out = module.preflight_fixture_source(sport="SOCCER", source_id="THE_ODDS_API", source_payload=SOCCER, crosswalk=SOCCER_XWALK, as_of_utc="2026-11-04T20:00:00Z")
    assert out["code"] == "SOURCE_EVIDENCE_UNVERIFIED"


def test_soccer_home_away_are_not_swappable():
    bad = copy.deepcopy(SOCCER)
    bad["matches"][0]["homeTeam"], bad["matches"][0]["awayTeam"] = bad["matches"][0]["awayTeam"], bad["matches"][0]["homeTeam"]
    assert run("SOCCER", bad, SOCCER_XWALK)["code"] == "SOURCE_FIXTURE_NOT_FOUND"


def test_tennis_player_sides_are_order_independent():
    xwalk = {**TENNIS_XWALK, "player1_id": 67535, "player2_id": 22807}
    assert run("TENNIS", TENNIS, xwalk)["status"] == "SOURCE_MATCH_CANDIDATE_ONLY"


def test_tennis_doubles_never_qualifies():
    assert run("TENNIS", TENNIS, {**TENNIS_XWALK, "singles": False})["code"] == "SOURCE_FIXTURE_INELIGIBLE"


def test_partial_source_pages_hold():
    soccer = {**SOCCER, "resultSet": {"count": 2}}
    tennis = {**TENNIS, "hasNextPage": True}
    assert run("SOCCER", soccer, SOCCER_XWALK)["code"] == "SOURCE_FIXTURE_PARTIAL"
    assert run("TENNIS", tennis, TENNIS_XWALK)["code"] == "SOURCE_FIXTURE_PARTIAL"


def test_retirement_withdrawal_and_postponement_fail_closed():
    soccer = copy.deepcopy(SOCCER)
    soccer["matches"][0]["status"] = "POSTPONED"
    assert run("SOCCER", soccer, SOCCER_XWALK)["code"] == "SOURCE_FIXTURE_INELIGIBLE"
    for key in ("retirement", "withdrawal", "walkover"):
        tennis = copy.deepcopy(TENNIS)
        tennis["data"][0][key] = True
        assert run("TENNIS", tennis, TENNIS_XWALK)["code"] == "SOURCE_FIXTURE_INELIGIBLE"


def test_start_time_mismatch_and_unaware_times_hold():
    soccer = copy.deepcopy(SOCCER)
    soccer["matches"][0]["utcDate"] = "2026-11-05T03:00:00Z"
    assert run("SOCCER", soccer, SOCCER_XWALK)["code"] == "SOURCE_FIXTURE_NOT_FOUND"
    assert run("SOCCER", SOCCER, {**SOCCER_XWALK, "start_time_utc": "2026-11-05T01:00:00"})["code"] == "SOURCE_FIXTURE_SCHEMA_INVALID"


def test_duplicate_fixture_matches_are_ambiguous():
    duplicate = copy.deepcopy(TENNIS)
    duplicate["data"].append({**duplicate["data"][0], "matchId": 100124})
    assert run("TENNIS", duplicate, TENNIS_XWALK)["code"] == "SOURCE_FIXTURE_AMBIGUOUS"


def test_schema_corruption_holds_entire_source():
    tennis = copy.deepcopy(TENNIS)
    tennis["data"].append({"unexpected": "shape"})
    assert run("TENNIS", tennis, TENNIS_XWALK)["code"] == "SOURCE_FIXTURE_SCHEMA_INVALID"


def test_started_fixture_is_never_a_pregame_candidate():
    out = module.preflight_fixture_source(sport="SOCCER", source_id=module.SOURCES["SOCCER"], source_payload=SOCCER, crosswalk=SOCCER_XWALK, as_of_utc="2026-11-05T01:00:00Z")
    assert out["code"] == "SOURCE_FIXTURE_INELIGIBLE"


def test_actual_source_start_already_passed_even_if_alias_start_is_future():
    soccer = copy.deepcopy(SOCCER)
    soccer["matches"][0]["utcDate"] = "2026-11-05T00:40:00Z"
    out = module.preflight_fixture_source(sport="SOCCER", source_id=module.SOURCES["SOCCER"], source_payload=soccer, crosswalk=SOCCER_XWALK, as_of_utc="2026-11-05T00:50:00Z")
    assert out["code"] == "SOURCE_FIXTURE_INELIGIBLE"


def test_tennis_second_page_alone_is_not_a_complete_fixture_corpus():
    tennis = {**TENNIS, "pageNo": 2}
    assert run("TENNIS", tennis, TENNIS_XWALK)["code"] == "SOURCE_FIXTURE_PARTIAL"
