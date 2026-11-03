"""Offline NHL first-party pregame source-boundary tests (no provider calls)."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json

import pytest

from v17.nhl_official_pregame_source_receipt import (
    NHLPregameSourceError,
    capture_nhl_official_pregame,
)

EVENT_ID = "2026020245"
KICKOFF = "2026-11-04T01:00:00Z"
BEFORE = datetime(2026, 11, 3, 23, 0, tzinfo=timezone.utc)


def _payload(**changes):
    data = {
        "id": int(EVENT_ID),
        "startTimeUTC": KICKOFF,
        "homeTeam": {"abbrev": "NSH"},
        "awayTeam": {"abbrev": "CAR"},
        "gameType": 2,
        "gameState": "FUT",
        "matchup": {"goalieComparison": {
            "leaders": [{"playerId": 999, "savePctg": 0.92}]
        }},
    }
    data.update(changes)
    return data


class _Response:
    status_code = 200

    def __init__(self, data=None, *, raw=None, status=200, url=None):
        self.status_code = status
        self.url = url or "https://api-web.nhle.com/v1/gamecenter/2026020245/landing"
        self.content = (
            json.dumps(_payload() if data is None else data).encode("utf-8")
            if raw is None else raw
        )
        self.closed = False

    def iter_content(self, *, chunk_size):
        for i in range(0, len(self.content), chunk_size):
            yield self.content[i:i+chunk_size]

    def close(self):
        self.closed = True


class _Session:
    def __init__(self, response=None):
        self.response = response if response is not None else _Response()
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def _capture(session=None, **overrides):
    values = {
        "official_event_id": EVENT_ID,
        "event_start_time_utc": KICKOFF,
        "home_team": "Nashville Predators",
        "away_team": "Carolina Hurricanes",
        "session": session if session is not None else _Session(),
        "clock": lambda: BEFORE,
    }
    values.update(overrides)
    return capture_nhl_official_pregame(**values)


def test_official_fut_receipt_records_raw_bytes_and_exact_identity():
    session = _Session()
    receipt = _capture(session)
    assert receipt["status"] == "NHL_OFFICIAL_SOURCE_CAPTURED_NOT_MODEL_READY"
    assert receipt["official_event_id"] == EVENT_ID
    assert receipt["home_team"] == "NSH"
    assert receipt["away_team"] == "CAR"
    assert receipt["source_payload_sha256"] == sha256(session.response.content).hexdigest()
    assert receipt["source_uri"] == (
        "https://api-web.nhle.com/v1/gamecenter/2026020245/landing"
    )
    assert session.calls[0][1]["timeout"] == (5, 10)
    assert session.calls[0][1]["stream"] is True
    assert session.calls[0][1]["allow_redirects"] is False
    assert session.response.closed is True
    assert receipt["can_execute"] is False
    assert receipt["probability_publishable"] is False
    assert receipt["rank_eligible"] is False
    assert receipt["model_invoked"] is False
    assert receipt["raw_payload_archived"] is False
    assert receipt["source_signature_independently_verified"] is False


def test_historical_goalie_comparison_is_not_a_confirmed_start():
    receipt = _capture()
    assert receipt["goalies_confirmed"] is False
    assert receipt["starting_goalie_status"] == "UNCONFIRMED"
    assert receipt["injury_report_status"] == "NOT_CAPTURED"
    assert "goalie_status" in receipt["missing_fields"]
    assert "calibration_artifact" in receipt["missing_fields"]


@pytest.mark.parametrize(
    "change,code",
    [
        ({"id": 2026020246}, "NHL_SOURCE_EVENT_IDENTITY_MISMATCH"),
        ({"homeTeam": {"abbrev": "CAR"}}, "NHL_SOURCE_EVENT_IDENTITY_MISMATCH"),
        ({"awayTeam": {"abbrev": "NSH"}}, "NHL_SOURCE_EVENT_IDENTITY_MISMATCH"),
        ({"startTimeUTC": "2026-11-04T01:01Z"}, "NHL_SOURCE_EVENT_IDENTITY_MISMATCH"),
        ({"gameState": "LIVE"}, "NHL_SOURCE_EVENT_NOT_FUTURE"),
        ({"gameState": "FINAL"}, "NHL_SOURCE_EVENT_NOT_FUTURE"),
        ({"gameState": "UNKNOWN"}, "NHL_SOURCE_EVENT_NOT_FUTURE"),
        ({"gameType": 1}, "NHL_SOURCE_GAME_TYPE_INVALID"),
        ({"homeTeam": None}, "NHL_SOURCE_SCHEMA_INVALID"),
    ],
)
def test_identity_temporal_and_schema_mismatches_hold(change, code):
    with pytest.raises(NHLPregameSourceError) as raised:
        _capture(_Session(_Response(_payload(**change))))
    assert raised.value.code == code


@pytest.mark.parametrize("status,code", [(429, "NHL_SOURCE_RATE_LIMITED"), (403, "NHL_SOURCE_HTTP_FAILED")])
def test_http_failure_not_silently_replaced_with_market_or_history(status, code):
    with pytest.raises(NHLPregameSourceError) as raised:
        _capture(_Session(_Response(status=status)))
    assert raised.value.code == code


@pytest.mark.parametrize(
    "raw,code",
    [
        (b"", "NHL_SOURCE_RAW_BYTES_MISSING_OR_OVERSIZE"),
        (b"not json", "NHL_SOURCE_JSON_INVALID"),
        (b"{}", "NHL_SOURCE_SCHEMA_INVALID"),
        (b"[]" , "NHL_SOURCE_SCHEMA_INVALID"),
    ],
)
def test_bad_raw_source_fails_closed(raw, code):
    with pytest.raises(NHLPregameSourceError) as raised:
        _capture(_Session(_Response(raw=raw)))
    assert raised.value.code == code


def test_kickoff_rejects_before_provider_call():
    session = _Session()
    with pytest.raises(NHLPregameSourceError) as raised:
        _capture(session, clock=lambda: datetime(2026, 11, 4, 1, 0, tzinfo=timezone.utc))
    assert raised.value.code == "NHL_SOURCE_NOT_PREGAME"
    assert session.calls == []


def test_finished_response_after_fetch_not_accepted_as_pregame():
    clocks = iter([
        BEFORE,
        datetime(2026, 11, 4, 1, 0, tzinfo=timezone.utc),
    ])
    with pytest.raises(NHLPregameSourceError) as raised:
        _capture(clock=lambda: next(clocks))
    assert raised.value.code == "NHL_SOURCE_CAPTURE_AFTER_START"


def test_identical_raw_payload_has_same_snapshot_id_even_when_clock_changes():
    first = _capture()
    second = _capture(clock=lambda: datetime(2026, 11, 3, 23, 1, tzinfo=timezone.utc))
    assert first["source_snapshot_id"] == second["source_snapshot_id"]
    assert first["as_of"] != second["as_of"]


def test_rejects_bad_inputs_without_network():
    session = _Session()
    with pytest.raises(NHLPregameSourceError) as raised:
        _capture(session, official_event_id="../whatever")
    assert raised.value.code == "NHL_SOURCE_EVENT_ID_INVALID"
    assert session.calls == []
    with pytest.raises(NHLPregameSourceError) as raised:
        _capture(session, home_team="unknown")
    assert raised.value.code == "NHL_SOURCE_TEAMS_INVALID"
    assert session.calls == []


def test_network_failure_yields_typed_hold():
    class BrokenSession:
        def get(self, *args, **kwargs):
            raise TimeoutError("source offline")
    with pytest.raises(NHLPregameSourceError) as raised:
        _capture(session=BrokenSession())
    assert raised.value.code == "NHL_SOURCE_FETCH_FAILED"


@pytest.mark.parametrize("raw", [b"x" * 2_000_001, b""])
def test_oversize_or_empty_raw_bytes_are_not_evidence(raw):
    with pytest.raises(NHLPregameSourceError) as raised:
        _capture(_Session(_Response(raw=raw)))
    assert raised.value.code == "NHL_SOURCE_RAW_BYTES_MISSING_OR_OVERSIZE"


def test_non_utf8_source_json_is_not_accepted():
    with pytest.raises(NHLPregameSourceError) as raised:
        _capture(_Session(_Response(raw=b"\xff")))
    assert raised.value.code == "NHL_SOURCE_JSON_INVALID"


@pytest.mark.parametrize(
    "start",
    ["not-an-instant", "2026-11-04T01:00:00", "0001-01-01T00:00:00+01:00"],
)
def test_invalid_and_overflowing_event_time_fails_closed(start):
    with pytest.raises(NHLPregameSourceError) as raised:
        _capture(event_start_time_utc=start)
    assert raised.value.code == "NHL_SOURCE_TIME_INVALID"


def test_invalid_clock_timezone_fails_closed():
    with pytest.raises(NHLPregameSourceError) as raised:
        _capture(clock=lambda: datetime(2026, 11, 3, 23, 0))
    assert raised.value.code == "NHL_SOURCE_TIME_INVALID"


def test_event_id_and_team_identity_cannot_be_collapsed():
    with pytest.raises(NHLPregameSourceError) as raised:
        _capture(home_team="Carolina Hurricanes", away_team="CAR")
    assert raised.value.code == "NHL_SOURCE_TEAMS_INVALID"


@pytest.mark.parametrize(
    "response",
    [
        _Response(status=302),
        _Response(url="https://example.org/untrusted/source"),
    ],
)
def test_redirect_or_non_official_final_host_is_never_attested(response):
    with pytest.raises(NHLPregameSourceError) as raised:
        _capture(_Session(response))
    assert raised.value.code == "NHL_SOURCE_REDIRECT_FORBIDDEN"
    assert response.closed is True


def test_oversize_stream_aborts_and_closes_without_parsing():
    response = _Response(raw=b"0" * 2_100_000)
    with pytest.raises(NHLPregameSourceError) as raised:
        _capture(_Session(response))
    assert raised.value.code == "NHL_SOURCE_RAW_BYTES_MISSING_OR_OVERSIZE"
    assert response.closed is True


def test_stream_exception_is_typed_and_closes_response():
    class BrokenResponse(_Response):
        def iter_content(self, *, chunk_size):
            yield b'{"id":'
            raise ConnectionError("broken stream")

    response = BrokenResponse()
    with pytest.raises(NHLPregameSourceError) as raised:
        _capture(_Session(response))
    assert raised.value.code == "NHL_SOURCE_FETCH_FAILED"
    assert response.closed is True


def test_total_stream_deadline_enforced(monkeypatch):
    from v17 import nhl_official_pregame_source_receipt as source
    sequence = iter([0.0, 21.0])
    monkeypatch.setattr(source.time, "monotonic", lambda: next(sequence))
    response = _Response()
    with pytest.raises(NHLPregameSourceError) as raised:
        _capture(_Session(response))
    assert raised.value.code == "NHL_SOURCE_TOTAL_DEADLINE_EXCEEDED"
    assert response.closed is True
