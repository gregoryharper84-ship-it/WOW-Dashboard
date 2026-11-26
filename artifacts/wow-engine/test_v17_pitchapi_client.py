"""Isolated PitchAPI transport-contract tests (issue #2880 / WOW-PITCHAPI-20261126)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pytest

from v17.pitchapi_client import (
    LEAGUES_PATH,
    PITCHAPI_ORIGIN,
    USER_AGENT,
    LeagueSummary,
    PitchAPIClient,
    PitchAPIError,
    PitchAPIRawResponse,
    _select_probe_season,
    credential_class,
    find_wow_league,
    league_discovery_canary,
    soccer_pitchapi_probe_after_forward,
)


NOW = datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc)
SECRET = "pk_test_secret-value-do-not-leak"


def _raw(status: int, payload: object, *, headers: dict[str, str] | None = None, url: str | None = None) -> PitchAPIRawResponse:
    body = payload if isinstance(payload, (bytes, bytearray)) else json.dumps(payload).encode("utf-8")
    return PitchAPIRawResponse(
        status=status,
        body=bytes(body),
        headers={k.lower(): v for k, v in (headers or {}).items()},
        final_url=url or f"{PITCHAPI_ORIGIN}{LEAGUES_PATH}",
    )


def _leagues_payload(*rows: dict) -> dict:
    return {"data": {"leagues": list(rows)}}


def test_credential_class_prefix_only():
    assert credential_class(None) == "MISSING"
    assert credential_class("  ") == "MISSING"
    assert credential_class("pk_test_abc") == "TEST"
    assert credential_class("pk_live_abc") == "LIVE"
    assert credential_class("other") == "UNKNOWN"


def test_blank_key_fails_closed(monkeypatch):
    monkeypatch.delenv("PITCHAPI_API_KEY", raising=False)
    with pytest.raises(PitchAPIError) as exc:
        PitchAPIClient.from_environment()
    assert exc.value.code == "PITCHAPI_API_KEY_MISSING"
    receipt = league_discovery_canary()
    assert receipt["status"] == "FAILED"
    assert receipt["code"] == "PITCHAPI_API_KEY_MISSING"
    assert receipt["credential_class"] == "MISSING"
    assert receipt["can_execute"] is False
    assert SECRET not in json.dumps(receipt)


def test_list_leagues_uses_exact_path_headers_and_no_trailing_slash(monkeypatch):
    monkeypatch.setenv("PITCHAPI_API_KEY", f"  {SECRET}  ")
    captured = {}

    def transport(request, timeout_s):
        captured["url"] = request.full_url
        captured["timeout"] = timeout_s
        captured["headers"] = {k: request.get_header(k) for k in ("X-api-key", "User-agent", "Accept")}
        # urllib normalizes header lookup; also inspect header_items
        captured["raw_headers"] = dict(request.header_items())
        assert timeout_s == 15.0
        return _raw(
            200,
            _leagues_payload(
                {
                    "id": "l_4Kd0Wq",
                    "name": "Premier League",
                    "country_code": "ENG",
                    "seasons": ["2025/2026"],
                    "is_free": True,
                }
            ),
            headers={"X-Request-Id": "req- leagues-1"},
        )

    client = PitchAPIClient.from_environment(transport=transport)
    leagues, raw = client.list_leagues()
    assert captured["url"] == "https://api.pitchapi.dev/v1/leagues"
    assert not captured["url"].endswith("/v1/leagues/")
    header_map = {k.lower(): v for k, v in captured["raw_headers"].items()}
    assert header_map.get("x-api-key") == SECRET
    assert header_map.get("user-agent") == USER_AGENT
    assert len(leagues) == 1
    assert leagues[0].id == "l_4Kd0Wq"
    assert raw.status == 200


def test_malformed_envelope_fails_closed(monkeypatch):
    monkeypatch.setenv("PITCHAPI_API_KEY", SECRET)

    def transport(request, timeout_s):
        return _raw(200, {"leagues": []})

    client = PitchAPIClient.from_environment(transport=transport)
    with pytest.raises(PitchAPIError) as exc:
        client.list_leagues()
    assert exc.value.code == "PITCHAPI_ENVELOPE_INVALID"


def test_non_200_and_403_are_typed(monkeypatch):
    monkeypatch.setenv("PITCHAPI_API_KEY", SECRET)

    def forbidden(request, timeout_s):
        return _raw(403, {"error": {"code": "FORBIDDEN", "message": "no"}}, headers={"X-Request-Id": "r-403"})

    client = PitchAPIClient.from_environment(transport=forbidden)
    with pytest.raises(PitchAPIError) as exc:
        client.list_leagues()
    assert exc.value.code == "PITCHAPI_FORBIDDEN"
    assert exc.value.http_status == 403
    assert exc.value.request_id == "r-403"
    assert SECRET not in str(exc.value)
    assert SECRET not in json.dumps(exc.value.to_dict())


def test_429_captures_retry_after_without_paid_retry(monkeypatch):
    monkeypatch.setenv("PITCHAPI_API_KEY", SECRET)
    calls = []

    def limited(request, timeout_s):
        calls.append(1)
        return _raw(429, {"error": {"code": "RATE_LIMIT"}}, headers={"Retry-After": "12", "X-Request-Id": "r-429"})

    client = PitchAPIClient.from_environment(transport=limited)
    with pytest.raises(PitchAPIError) as exc:
        client.list_leagues()
    assert exc.value.code == "PITCHAPI_RATE_LIMITED"
    assert exc.value.retry_after_seconds == 12
    assert len(calls) == 1  # no automatic paid/retry storm


def test_redirect_refused(monkeypatch):
    monkeypatch.setenv("PITCHAPI_API_KEY", SECRET)

    def redirect(request, timeout_s):
        raise PitchAPIError("PITCHAPI_REDIRECT_REFUSED", "redirect", http_status=302)

    client = PitchAPIClient.from_environment(transport=redirect)
    with pytest.raises(PitchAPIError) as exc:
        client.list_leagues()
    assert exc.value.code == "PITCHAPI_REDIRECT_REFUSED"


def test_urlerror_timeout_reason_maps_to_pitchapi_timeout():
    import urllib.error
    from v17 import pitchapi_client as mod

    request = __import__("urllib.request").request.Request(
        f"{PITCHAPI_ORIGIN}{LEAGUES_PATH}",
        headers={"X-API-KEY": SECRET, "User-Agent": USER_AGENT},
        method="GET",
    )

    class FakeOpener:
        def open(self, _req, timeout=None):
            raise urllib.error.URLError(TimeoutError("timed out"))

    original = mod.urllib.request.build_opener
    mod.urllib.request.build_opener = lambda *_args, **_kwargs: FakeOpener()
    try:
        with pytest.raises(PitchAPIError) as exc:
            mod._default_transport(request, 15.0)
        assert exc.value.code == "PITCHAPI_TIMEOUT"
    finally:
        mod.urllib.request.build_opener = original


def test_disallowed_path_and_trailing_slash(monkeypatch):
    monkeypatch.setenv("PITCHAPI_API_KEY", SECRET)
    client = PitchAPIClient.from_environment(transport=lambda *_: _raw(200, {}))
    with pytest.raises(PitchAPIError) as exc:
        client._get("/v1/secret")
    assert exc.value.code == "PITCHAPI_ENDPOINT_NOT_ALLOWLISTED"
    with pytest.raises(PitchAPIError) as exc2:
        client._get("/v1/leagues/")
    assert exc2.value.code in {"PITCHAPI_ENDPOINT_NOT_ALLOWLISTED", "PITCHAPI_TRAILING_SLASH_FORBIDDEN"}


def test_canary_receipt_shape_and_no_secret_leakage(monkeypatch):
    monkeypatch.setenv("PITCHAPI_API_KEY", SECRET)
    leagues_body = _leagues_payload(
        {
            "id": "l_4Kd0Wq",
            "name": "Premier League",
            "country_code": "ENG",
            "seasons": ["2024/2025", "2025/2026", "2026/2027"],
            "is_free": True,
        }
    )
    matches_body = {
        "data": {
            "league": {"id": "l_4Kd0Wq", "name": "Premier League", "season": "2026/2027"},
            "matches": [
                {
                    "id": "m_B8x2K9",
                    "date": "2026-11-20",
                    "status": "finished",
                    "score_home": 2,
                    "score_away": 1,
                    "home_team": {"id": "t_9aB2xQ", "name": "Arsenal"},
                    "away_team": {"id": "t_1aB2xQ", "name": "Chelsea"},
                }
            ],
        }
    }

    def transport(request, timeout_s):
        if request.full_url.endswith("/v1/leagues"):
            return _raw(200, leagues_body, headers={"X-Request-Id": "req-1"})
        assert "/v1/leagues/l_4Kd0Wq/matches" in request.full_url
        assert "season=2026%2F2027" in request.full_url or "season=2026/2027" in request.full_url
        assert "status=upcoming" not in request.full_url
        return _raw(200, matches_body, headers={"X-Request-Id": "req-2"}, url=request.full_url)

    client = PitchAPIClient.from_environment(transport=transport)
    receipt = league_discovery_canary(client=client, wow_league="EPL", now=NOW)
    assert receipt["status"] == "OK"
    assert receipt["http_status"] == 200
    assert receipt["byte_count"] == len(json.dumps(leagues_body).encode("utf-8"))
    assert receipt["body_sha256"] == hashlib.sha256(json.dumps(leagues_body).encode("utf-8")).hexdigest()
    assert receipt["league_count"] == 1
    assert receipt["request_id"] == "req-1"
    assert receipt["credential_class"] == "TEST"
    assert receipt["observed_at"].endswith("Z")
    assert receipt["persisted"] is False
    assert receipt["can_execute"] is False
    assert receipt["match_probe"]["season"] == "2026/2027"
    assert receipt["match_probe"]["freshness"]["status"] == "FRESH"
    assert receipt["match_probe"]["fresh_relative_to_soccer_source_results_stale"] is True
    assert receipt["match_probe"]["canonical_event_crosswalk"] == "NOT_PERFORMED"
    assert receipt["rights"]["production_use_authorized"] is False
    dumped = json.dumps(receipt)
    assert SECRET not in dumped
    assert "pk_test_secret" not in dumped


def test_match_probe_failure_preserves_discovery_receipt(monkeypatch):
    monkeypatch.setenv("PITCHAPI_API_KEY", SECRET)
    leagues_body = _leagues_payload(
        {
            "id": "l_4Kd0Wq",
            "name": "Premier League",
            "country_code": "ENG",
            "seasons": ["2026/2027"],
            "is_free": True,
        }
    )

    def transport(request, timeout_s):
        if request.full_url.endswith("/v1/leagues"):
            return _raw(200, leagues_body, headers={"X-Request-Id": "req-leagues"})
        return _raw(429, {"error": {"code": "RATE_LIMIT"}}, headers={"Retry-After": "9", "X-Request-Id": "req-matches"})

    client = PitchAPIClient.from_environment(transport=transport)
    receipt = league_discovery_canary(client=client, wow_league="EPL", now=NOW)
    assert receipt["status"] == "OK"
    assert receipt["http_status"] == 200
    assert receipt["request_id"] == "req-leagues"
    assert receipt["league_count"] == 1
    assert receipt["byte_count"] == len(json.dumps(leagues_body).encode("utf-8"))
    assert receipt["match_probe"]["status"] == "FAILED"
    assert receipt["match_probe"]["code"] == "PITCHAPI_RATE_LIMITED"
    assert receipt["match_probe"]["http_status"] == 429
    assert receipt["match_probe"]["request_id"] == "req-matches"
    assert receipt["match_probe"]["retry_after_seconds"] == 9


def test_select_probe_season_prefers_current_not_first_catalogue_entry():
    assert _select_probe_season(["2024/2025", "2026/2027"], NOW) == "2026/2027"
    assert _select_probe_season(["2024/2025", "2025/2026"], NOW) == "2025/2026"


def test_find_wow_league_rejects_explicit_country_mismatch():
    rsa_only = [
        LeagueSummary("l_rsa001", "Premier League", "RSA", ("2026/2027",), True),
    ]
    assert find_wow_league(rsa_only, "EPL") is None
    eng = LeagueSummary("l_4Kd0Wq", "Premier League", "ENG", ("2026/2027",), True)
    assert find_wow_league([*rsa_only, eng], "EPL") is eng
    unknown = LeagueSummary("l_unk001", "Premier League", None, ("2026/2027",), True)
    assert find_wow_league([unknown], "EPL") is unknown


def test_soccer_hook_selects_stale_league_and_never_clears_blocker(monkeypatch):
    monkeypatch.setenv("PITCHAPI_API_KEY", SECRET)

    def transport(request, timeout_s):
        return _raw(
            200,
            _leagues_payload(
                {
                    "id": "l_0bfbkO",
                    "name": "La Liga",
                    "country_code": "ESP",
                    "seasons": ["2025/2026"],
                    "is_free": True,
                }
            ),
            headers={"X-Request-Id": "req-laliga"},
        )

    client = PitchAPIClient.from_environment(transport=transport)
    forward = {
        "leagues": {
            "LALIGA": {"blocked_by_code": {"SOCCER_SOURCE_RESULTS_STALE": 2}, "registration_id": "r1"},
            "EPL": {"blocked_by_code": {}, "registration_id": "r2"},
        }
    }
    receipt = soccer_pitchapi_probe_after_forward(forward, client=client, now=NOW)
    assert receipt["selected_stale_league"] == "LALIGA"
    assert receipt["stale_leagues"] == ["LALIGA"]
    assert receipt["blocker_progress"]["cleared"] is False
    assert receipt["blocker_progress"]["SOCCER_SOURCE_RESULTS_STALE"] == "EVIDENCE_ONLY_PROBE"
    assert receipt["pitchapi_league"]["id"] == "l_0bfbkO"
    assert SECRET not in json.dumps(receipt)


def test_maintenance_route_exposes_pitchapi_receipt():
    import inspect
    from v17 import first_six_open_data_maintenance as mod

    source = inspect.getsource(mod.install_first_six_open_data_maintenance_routes)
    assert "soccer_pitchapi_probe_after_forward" in source
    assert "pitchapi" in source
