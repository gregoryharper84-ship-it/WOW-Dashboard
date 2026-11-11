"""Offline negative and matching-source fixtures; never live API tests."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from v17.nhl_pregame_gamecenter_capture import capture_nhl_pregame_gamecenter

NOW = datetime(2026, 11, 9, 22, 30, tzinfo=timezone.utc)
EVENT_ID = "2026020219"
URL = f"https://api-web.nhle.com/v1/gamecenter/{EVENT_ID}/boxscore"


def identity(**kwargs):
    val = {
        "event_id": EVENT_ID,
        "home_team": "BOS",
        "away_team": "NYR",
        "event_start_time": (NOW + timedelta(hours=3)).isoformat(),
        "identity_provider": "NHL_PUBLIC_WEB_API",
        "identity_source": "https://api-web.nhle.com/v1/club-schedule-season/BOS/20262027",
        "identity_verified_at": (NOW - timedelta(minutes=2)).isoformat(),
        "market_features_used": False,
        "prediction_authority": False,
        "can_execute": False,
    }
    val.update(kwargs)
    return val


def payload(**kwargs):
    val = {
        "id": int(EVENT_ID),
        "gameState": "FUT",
        "startTimeUTC": (NOW + timedelta(hours=3)).isoformat(),
        "homeTeam": {"abbrev": "BOS"},
        "awayTeam": {"abbrev": "NYR"},
        "playerByGameStats": {"homeTeam": {"goalies": [{"playerId": 1, "starter": True}]},
                              "awayTeam": {"goalies": [{"playerId": 2, "starter": True}]}},
    }
    val.update(kwargs)
    return val


class Response:
    status_code = 200
    url = URL

    def __init__(self, data=None, *, raw=None, status=200, url=URL):
        self.status_code = status
        self.url = url
        self.content = json.dumps(data if data is not None else payload()).encode() if raw is None else raw


def capture(*, response=None, event_id=EVENT_ID, expected=None, now=NOW):
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return response if response is not None else Response()
    result = capture_nhl_pregame_gamecenter(
        event_id, resolved_identity=identity() if expected is None else expected,
        as_of_utc=now, get=get,
    )
    return result, calls


def test_official_future_capture_still_holds_goalie_even_if_boxscore_has_starter_flag():
    resp = Response()
    result, calls = capture(response=resp)
    assert result["code"] == "NHL_GOALIE_STARTER_CONFIRMATION_REQUIRED"
    assert result["source_content_sha256"] == hashlib.sha256(resp.content).hexdigest()
    assert result["gamecenter_source_status"] == "OFFICIAL_PREGAME_CAPTURED_RESEARCH_ONLY"
    assert result["goalie_status"] == "UNVERIFIED"
    assert result["source_snapshot_id"] is None
    assert result["timestamp_attestation"] == "RUNNER_CLOCK_ONLY_NOT_INDEPENDENT"
    assert result["fitted_specialist_invoked"] is False
    assert result["probability_publishable"] is result["rank_eligible"] is result["can_execute"] is False
    assert len(calls) == 1 and calls[0][0] == URL and calls[0][1]["allow_redirects"] is False


@pytest.mark.parametrize("field,value,code", [
    ("id", 2026020220, "NHL_GAMECENTER_CANONICAL_IDENTITY_MISMATCH"),
    ("homeTeam", {"abbrev": "CAR"}, "NHL_GAMECENTER_CANONICAL_IDENTITY_MISMATCH"),
    ("awayTeam", {"abbrev": "BOS"}, "NHL_GAMECENTER_CANONICAL_IDENTITY_MISMATCH"),
    ("gameState", "LIVE", "NHL_EVENT_NOT_PREGAME"),
    ("gameState", "OFF", "NHL_EVENT_NOT_PREGAME"),
    ("startTimeUTC", (NOW + timedelta(hours=4)).isoformat(), "NHL_GAMECENTER_START_TIME_MISMATCH"),
    ("startTimeUTC", "not-a-date", "NHL_GAMECENTER_START_TIME_MISMATCH"),
])
def test_cross_event_started_and_schema_drift_hold(field, value, code):
    res, _ = capture(response=Response(payload(**{field: value})))
    assert res["code"] == code
    assert res["can_execute"] is False


@pytest.mark.parametrize("event_id", ["not-event", "202602021", "2026020219\u0660", 2026020219])
def test_provider_event_alias_or_invalid_id_never_fetches(event_id):
    res, calls = capture(event_id=event_id)
    assert res["code"] == "NHL_GAMECENTER_EVENT_ID_INVALID"
    assert calls == []


@pytest.mark.parametrize("field,value", [
    ("event_id", "0000000000"), ("home_team", "NYR"),
    ("identity_provider", "ESPN"), ("identity_source", "https://other.example/stolen"),
    ("identity_verified_at", (NOW - timedelta(hours=26)).isoformat()),
    ("can_execute", True), ("prediction_authority", True),
])
def test_forged_or_stale_identity_receipt_rejected_before_fetch(field, value):
    res, calls = capture(expected=identity(**{field: value}))
    assert res["code"] == "NHL_CANONICAL_IDENTITY_RECEIPT_MISMATCH"
    assert calls == []


def test_caller_field_is_not_an_authorized_resolver_receipt():
    res, calls = capture(expected={"resolved_identity": identity()})
    assert res["code"] == "NHL_CANONICAL_IDENTITY_RECEIPT_MISMATCH"
    assert calls == []


@pytest.mark.parametrize("response,code", [
    (Response(status=429), "NHL_OFFICIAL_GAMECENTER_RATE_LIMITED"),
    (Response(status=302), "NHL_OFFICIAL_GAMECENTER_REDIRECT_REJECTED"),
    (Response(url="https://elsewhere.example/boxscore"), "NHL_OFFICIAL_GAMECENTER_SOURCE_UNOBTAINABLE"),
    (Response(raw=b"not-json"), "NHL_OFFICIAL_GAMECENTER_PAYLOAD_INVALID"),
    (Response(raw=b""), "NHL_OFFICIAL_GAMECENTER_PAYLOAD_INVALID"),
    (Response(raw=b"[]"), "NHL_OFFICIAL_GAMECENTER_SCHEMA_DRIFT"),
])
def test_network_source_failures_never_promote(response, code):
    res, _ = capture(response=response)
    assert res["code"] == code
    assert res["source_snapshot_id"] is None
    assert res["probability_publishable"] is False


def test_no_network_on_past_official_start_even_if_gamecenter_would_claim_future():
    res, calls = capture(now=NOW + timedelta(hours=4))
    assert res["code"] == "NHL_EVENT_NOT_PREGAME"
    assert calls == []


def test_timeout_never_becomes_model_unavailable():
    def fails(*args, **kwargs):
        raise TimeoutError("upstream")
    res = capture_nhl_pregame_gamecenter(
        EVENT_ID, resolved_identity=identity(), as_of_utc=NOW, get=fails,
    )
    assert res["code"] == "NHL_OFFICIAL_GAMECENTER_SOURCE_UNOBTAINABLE"
    assert res["fitted_specialist_invoked"] is False
    assert res["can_execute"] is False


def test_naive_as_of_rejected_instead_of_implicit_utc():
    with pytest.raises(ValueError):
        capture_nhl_pregame_gamecenter(
            EVENT_ID, resolved_identity=identity(), as_of_utc=NOW.replace(tzinfo=None),
        )
