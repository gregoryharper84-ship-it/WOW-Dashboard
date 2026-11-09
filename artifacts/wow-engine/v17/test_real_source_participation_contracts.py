"""Source-backed participation contracts for NFL, MLB, and WNBA.

The corpus in ``v17/fixtures/participation`` reproduces documented provider
response shapes; it is **not** a network capture (see its README). These tests
therefore prove parsing, binding, and fail-closed behaviour against those
shapes. They are explicitly not evidence that a provider returned these values
on a given day, and must not be reported as live pregame verification.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import nfl_prop_auto_hydration as nfl
import prop_auto_hydration as mlb
import prop_auto_hydration_plate_appearances as pa
import wnba_prop_auto_hydration as wnba
import wnba_injury_status as wnba_status
from v17.player_availability_gate import (
    AVAILABILITY_AVAILABLE,
    AVAILABILITY_UNAVAILABLE,
    AVAILABILITY_UNVERIFIED,
    PLAYER_RULED_OUT,
    evaluate_player_availability,
    participation_evidence,
)

FIXTURES = Path(__file__).parent / "fixtures" / "participation"
EVENT_START = "2026-08-01T23:05:00+00:00"
CAPTURED = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
GAME_PK = 823001


class _Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload
        self.content = json.dumps(payload).encode("utf-8")
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


def _fixture(name: str):
    with (FIXTURES / name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _text_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Corpus integrity
# ---------------------------------------------------------------------------
def test_corpus_manifest_pins_every_fixture_and_admits_reconstruction():
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["captured"] is False
    assert "not" in manifest["capture_note"].lower()
    on_disk = {
        path.name
        for path in FIXTURES.iterdir()
        if path.is_file() and path.name not in {"manifest.json", "README.md"}
    }
    assert on_disk == set(manifest["files"])
    for name, meta in manifest["files"].items():
        digest = hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest()
        assert digest == meta["sha256"], f"{name} changed without a manifest update"
        assert meta["endpoint"].startswith("https://")


# ---------------------------------------------------------------------------
# MLB — identity, event binding, probable starter, posted/unposted/scratch
# ---------------------------------------------------------------------------
def test_mlb_schedule_binds_probable_starter_to_official_event_and_team():
    context = mlb._schedule_context(
        660271,
        event_start=mlb._aware(EVENT_START),
        http_get=lambda *a, **k: _Response(_fixture("mlb_schedule_probable_pitcher_20260801.json")),
    )
    assert context["official_game_pk"] == GAME_PK
    assert context["official_game_date"] == "2026-08-01T23:05:00Z"
    assert context["team"] == "LAD"
    assert context["team_id"] == 119
    assert context["opponent"] == "SD"
    assert context["venue"] == "Dodger Stadium"
    assert context["starter_status"] == "STARTER_PROBABLE_OFFICIAL_SCHEDULE"

    evidence = participation_evidence(
        basis="PROBABLE_STARTER",
        confirmation="PROBABLE",
        source="MLB_STATS_API_SCHEDULE_PROBABLE_PITCHER",
        captured_at=CAPTURED.isoformat(),
        fresh=True,
        athlete_id=660271,
        event_id=context["official_game_pk"],
        team=context["team"],
    )
    decision = evaluate_player_availability({"availability": evidence})
    assert decision.status == AVAILABILITY_AVAILABLE
    assert decision.basis == "PROBABLE_STARTER"
    assert decision.risk is True
    assert decision.provenance == {"athlete_id": "660271", "event_id": "823001", "team": "LAD"}


def test_mlb_schedule_without_the_pitcher_as_probable_is_refused():
    with pytest.raises(mlb.PropAutoHydrationError) as excinfo:
        mlb._schedule_context(
            660271,
            event_start=mlb._aware(EVENT_START),
            http_get=lambda *a, **k: _Response(
                {
                    "dates": [
                        {
                            "games": [
                                {
                                    "gamePk": GAME_PK,
                                    "gameDate": "2026-08-01T23:05:00Z",
                                    "teams": {
                                        "home": {
                                            "team": {"id": 119, "abbreviation": "LAD"},
                                            "probablePitcher": {"id": 999999},
                                        },
                                        "away": {"team": {"id": 135, "abbreviation": "SD"}},
                                    },
                                }
                            ]
                        }
                    ]
                }
            ),
        )
    assert excinfo.value.code == "MLB_STARTER_STATUS_UNRESOLVED"


def _pa_request_json(box_fixture: str):
    def request_json(url, *, params=None, http_get=None, **kwargs):
        if url.endswith("/people/592450"):
            return _fixture("mlb_people_current_team_592450.json")
        if url.endswith("/schedule"):
            return _fixture("mlb_schedule_probable_pitcher_20260801.json")
        if url.endswith("/boxscore"):
            game_pk = int(url.split("/game/")[1].split("/")[0])
            if game_pk == GAME_PK:
                return _fixture(box_fixture)
            return {
                "teams": {
                    "away": {"battingOrder": [605484, 605485]},
                    "home": {"battingOrder": [592450, 660271]},
                }
            }
        if url.endswith("/people/592450/stats"):
            splits = [
                {
                    "date": f"2026-07-{day:02d}",
                    "game": {"gamePk": 700000 + day},
                    "opponent": {"abbreviation": "SD"},
                    "gameNumber": 1,
                    "stat": {
                        "plateAppearances": 4,
                        "atBats": 3,
                        "hits": 1,
                        "baseOnBalls": 1,
                        "strikeOuts": 0,
                    },
                }
                for day in range(1, 11)
            ]
            return {"stats": [{"splits": splits}]}
        raise AssertionError(f"unexpected URL: {url}")

    return request_json


def _int_value(value, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class _PAError(RuntimeError):
    pass


def _hydrate_pa(box_fixture: str) -> dict:
    return pa.hydrate_mlb_plate_appearance_evidence(
        player="Mookie Betts",
        event_start_time=EVENT_START,
        resolve_player_id=lambda *a, **k: (592450, "Mookie Betts"),
        request_json=_pa_request_json(box_fixture),
        int_value=_int_value,
        error_type=_PAError,
        mlb_stats_api_base="https://statsapi.mlb.com/api/v1",
        evidence_version="PROP_EVIDENCE_V1",
        min_games=10,
        http_get=lambda *a, **k: _Response({}),
        now=CAPTURED,
    )


def test_posted_batting_order_clears_a_hitter_as_confirmed():
    payload = _hydrate_pa("mlb_boxscore_posted_order_823001.json")
    role = payload["role_status"]
    assert role["status"] == "STARTER_CONFIRMED"
    assert role["availability"]["basis"] == "POSTED_BATTING_ORDER"
    assert role["availability"]["confirmation"] == "CONFIRMED"
    assert role["availability"]["evidence_status"] == "RETRIEVED"
    assert role["availability"]["event_id"] == str(GAME_PK)
    assert role["availability"]["team"] == "LAD"
    assert payload["opportunity_ledger"]["batting_slot"] == 1
    decision = evaluate_player_availability(role)
    assert decision.status == AVAILABILITY_AVAILABLE
    assert decision.risk is False


def test_unposted_batting_order_holds_the_hitter():
    payload = _hydrate_pa("mlb_boxscore_unposted_order_823001.json")
    role = payload["role_status"]
    assert role["status"] == "LINEUP_UNCONFIRMED"
    assert role["availability"]["evidence_status"] == "OFFICIAL_BATTING_ORDER_NOT_POSTED"
    assert role["availability"]["basis"] is None
    decision = evaluate_player_availability(role)
    assert decision.status == AVAILABILITY_UNVERIFIED
    assert decision.reason == "AVAILABILITY_EVIDENCE_OFFICIAL_BATTING_ORDER_NOT_POSTED"


def test_late_scratch_is_distinguished_from_an_unposted_lineup():
    payload = _hydrate_pa("mlb_boxscore_late_scratch_823001.json")
    role = payload["role_status"]
    assert role["status"] == "NOT_IN_POSTED_BATTING_ORDER"
    assert role["confirmation_strength"] == "OFFICIAL_BATTING_ORDER_EXCLUDES_PLAYER"
    assert role["availability"]["evidence_status"] == "NOT_IN_POSTED_BATTING_ORDER"
    assert payload["opportunity_ledger"]["lineup_state"] == "PLAYER_NOT_IN_POSTED_ORDER"
    assert payload["opportunity_ledger"]["batting_slot"] is None
    decision = evaluate_player_availability(role)
    assert decision.status == AVAILABILITY_UNVERIFIED
    assert decision.reason == "AVAILABILITY_EVIDENCE_NOT_IN_POSTED_BATTING_ORDER"


# ---------------------------------------------------------------------------
# WNBA — report completeness, designations, unlisted clearance
# ---------------------------------------------------------------------------
def test_wnba_complete_report_clears_only_an_unlisted_player():
    text = _text_fixture("wnba_injury_report_20260801.txt")
    result = wnba._availability_from_report(
        text,
        player_name="Jackie Young",
        team_name="Las Vegas Aces",
        opponent_name="Indiana Fever",
        matchup="LVA@IND",
        game_date="08/01/2026",
    )
    assert result["availability"] == "NOT_LISTED_ON_FRESH_OFFICIAL_INJURY_REPORT"
    assert result["designation"] is None

    evidence = participation_evidence(
        basis="FRESH_OFFICIAL_REPORT_UNLISTED",
        source="WNBA_OFFICIAL_INJURY_REPORT",
        captured_at=CAPTURED.isoformat(),
        fresh=True,
        report_complete=True,
        athlete_id="1628932",
        event_id="1022600200",
        team="Las Vegas Aces",
    )
    decision = evaluate_player_availability({"availability": evidence})
    assert decision.status == AVAILABILITY_AVAILABLE
    assert decision.risk is False


@pytest.mark.parametrize(
    ("player", "team", "expected"),
    [("A'ja Wilson", "Las Vegas Aces", "OUT")],
)
def test_wnba_explicit_out_is_returned_as_a_ruling(player, team, expected):
    result = wnba._availability_from_report(
        _text_fixture("wnba_injury_report_20260801.txt"),
        player_name=player,
        team_name=team,
        opponent_name="Indiana Fever" if team == "Las Vegas Aces" else "Las Vegas Aces",
        matchup="LVA@IND",
        game_date="08/01/2026",
    )
    assert result == {"availability": "UNAVAILABLE", "designation": expected, "injury_reason": None}


@pytest.mark.parametrize(
    ("player", "team", "expected"),
    [
        ("Chelsea Gray", "Las Vegas Aces", "QUESTIONABLE"),
        ("Aliyah Boston", "Indiana Fever", "DOUBTFUL"),
    ],
)
def test_wnba_playable_designations_still_need_uncertified_model_handling(player, team, expected):
    with pytest.raises(wnba.WNBAPropHydrationError) as excinfo:
        wnba._availability_from_report(
            _text_fixture("wnba_injury_report_20260801.txt"),
            player_name=player,
            team_name=team,
            opponent_name="Indiana Fever" if team == "Las Vegas Aces" else "Las Vegas Aces",
            matchup="LVA@IND",
            game_date="08/01/2026",
        )
    assert excinfo.value.code == "WNBA_PLAYER_AVAILABILITY_NOT_CLEAR"
    assert excinfo.value.detail["designation"] == expected


def test_wnba_explicitly_available_player_is_cleared_from_the_report():
    result = wnba._availability_from_report(
        _text_fixture("wnba_injury_report_20260801.txt"),
        player_name="Kiah Stokes",
        team_name="Las Vegas Aces",
        opponent_name="Indiana Fever",
        matchup="LVA@IND",
        game_date="08/01/2026",
    )
    assert result == {"availability": "AVAILABLE", "designation": "AVAILABLE", "injury_reason": None}


def test_wnba_unsubmitted_team_report_is_refused():
    with pytest.raises(wnba.WNBAPropHydrationError) as excinfo:
        wnba._availability_from_report(
            _text_fixture("wnba_injury_report_not_submitted_20260801.txt"),
            player_name="Jackie Young",
            team_name="Las Vegas Aces",
            opponent_name="Indiana Fever",
            matchup="LVA@IND",
            game_date="08/01/2026",
        )
    assert excinfo.value.code == "WNBA_INJURY_REPORT_NOT_SUBMITTED"


def test_wnba_report_for_another_matchup_is_refused():
    with pytest.raises(wnba.WNBAPropHydrationError) as excinfo:
        wnba._availability_from_report(
            _text_fixture("wnba_injury_report_20260801.txt"),
            player_name="Jackie Young",
            team_name="Las Vegas Aces",
            opponent_name="Indiana Fever",
            matchup="LVA@NYL",
            game_date="08/01/2026",
        )
    assert excinfo.value.code == "WNBA_INJURY_REPORT_EVENT_UNRESOLVED"


def test_wnba_report_without_the_opponent_header_cannot_bound_the_section():
    # The target team is present and has submitted, but the page does not carry
    # the opponent header, so the section end is unknown and an absence inside
    # it must not be read as clearance.
    text = "Injury Report LVA@IND Las Vegas Aces A'ja Wilson - Out (Rest)"
    with pytest.raises(wnba.WNBAPropHydrationError) as excinfo:
        wnba._availability_from_report(
            text,
            player_name="Jackie Young",
            team_name="Las Vegas Aces",
            opponent_name="Indiana Fever",
            matchup="LVA@IND",
            game_date="08/01/2026",
        )
    assert excinfo.value.code == "WNBA_INJURY_REPORT_SECTION_INCOMPLETE"


def test_wnba_section_with_no_status_rows_is_incomplete():
    # A section with no parsable designation row proves nothing about a player
    # who is absent from it.
    text = "Injury Report LVA@IND Las Vegas Aces Indiana Fever Caitlin Clark - Available"
    with pytest.raises(wnba.WNBAPropHydrationError) as excinfo:
        wnba._availability_from_report(
            text,
            player_name="Jackie Young",
            team_name="Las Vegas Aces",
            opponent_name="Indiana Fever",
            matchup="LVA@IND",
            game_date="08/01/2026",
        )
    assert excinfo.value.code == "WNBA_INJURY_REPORT_SECTION_INCOMPLETE"


@pytest.mark.parametrize(
    "parse",
    [wnba._availability_from_report, wnba_status.availability_from_report],
)
def test_second_listed_team_cannot_clear_from_later_matchup_statuses(parse):
    """A later game's status must never certify a missing team report."""
    report = (
        "Injury Report\nLVA@IND\nLas Vegas Aces\nA'ja Wilson - Out\n"
        "Indiana Fever\n\nNYL@CHI\nNew York Liberty\nOther Player - Available\n"
        "Chicago Sky\nAnother Player - Available\n"
    )
    with pytest.raises(Exception) as excinfo:
        parse(
            report,
            player_name="Caitlin Clark",
            team_name="Indiana Fever",
            opponent_name="Las Vegas Aces",
            matchup="LVA@IND",
            game_date="08/01/2026",
        )
    assert excinfo.value.code == "WNBA_INJURY_REPORT_SECTION_INCOMPLETE"


@pytest.mark.parametrize(
    "parse",
    [wnba._availability_from_report, wnba_status.availability_from_report],
)
def test_second_listed_team_out_stays_explicit_before_next_game(parse):
    report = (
        "Injury Report\nLVA@IND\nLas Vegas Aces\nA'ja Wilson - Available\n"
        "Indiana Fever\nCaitlin Clark - Out\n\nNYL@CHI\n"
        "New York Liberty\nOther Player - Available\n"
    )
    result = parse(
        report,
        player_name="Caitlin Clark",
        team_name="Indiana Fever",
        opponent_name="Las Vegas Aces",
        matchup="LVA@IND",
        game_date="08/01/2026",
    )
    assert result["designation"] == "OUT"
    assert result["availability"] == "UNAVAILABLE"


@pytest.mark.parametrize(
    "parse",
    [wnba._availability_from_report, wnba_status.availability_from_report],
)
def test_wnba_extended_team_report_finds_out_beyond_1800_chars(parse):
    earlier = "".join(f"Reserve Player {i} - Available\n" for i in range(95))
    report = (
        "Injury Report\nLVA@IND\nLas Vegas Aces\n"
        + earlier + "Late Player - Out\n\nIndiana Fever\nOther Player - Available\n"
    )
    assert report.index("Late Player") - report.index("Las Vegas Aces") > 1800
    result = parse(
        report,
        player_name="Late Player",
        team_name="Las Vegas Aces",
        opponent_name="Indiana Fever",
        matchup="LVA@IND",
        game_date="08/01/2026",
    )
    assert result["availability"] == "UNAVAILABLE"
    assert result["designation"] == "OUT"


# ---------------------------------------------------------------------------
# NFL — designation on the official athlete record
# ---------------------------------------------------------------------------
def _nfl_availability(fixture: str, athlete_id: str = "4241457") -> dict:
    return nfl._athlete_availability(
        athlete_id,
        http_get=lambda *a, **k: _Response(_fixture(fixture)),
        captured=CAPTURED,
    )


def test_nfl_empty_athlete_profile_injuries_cannot_certify_a_complete_report():
    evidence = nfl.bind_availability_to_event(
        _nfl_availability("espn_nfl_athlete_clear_2026.json", athlete_id="4241459"),
        team="TEST",
        event_id="401-test",
    )
    assert evidence.get("basis") is None
    assert evidence["report_complete"] is False
    assert evidence["fresh"] is False
    assert evidence["athlete_id"] == "4241459"
    assert evidence["source_endpoint"].endswith("/athletes/4241459")
    assert evidence["evidence_status"] == "OFFICIAL_REPORT_COMPLETENESS_UNVERIFIED"
    assert evaluate_player_availability({"availability": evidence}).status == AVAILABILITY_UNVERIFIED


def test_nfl_missing_injuries_section_stays_unverified_after_event_binding():
    evidence = nfl.bind_availability_to_event(
        nfl._athlete_availability(
            "4241459",
            http_get=lambda *a, **k: _Response({"athlete": {"id": "4241459"}}),
            captured=CAPTURED,
        ),
        team="TEST",
        event_id="401-test",
    )
    assert evidence["evidence_status"] == "OFFICIAL_REPORT_COMPLETENESS_UNVERIFIED"
    assert evaluate_player_availability({"availability": evidence}).status == AVAILABILITY_UNVERIFIED


def test_nfl_response_for_another_athlete_cannot_authorize_clearance():
    evidence = nfl.bind_availability_to_event(
        _nfl_availability("espn_nfl_athlete_clear_2026.json", athlete_id="4241457"),
        team="TEST",
        event_id="401-test",
    )
    assert evidence["evidence_status"] == "ATHLETE_IDENTITY_UNVERIFIED"
    assert evaluate_player_availability({"availability": evidence}).status == AVAILABILITY_UNVERIFIED


def test_nfl_unbound_empty_report_remains_unverified():
    evidence = _nfl_availability("espn_nfl_athlete_clear_2026.json", athlete_id="4241459")
    decision = evaluate_player_availability({"availability": evidence})
    assert decision.status == AVAILABILITY_UNVERIFIED
    assert decision.reason == "AVAILABILITY_EVIDENCE_OFFICIAL_REPORT_COMPLETENESS_UNVERIFIED"



def test_nfl_out_designation_rules_the_player_out():
    evidence = _nfl_availability("espn_nfl_athlete_injuries_2026.json")
    assert evidence["designation"] == "OUT"
    decision = evaluate_player_availability({"availability": evidence})
    assert decision.status == AVAILABILITY_UNAVAILABLE
    assert decision.blocker == PLAYER_RULED_OUT


def test_nfl_unparsed_designation_is_unverified():
    evidence = _nfl_availability("espn_nfl_athlete_unparsed_2026.json", athlete_id="4241458")
    assert evidence["evidence_status"] == "INJURY_DESIGNATION_UNPARSED"
    decision = evaluate_player_availability({"availability": evidence})
    assert decision.status == AVAILABILITY_UNVERIFIED
    assert decision.reason == "AVAILABILITY_EVIDENCE_INJURY_DESIGNATION_UNPARSED"


def test_nfl_failed_retrieval_is_unverified_not_available():
    def explode(*args, **kwargs):
        raise RuntimeError("provider down")

    evidence = nfl._athlete_availability("4241457", http_get=explode, captured=CAPTURED)
    assert evidence["evidence_status"] == "RETRIEVAL_FAILED"
    assert evidence["reason"] == "INJURY_REPORT_RETRIEVAL_FAILED:RuntimeError"
    decision = evaluate_player_availability({"availability": evidence})
    assert decision.status == AVAILABILITY_UNVERIFIED
    assert decision.reason == "AVAILABILITY_EVIDENCE_RETRIEVAL_FAILED"


# ---------------------------------------------------------------------------
# Phase 2 — the dimensions stay separate
# ---------------------------------------------------------------------------
def test_roster_membership_alone_does_not_clear():
    decision = evaluate_player_availability({"status": "ACTIVE_CURRENT_ESPN_ROSTER"})
    assert decision.status == AVAILABILITY_UNVERIFIED
    assert decision.reason == "AVAILABILITY_EVIDENCE_NOT_SUPPLIED"


def test_starter_basis_is_a_participation_fact_not_an_injury_clearance():
    evidence = participation_evidence(
        basis="CONFIRMED_STARTER",
        confirmation="CONFIRMED",
        source="TEST_OFFICIAL_LINEUP",
        captured_at=CAPTURED.isoformat(),
        fresh=True,
        athlete_id="592450",
        event_id="823001",
        team="LAD",
    )
    decision = evaluate_player_availability({"availability": evidence})
    assert decision.status == AVAILABILITY_AVAILABLE
    assert decision.designation is None
    assert decision.basis == "CONFIRMED_STARTER"
    assert decision.risk is False


def test_predicted_workload_does_not_clear_availability():
    decision = evaluate_player_availability(
        {
            "status": "CURRENT_ROSTER_CONFIRMED",
            "opportunity_ledger": {"l10_minutes_mean": 31.4, "l5_minutes_mean": 33.0},
        }
    )
    assert decision.status == AVAILABILITY_UNVERIFIED


def test_questionable_and_doubtful_remain_playable_risk_flags():
    for designation in ("QUESTIONABLE", "DOUBTFUL"):
        decision = evaluate_player_availability(
            {
                "availability": participation_evidence(
                    designation=designation,
                    source="TEST",
                    captured_at=CAPTURED.isoformat(),
                    fresh=True,
                    athlete_id="1",
                    event_id="2",
                    team="TEST",
                )
            }
        )
        assert decision.status == AVAILABILITY_AVAILABLE, designation
        assert decision.risk is True, designation
        assert decision.designation == designation
