"""Provider exact-event identity and provenance tests, no betting/scoring. #2131"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from nfl_inactives_source_shadow import bind_nflmeta_game, read_nflmeta_inactives_shadow

NOW = datetime(2026, 10, 26, 22, 0, tzinfo=timezone.utc)
START = NOW + timedelta(minutes=55)


def _games(**overrides):
    row = {
        "id": 23456,
        "seasonYear": 2026,
        "week": 7,
        "isPlayoff": False,
        "homeAbbr": "PHI",
        "awayAbbr": "DAL",
        "kickoffAt": START.isoformat(),
    }
    row.update(overrides)
    return {"meta": {"data_status": {"status": "complete"}}, "data": [row]}


def _bind(response):
    return bind_nflmeta_game(
        response, season=2026, week=7, home_team="PHI", away_team="DAL", event_start=START,
    )


def test_only_exact_game_identity_is_accepted():
    assert _bind(_games()) == 23456


@pytest.mark.parametrize("field,value", [
    ("seasonYear", 2025),
    ("week", 6),
    ("isPlayoff", True),
    ("homeAbbr", "DAL"),
    ("awayAbbr", "PHI"),
    ("kickoffAt", (START + timedelta(days=1)).isoformat()),
    ("kickoffAt", "2026-10-26"),
    ("id", True),
    ("id", 0),
])
def test_wrong_matchup_identity_or_time_blocks_game_join(field, value):
    assert _bind(_games(**{field: value})) is None


def test_ambiguous_vendor_game_ids_block():
    payload = _games()
    payload["data"] = [payload["data"][0], dict(payload["data"][0], id=33457)]
    assert _bind(payload) is None


@pytest.mark.parametrize("status", ["partial", "stale", "running", "missing", None])
def test_noncomplete_list_status_does_not_authorize(status):
    payload = _games()
    payload["meta"]["data_status"]["status"] = status
    assert _bind(payload) is None


def _read(**opts):
    inputs = {
        "season": 2026, "week": 7, "home_team": "PHI", "away_team": "DAL",
        "player_team": "PHI", "player_name": "Eligible Candidate",
        "event_start": START, "now": NOW,
    }
    inputs.update(opts)
    return read_nflmeta_inactives_shadow(**inputs)


def test_off_by_default_has_no_provider_requests():
    def forbidden(*args, **kw):
        raise AssertionError("disabled provider was contacted")
    out = _read(enabled=False, http_get=forbidden, api_key="unused")
    assert out["reason"] == "SHADOW_NOT_ENABLED"
    assert out["probability_publishable"] is False
    assert out["can_execute"] is False


def test_missing_key_fails_closed():
    out = _read(enabled=True, api_key="")
    assert out["reason"] == "PROVIDER_CREDENTIAL_UNCONFIGURED"


def test_source_http_status_or_failed_game_identity_never_score():
    calls = []
    def mock_get(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(status_code=200, content=b"{}", json=lambda: _games(homeAbbr="NYG"))
    out = _read(enabled=True, api_key="test-no-real-secret", http_get=mock_get)
    assert out["reason"] == "PROVIDER_GAME_IDENTITY_UNVERIFIED"
    assert len(calls) == 1
    assert out["probability_publishable"] is False


def test_authorized_source_candidate_is_still_shadow_only(monkeypatch):
    calls = []
    def mock_get(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/games"):
            return SimpleNamespace(status_code=200, content=b"{}", json=lambda: _games())
        return SimpleNamespace(
            status_code=200,
            content=b"{}",
            json=lambda: {"meta": {"game_id": 23456}, "data": {"game_id": 23456, "teams": []}},
        )
    out = _read(enabled=True, api_key="test-key-do-not-log", http_get=mock_get)
    assert out["reason"] == "BOTH_TEAMS_REPORTS_REQUIRED"
    assert out["provider_game_id"] == 23456
    assert out["can_execute"] is False
    assert out["probability_publishable"] is False
    assert len(calls) == 2
    assert calls[0][0] == "https://nflmeta.org/api/v1/games"
    assert calls[1][0] == "https://nflmeta.org/api/v1/games/23456/inactives"
    assert calls[0][1]["follow_redirects"] is False
    assert "test-key-do-not-log" not in str(out)


def test_provider_http_401_or_quota_does_not_turn_into_model_unavailable():
    for status in (401, 429, 500):
        out = _read(
            enabled=True, api_key="test",
            http_get=lambda *_a, **_k: SimpleNamespace(status_code=status, content=b"{}"),
        )
        assert out["reason"] == "PROVIDER_GAMES_UNAVAILABLE"
        assert out["code"] == "PLAYER_AVAILABILITY_UNVERIFIED"


def test_server_only_team_path_blocks_injection():
    out = _read(
        enabled=True, api_key="test",
        home_team="PHI/../../", http_get=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("called")),
    )
    assert out["reason"] == "GAME_IDENTITY_INVALID"
