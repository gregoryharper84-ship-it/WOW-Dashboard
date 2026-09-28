from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import requests

from v17 import live_tennis_api_discovery as tennis


class _Response:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload


def _target(league: str = "ATP", *, family: str = "TENNIS", regime: str = "REGULAR_SEASON"):
    return SimpleNamespace(family=family, league=league, regime=regime)


def _fixture(
    match_id: int,
    *,
    start_time: str | None = "2026-09-28T18:00:00Z",
    event_date: str | None = "2026-09-28",
):
    return {
        "id": match_id,
        "event_date": event_date,
        "start_time": start_time,
        "player1_id": 101,
        "player2_id": 202,
        "tour": "atp",
        "tournament": "Example Open",
        "round": "Round of 32",
        "round_code": "R32",
        "surface": "hard",
        "player1_name": "Player One",
        "player2_name": "Player Two",
        "status": "scheduled",
    }


def test_target_mapping_is_atp_wta_only():
    assert tennis.tour_for_target(_target("ATP")) == "atp"
    assert tennis.tour_for_target(_target("WTA")) == "wta"
    assert tennis.tour_for_target(_target("ITF")) is None
    assert tennis.tour_for_target(_target("ATP", family="SOCCER")) is None
    assert tennis.CAN_EXECUTE is False


def test_missing_credential_fails_before_transport(monkeypatch):
    for alias in ("WOW_LIVE_TENNIS_API_KEY", "LIVE_TENNIS_API_KEY"):
        monkeypatch.delenv(alias, raising=False)
    called = False

    def requester(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("transport must not run without credential")

    with pytest.raises(Exception) as exc_info:
        tennis.fetch_target(
            _target(),
            started=datetime(2026, 9, 27, 12, tzinfo=timezone.utc),
            horizon_hours=48,
            requester=requester,
        )

    exc = exc_info.value
    assert getattr(exc, "code", None) == tennis.CREDENTIAL_UNCONFIGURED
    assert called is False
    assert exc.acquisition.primary_path_state == "NOT_ATTEMPTED"


def test_success_preserves_alias_only_and_date_only_states(monkeypatch):
    monkeypatch.setenv("WOW_LIVE_TENNIS_API_KEY", "test-key")
    seen = []

    def requester(url, *, headers, params, timeout):
        seen.append((url, headers, params, timeout))
        return _Response(
            200,
            {
                "data": [
                    _fixture(11),
                    _fixture(12, start_time=None, event_date="2026-09-28"),
                ],
                "meta": {"count": 2},
            },
        )

    result = tennis.fetch_target(
        _target("ATP"),
        started=datetime(2026, 9, 27, 12, tzinfo=timezone.utc),
        horizon_hours=48,
        requester=requester,
    )

    assert result.primary_path_state == "SUCCEEDED_WITH_ROWS"
    assert len(result.rows) == 2
    exact, date_only = result.rows
    assert exact["provider_event_id"] == "11"
    assert exact["canonical_identity_status"] == "ALIAS_ONLY_UNRESOLVED"
    assert exact["schedule_precision"] == "EXACT_START_TIME"
    assert date_only["commence_time"] is None
    assert date_only["event_date"] == "2026-09-28"
    assert date_only["schedule_precision"] == "DATE_ONLY_START_TIME_UNASSIGNED"
    for row in result.rows:
        assert row["prediction_authority"] is False
        assert row["exact_line_authority"] is False
        assert row["canonical_identity_authority"] is False
        assert row["settlement_authority"] is False
        assert row["can_execute"] is False
        assert "id" not in row
        assert "event_id" not in row
        assert "official_event_id" not in row
    assert seen[0][0].endswith("/fixtures")
    assert seen[0][1] == {"X-API-Key": "test-key"}
    assert seen[0][2] == {"tour": "atp", "draw": "singles", "limit": 50, "offset": 0}


def test_exact_and_date_only_horizon_filtering(monkeypatch):
    monkeypatch.setenv("WOW_LIVE_TENNIS_API_KEY", "test-key")

    def requester(*args, **kwargs):
        return _Response(
            200,
            {
                "data": [
                    _fixture(1, start_time="2026-09-27T11:59:59Z", event_date="2026-09-27"),
                    _fixture(2, start_time="2026-09-29T12:00:01Z", event_date="2026-09-29"),
                    _fixture(3, start_time=None, event_date="2026-09-30"),
                    _fixture(4, start_time="2026-09-28T10:00:00Z", event_date="2026-09-28"),
                ],
                "meta": {"count": 4},
            },
        )

    result = tennis.fetch_target(
        _target(),
        started=datetime(2026, 9, 27, 12, tzinfo=timezone.utc),
        horizon_hours=48,
        requester=requester,
    )
    assert [row["provider_event_id"] for row in result.rows] == ["4"]


def test_full_fourth_page_fails_typed_instead_of_claiming_complete_coverage(monkeypatch):
    monkeypatch.setenv("WOW_LIVE_TENNIS_API_KEY", "test-key")
    calls = []

    def requester(url, *, headers, params, timeout):
        calls.append(params["offset"])
        base = params["offset"]
        return _Response(
            200,
            {
                "data": [_fixture(base + idx + 1) for idx in range(tennis.PAGE_LIMIT)],
                "meta": {"count": 500},
            },
        )

    with pytest.raises(Exception) as exc_info:
        tennis.fetch_target(
            _target(),
            started=datetime(2026, 9, 27, 12, tzinfo=timezone.utc),
            horizon_hours=48,
            requester=requester,
        )
    assert getattr(exc_info.value, "code", None) == tennis.PAGINATION_LIMIT_REACHED
    assert calls == [0, 50, 100, 150]


def test_http_and_transport_failures_remain_typed(monkeypatch):
    monkeypatch.setenv("WOW_LIVE_TENNIS_API_KEY", "super-secret-test-key")

    with pytest.raises(Exception) as http_exc:
        tennis.fetch_target(
            _target(),
            started=datetime(2026, 9, 27, 12, tzinfo=timezone.utc),
            horizon_hours=48,
            requester=lambda *args, **kwargs: _Response(429, {"error": "rate_limited"}),
        )
    assert getattr(http_exc.value, "code", None) == "LIVE_TENNIS_API_HTTP_429"
    assert "super-secret-test-key" not in str(http_exc.value)

    def timeout(*args, **kwargs):
        raise requests.Timeout("slow")

    with pytest.raises(Exception) as transport_exc:
        tennis.fetch_target(
            _target(),
            started=datetime(2026, 9, 27, 12, tzinfo=timezone.utc),
            horizon_hours=48,
            requester=timeout,
        )
    assert getattr(transport_exc.value, "code", None) == tennis.TRANSPORT_FAILED


def test_invalid_json_and_schema_are_typed(monkeypatch):
    monkeypatch.setenv("WOW_LIVE_TENNIS_API_KEY", "test-key")

    with pytest.raises(Exception) as invalid_json:
        tennis.fetch_target(
            _target(),
            started=datetime(2026, 9, 27, 12, tzinfo=timezone.utc),
            horizon_hours=48,
            requester=lambda *args, **kwargs: _Response(200, ValueError("bad json")),
        )
    assert getattr(invalid_json.value, "code", None) == tennis.INVALID_JSON

    with pytest.raises(Exception) as invalid_schema:
        tennis.fetch_target(
            _target(),
            started=datetime(2026, 9, 27, 12, tzinfo=timezone.utc),
            horizon_hours=48,
            requester=lambda *args, **kwargs: _Response(200, {"data": {}}),
        )
    assert getattr(invalid_schema.value, "code", None) == tennis.SCHEMA_INVALID
