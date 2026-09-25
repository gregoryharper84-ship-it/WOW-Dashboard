from __future__ import annotations

import json

from v17 import thesportsdb_free_discovery as free


class _Response:
    def __init__(self, payload: dict, status: int = 200):
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._body


def _event(**overrides):
    row = {
        "idEvent": "12345",
        "strHomeTeam": "Alpha FC",
        "strAwayTeam": "Beta FC",
        "strTimestamp": "2026-09-25T19:30:00+00:00",
        "dateEvent": "2026-09-25",
        "strTime": "19:30:00",
        "strStatus": "Not Started",
        "idLeague": "4328",
        "strLeague": "English Premier League",
        "strSport": "Soccer",
    }
    row.update(overrides)
    return row


def test_normalize_keeps_provider_id_alias_only_and_never_invents_official_identity():
    row = free.normalize_event(_event(), requested_sport="Soccer")
    assert row is not None
    assert row["provider_event_id"] == "12345"
    assert row["provider_event_id_type"] == "THESPORTSDB_IDEVENT_ALIAS"
    assert row["canonical_identity_status"] == "ALIAS_ONLY_UNRESOLVED"
    assert "id" not in row
    assert "event_id" not in row
    assert "official_event_id" not in row
    assert row["research_only"] is True
    assert row["prediction_authority"] is False
    assert row["exact_line_authority"] is False
    assert row["can_execute"] is False


def test_clock_only_provider_time_does_not_invent_timezone_or_instant():
    row = free.normalize_event(
        _event(
            strTimestamp=None,
            strTime=None,
            strEventTime="19:30:00",
            dateEvent="2026-09-25",
        ),
        requested_sport="Soccer",
    )
    assert row is not None
    assert row["commence_time"] is None
    assert row["provider_date"] == "2026-09-25"
    assert row["provider_time"] == "19:30:00"


def test_naive_provider_timestamp_is_retained_only_as_provider_value():
    row = free.normalize_event(
        _event(strTimestamp="2026-09-25T16:00:00"),
        requested_sport="Soccer",
    )
    assert row is not None
    assert row["commence_time"] is None
    assert row["provider_timestamp"] == "2026-09-25T16:00:00"


def test_zulu_provider_timestamp_is_an_absolute_instant():
    row = free.normalize_event(
        _event(strTimestamp="2026-09-25T16:00:00Z"),
        requested_sport="Soccer",
    )
    assert row is not None
    assert row["commence_time"] == "2026-09-25T16:00:00Z"


def test_fetch_day_uses_documented_official_api_and_never_claims_complete_free_coverage():
    observed = {}

    def opener(request, timeout):
        observed["url"] = request.full_url
        observed["timeout"] = timeout
        observed["accept"] = request.headers.get("Accept")
        return _Response({"events": [_event()]})

    result = free.fetch_day(
        slate_date="2026-09-25",
        sport="Soccer",
        league_id="4328",
        opener=opener,
    )
    assert result.ok is True
    assert result.http_status == 200
    assert len(result.rows) == 1
    assert result.coverage_complete is False
    assert result.documented_free_response_limit == 3
    assert observed["url"].startswith(f"{free.BASE_URL}/eventsday.php?")
    assert "d=2026-09-25" in observed["url"]
    assert "s=Soccer" in observed["url"]
    assert "l=4328" in observed["url"]
    assert observed["accept"] == "application/json"
    assert observed["timeout"] == free.TIMEOUT_SECONDS
    assert result.can_execute is False


def test_payload_shape_failure_and_transport_failure_fail_closed():
    assert free.rows_from_payload({"events": "wrong"}, requested_sport="Soccer") == ()

    def broken(*_args, **_kwargs):
        raise TimeoutError("fixture")

    result = free.fetch_day(
        slate_date="2026-09-25",
        sport="Soccer",
        opener=broken,
    )
    assert result.ok is False
    assert result.code == "THESPORTSDB_TimeoutError"
    assert result.rows == ()
    assert result.coverage_complete is False
    assert result.can_execute is False
