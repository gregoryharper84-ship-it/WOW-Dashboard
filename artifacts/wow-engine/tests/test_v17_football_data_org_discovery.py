from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import requests

from v17 import football_data_org_discovery as fd


class _Response:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload


def _target(league: str = "EPL", *, family: str = "SOCCER", regime: str = "REGULAR_SEASON"):
    return SimpleNamespace(family=family, league=league, regime=regime)


def test_default_mapping_is_limited_to_verified_free_tier_competitions():
    assert fd.DEFAULT_COMPETITION_CODES == {
        "EPL": "PL",
        "FRA1": "FL1",
        "GER1": "BL1",
        "ESP1": "PD",
        "ITA1": "SA",
        "UEFACHAMP": "CL",
        "FIFA": "WC",
        "UEFAEURO": "EC",
    }
    assert "MLS" not in fd.DEFAULT_COMPETITION_CODES
    assert "UEFAEUROPA" not in fd.DEFAULT_COMPETITION_CODES
    assert fd.CAN_EXECUTE is False


def test_missing_credential_fails_before_transport(monkeypatch):
    for alias in (
        "WOW_FOOTBALL_DATA_API_TOKEN",
        "FOOTBALL_DATA_API_TOKEN",
        "FOOTBALL_DATA_TOKEN",
    ):
        monkeypatch.delenv(alias, raising=False)

    called = False

    def requester(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("transport must not run without credential")

    with pytest.raises(Exception) as exc_info:
        fd.fetch_target(
            _target(),
            started=datetime(2026, 9, 27, 12, tzinfo=timezone.utc),
            horizon_hours=36,
            requester=requester,
        )

    exc = exc_info.value
    assert getattr(exc, "code", None) == fd.CREDENTIAL_UNCONFIGURED
    assert called is False
    assert exc.acquisition.primary_path_id == fd.PATH_ID
    assert exc.acquisition.primary_path_state == "NOT_ATTEMPTED"


def test_unsupported_target_fails_before_transport(monkeypatch):
    monkeypatch.setenv("WOW_FOOTBALL_DATA_API_TOKEN", "test-token")

    def requester(*args, **kwargs):
        raise AssertionError("unsupported target must not call provider")

    with pytest.raises(Exception) as exc_info:
        fd.fetch_target(
            _target("MLS"),
            started=datetime(2026, 9, 27, 12, tzinfo=timezone.utc),
            horizon_hours=36,
            requester=requester,
        )
    assert getattr(exc_info.value, "code", None) == fd.TARGET_UNSUPPORTED


def test_success_returns_alias_only_research_rows(monkeypatch):
    monkeypatch.setenv("WOW_FOOTBALL_DATA_API_TOKEN", "test-token")
    seen = {}

    def requester(url, *, headers, params, timeout):
        seen.update(url=url, headers=headers, params=params, timeout=timeout)
        return _Response(
            200,
            {
                "matches": [
                    {
                        "id": 12345,
                        "utcDate": "2026-09-28T00:30:00Z",
                        "status": "SCHEDULED",
                        "lastUpdated": "2026-09-27T20:00:00Z",
                        "homeTeam": {"id": 1, "name": "Home FC", "crest": "https://example.invalid/home.png"},
                        "awayTeam": {"id": 2, "name": "Away FC", "crest": "https://example.invalid/away.png"},
                        "competition": {"code": "PL", "emblem": "https://example.invalid/pl.png"},
                    }
                ]
            },
        )

    result = fd.fetch_target(
        _target("EPL"),
        started=datetime(2026, 9, 27, 12, tzinfo=timezone.utc),
        horizon_hours=36,
        requester=requester,
    )

    assert result.primary_path_id == fd.PATH_ID
    assert result.primary_path_state == "SUCCEEDED_WITH_ROWS"
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row["provider_event_id"] == "12345"
    assert row["canonical_identity_status"] == "ALIAS_ONLY_UNRESOLVED"
    assert row["home_team"] == "Home FC"
    assert row["away_team"] == "Away FC"
    assert row["league"] == "EPL"
    assert row["prediction_authority"] is False
    assert row["exact_line_authority"] is False
    assert row["canonical_identity_authority"] is False
    assert row["can_execute"] is False
    assert "id" not in row
    assert "official_event_id" not in row
    assert "crest" not in row
    assert "emblem" not in row
    assert seen["url"].endswith("/competitions/PL/matches")
    assert seen["headers"] == {"X-Auth-Token": "test-token"}
    assert seen["params"] == {"dateFrom": "2026-09-27", "dateTo": "2026-09-29"}
    assert seen["timeout"] == fd.HTTP_TIMEOUT_SECONDS


def test_exact_horizon_filters_date_boundary_rows(monkeypatch):
    monkeypatch.setenv("WOW_FOOTBALL_DATA_API_TOKEN", "test-token")

    def requester(*args, **kwargs):
        return _Response(
            200,
            {
                "matches": [
                    {
                        "id": 1,
                        "utcDate": "2026-09-27T11:59:59Z",
                        "status": "SCHEDULED",
                        "homeTeam": {"name": "Before"},
                        "awayTeam": {"name": "Window"},
                    },
                    {
                        "id": 2,
                        "utcDate": "2026-09-29T00:00:01Z",
                        "status": "SCHEDULED",
                        "homeTeam": {"name": "After"},
                        "awayTeam": {"name": "Window"},
                    },
                ]
            },
        )

    result = fd.fetch_target(
        _target(),
        started=datetime(2026, 9, 27, 12, tzinfo=timezone.utc),
        horizon_hours=36,
        requester=requester,
    )
    assert result.rows == ()
    assert result.primary_path_state == "SUCCEEDED_EMPTY"


def test_http_429_preserves_provider_failure_without_secret(monkeypatch):
    monkeypatch.setenv("WOW_FOOTBALL_DATA_API_TOKEN", "super-secret-test-token")

    def requester(*args, **kwargs):
        return _Response(429, {"message": "rate limited"})

    with pytest.raises(Exception) as exc_info:
        fd.fetch_target(
            _target(),
            started=datetime(2026, 9, 27, 12, tzinfo=timezone.utc),
            horizon_hours=36,
            requester=requester,
        )
    exc = exc_info.value
    assert getattr(exc, "code", None) == "FOOTBALL_DATA_HTTP_429"
    assert exc.acquisition.primary_upstream_status == 429
    assert "super-secret-test-token" not in str(exc)


def test_transport_timeout_is_typed(monkeypatch):
    monkeypatch.setenv("WOW_FOOTBALL_DATA_API_TOKEN", "test-token")

    def requester(*args, **kwargs):
        raise requests.Timeout("slow")

    with pytest.raises(Exception) as exc_info:
        fd.fetch_target(
            _target(),
            started=datetime(2026, 9, 27, 12, tzinfo=timezone.utc),
            horizon_hours=36,
            requester=requester,
        )
    assert getattr(exc_info.value, "code", None) == fd.TRANSPORT_FAILED


def test_invalid_json_and_schema_are_typed(monkeypatch):
    monkeypatch.setenv("WOW_FOOTBALL_DATA_API_TOKEN", "test-token")

    with pytest.raises(Exception) as invalid_json:
        fd.fetch_target(
            _target(),
            started=datetime(2026, 9, 27, 12, tzinfo=timezone.utc),
            horizon_hours=36,
            requester=lambda *args, **kwargs: _Response(200, ValueError("bad json")),
        )
    assert getattr(invalid_json.value, "code", None) == fd.INVALID_JSON

    with pytest.raises(Exception) as invalid_schema:
        fd.fetch_target(
            _target(),
            started=datetime(2026, 9, 27, 12, tzinfo=timezone.utc),
            horizon_hours=36,
            requester=lambda *args, **kwargs: _Response(200, {"matches": {}}),
        )
    assert getattr(invalid_schema.value, "code", None) == fd.SCHEMA_INVALID
