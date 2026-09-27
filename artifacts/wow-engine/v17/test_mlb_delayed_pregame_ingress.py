from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import v17.team_event_probability_preservation as repair
from v17.host_routing import WOW_BETTING_ENGINE


class _RpcResult:
    def __init__(self, data):
        self.data = data


class _Rpc:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return _RpcResult(self.payload)


class _Client:
    def __init__(self, payload):
        self.payload = payload
        self.last_rpc = None
        self.last_params = None

    def rpc(self, name, params):
        self.last_rpc = name
        self.last_params = params
        return _Rpc(self.payload)


class _Api:
    def __init__(self, payload):
        self.client = _Client(payload)

    def get_client(self):
        return self.client


def _past() -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()


def _req(**updates):
    base = dict(
        requester_host_identity=WOW_BETTING_ENGINE,
        candidate_family="TEAM_EVENT",
        sport="MLB",
        league="MLB",
        official_event_id="823408",
        event_start_time_utc=_past(),
        home_team="Philadelphia Phillies",
        away_team="Tampa Bay Rays",
        market_prior=None,
        decision_intent="BEST_SIDE",
    )
    base.update(updates)
    return SimpleNamespace(**base)


def _pass_status():
    return {
        "status": "PASS",
        "code": "MLB_OFFICIAL_PREGAME_STATUS_PASS",
        "pregame": True,
        "official_abstract_state": "Preview",
        "official_detailed_state": "Delayed Start",
        "pitch_events": 0,
        "completed_plays": 0,
        "can_execute": False,
    }


def test_elapsed_mlb_clock_requires_fresh_official_pregame_pass():
    api = _Api(_pass_status())
    req = _req()

    result = repair._delayed_mlb_pregame_preflight(
        req,
        event_api=api,
        canonical_hydration_required=True,
    )

    assert result["status"] == "PASS"
    assert result["pregame"] is True
    assert api.client.last_rpc == "wow_mlb_current_pregame_status"
    assert api.client.last_params == {"p_official_event_id": "823408"}
    assert result["can_execute"] is False


def test_elapsed_mlb_live_state_fails_before_model_invocation():
    api = _Api({
        "status": "HOLD",
        "code": "EVENT_NOT_PREGAME",
        "pregame": False,
        "official_abstract_state": "Live",
        "official_detailed_state": "In Progress",
        "can_execute": False,
    })

    with pytest.raises(HTTPException) as exc_info:
        repair._delayed_mlb_pregame_preflight(
            _req(),
            event_api=api,
            canonical_hydration_required=True,
        )

    detail = exc_info.value.detail
    assert exc_info.value.status_code == 422
    assert detail["code"] == "MLB_TEAM_EVENT_EVENT_NOT_PREGAME"
    assert detail["blocker_code"] == "EVENT_NOT_PREGAME"
    assert detail["sport_model_invoked"] is False
    assert detail["probability_publishable"] is False
    assert detail["can_execute"] is False


def test_elapsed_mlb_status_transport_failure_fails_closed():
    class _BrokenApi:
        @staticmethod
        def get_client():
            raise RuntimeError("transport")

    with pytest.raises(HTTPException) as exc_info:
        repair._delayed_mlb_pregame_preflight(
            _req(),
            event_api=_BrokenApi(),
            canonical_hydration_required=True,
        )

    detail = exc_info.value.detail
    assert detail["code"] == "MLB_TEAM_EVENT_CURRENT_STATUS_UNAVAILABLE"
    assert detail["sport_model_invoked"] is False
    assert detail["can_execute"] is False


def test_elapsed_non_mlb_does_not_receive_mlb_clock_exception():
    result = repair._delayed_mlb_pregame_preflight(
        _req(sport="NFL", league="NFL"),
        event_api=_Api(_pass_status()),
        canonical_hydration_required=True,
    )
    assert result is None


def test_verified_delayed_mlb_override_is_exact_and_restored(monkeypatch):
    req = _req()
    api = _Api(_pass_status())
    original_aware_future = repair._base._aware_future
    calls = []

    def _fake_base(request, *, event_api, canonical_hydration_required=False):
        calls.append(request.official_event_id)
        assert repair._base._aware_future(request.event_start_time_utc) is True
        other_past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        assert repair._base._aware_future(other_past) is False
        return {
            "code": "MODEL_QUALIFIED_HOLD",
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        }

    monkeypatch.setattr(repair._base, "score_team_event_request", _fake_base)
    result = repair.score_team_event_request(
        req,
        event_api=api,
        canonical_hydration_required=True,
    )

    assert calls == ["823408"]
    assert result["official_delayed_pregame_status"]["status"] == "PASS"
    assert result["can_execute"] is False
    assert repair._base._aware_future is original_aware_future
