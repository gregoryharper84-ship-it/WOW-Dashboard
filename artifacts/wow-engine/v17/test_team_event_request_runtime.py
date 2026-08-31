from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from v17.host_routing import LLP_TEAM_BETTING_ENGINE, WOW_BETTING_ENGINE
from v17.team_event_request_runtime import TeamEventRequest, score_team_event_request


class _FakeScoreEventRequest:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeEventApi:
    ScoreEventRequest = _FakeScoreEventRequest

    @staticmethod
    def score_event(req):
        return {
            "ok": True,
            "code": "REAL_FITTED_MODEL_PATH_PROVEN",
            "official_event_id": req.official_event_id,
            "probability_fields_withheld": True,
            "probability_publishable": False,
            "can_execute": False,
        }


def _future() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()


def _base(**overrides):
    payload = {
        "requester_host_identity": WOW_BETTING_ENGINE,
        "research_run_id": "rr-v17-team-event-test",
        "requested_slate_date": (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat(),
        "requested_timezone": "America/Chicago",
        "scan_stage": "PREGAME",
        "candidate_family": "TEAM_EVENT",
        "decision_intent": "BEST_SIDE",
        "event_key": "MLB:test-1",
        "official_event_id": "test-1",
        "event_start_time_utc": _future(),
        "sport": "MLB",
        "league": "MLB",
        "market_family": "OUTRIGHT_WINNER",
        "settlement_basis": "FULL_GAME_INCLUDING_EXTRA_INNINGS",
        "home_team": "Home Team",
        "away_team": "Away Team",
        "source_snapshot_id": "00000000-0000-0000-0000-000000000001",
        "sport_specific_evidence": {
            "venue": "Test Park",
            "home_starting_pitcher": "Home Starter",
            "away_starting_pitcher": "Away Starter",
            "home_starter_status": "PROBABLE",
            "away_starter_status": "PROBABLE",
            "home_lineup_status": "PROJECTED",
            "away_lineup_status": "PROJECTED",
        },
    }
    payload.update(overrides)
    return TeamEventRequest(**payload)


def test_wow_requester_team_event_is_controlled_by_llp():
    result = score_team_event_request(_base(), event_api=_FakeEventApi)
    assert result["requester_host_identity"] == WOW_BETTING_ENGINE
    assert result["controlling_engine_identity"] == LLP_TEAM_BETTING_ENGINE
    assert result["host_terminal_authority"] is False
    assert result["can_execute"] is False


def test_llp_requester_team_event_is_controlled_by_llp():
    result = score_team_event_request(
        _base(requester_host_identity=LLP_TEAM_BETTING_ENGINE), event_api=_FakeEventApi
    )
    assert result["requester_host_identity"] == LLP_TEAM_BETTING_ENGINE
    assert result["controlling_engine_identity"] == LLP_TEAM_BETTING_ENGINE


def test_unsupported_sport_fails_closed_without_probability():
    req = _base(
        sport="NFL",
        league="NFL",
        event_key="NFL:test-2",
        official_event_id="test-2",
        settlement_basis="FULL_GAME_OUTRIGHT",
        sport_specific_evidence={},
    )
    with pytest.raises(HTTPException) as exc_info:
        score_team_event_request(req, event_api=_FakeEventApi)
    assert exc_info.value.status_code == 409
    detail = exc_info.value.detail
    assert detail["code"] == "MODEL_UNAVAILABLE"
    assert detail["controlling_engine_identity"] == LLP_TEAM_BETTING_ENGINE
    assert detail["probability_publishable"] is False
    assert detail["market_probability_substitution_allowed"] is False
    assert detail["generic_reasoning_substitution_allowed"] is False
    assert detail["can_execute"] is False
    assert not any(
        key in detail
        for key in ("raw_probability", "calibrated_probability", "calibrated_lower_bound")
    )


def test_mlb_missing_sport_specific_evidence_is_acquisition_incomplete():
    req = _base(sport_specific_evidence={})
    with pytest.raises(HTTPException) as exc_info:
        score_team_event_request(req, event_api=_FakeEventApi)
    assert exc_info.value.status_code == 422
    detail = exc_info.value.detail
    assert detail["code"] == "RUN_INVALID_ACQUISITION_INCOMPLETE"
    assert detail["controlling_engine_identity"] == LLP_TEAM_BETTING_ENGINE
    assert detail["probability_publishable"] is False
    assert detail["can_execute"] is False


def test_valid_mlb_delegates_to_existing_governed_event_adapter():
    result = score_team_event_request(_base(), event_api=_FakeEventApi)
    assert result["code"] == "REAL_FITTED_MODEL_PATH_PROVEN"
    assert result["probability_fields_withheld"] is True
    assert result["controlling_engine_identity"] == LLP_TEAM_BETTING_ENGINE
    assert result["global_terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_unknown_requester_host_fails_closed():
    req = _base(requester_host_identity="RANDOM_GPT")
    with pytest.raises(HTTPException) as exc_info:
        score_team_event_request(req, event_api=_FakeEventApi)
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "UNAUTHORIZED_WOW_REQUESTER_HOST"
    assert exc_info.value.detail["can_execute"] is False


def test_same_team_event_identity_fails_closed():
    req = _base(home_team="Same Team", away_team="Same Team")
    with pytest.raises(HTTPException) as exc_info:
        score_team_event_request(req, event_api=_FakeEventApi)
    assert exc_info.value.status_code == 422
    assert "EVENT_PARTICIPANTS_NOT_MUTUALLY_EXCLUSIVE" in exc_info.value.detail["errors"]


def test_past_event_fails_closed():
    req = _base(
        event_start_time_utc=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    )
    with pytest.raises(HTTPException) as exc_info:
        score_team_event_request(req, event_api=_FakeEventApi)
    assert exc_info.value.status_code == 422
    assert "EVENT_NOT_PREGAME_OR_TIMESTAMP_INVALID" in exc_info.value.detail["errors"]
