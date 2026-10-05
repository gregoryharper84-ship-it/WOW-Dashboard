import json
from pathlib import Path

import pytest

import wnba_prop_auto_hydration as wnba
from v17 import wnba_official_schedule_web_fallback as fallback


NEXT_DATA = {
    "props": {
        "siteHeaderOptions": {
            "teams": [
                {"tid": "1611661319", "ta": "LVA", "tc": "Las Vegas", "tn": "Aces"},
                {"tid": "1611661325", "ta": "IND", "tc": "Indiana", "tn": "Fever"},
            ]
        }
    }
}

HTML = f"""
<html><body>
<script id="__NEXT_DATA__" type="application/json">{json.dumps(NEXT_DATA)}</script>
<a class="GameTile" href="https://www.wnba.com/game/lva-vs-ind-1042600122">
  <time datetime="2026-09-29T22:30:00Z">6:30 PM</time>
  <div class="team--away">
    <img src="https://cdn.wnba.com/logos/wnba/1611661319/primary/L/logo.svg">
    <p aria-label="Las Vegas Aces">Aces</p>
  </div>
  <div class="team--home">
    <img src="https://cdn.wnba.com/logos/wnba/1611661325/primary/L/logo.svg">
    <p aria-label="Indiana Fever">Fever</p>
  </div>
</a>
</body></html>
"""


CURRENT_API_PAYLOAD = {
    "leagueSchedule": {
        "gameDates": [
            {
                "gameDate": "10/07/2026 00:00:00",
                "games": [
                    {
                        "gameId": "1042600202",
                        "gameDateTimeUTC": "2026-10-07T23:30:00Z",
                        "gameDateUTC": "2026-10-07T04:00:00Z",
                        "gameStatus": 1,
                        "homeTeam": {
                            "teamId": 1611661330,
                            "teamCity": "Atlanta",
                            "teamName": "Dream",
                            "teamTricode": "ATL",
                        },
                        "awayTeam": {
                            "teamId": 1611661313,
                            "teamCity": "New York",
                            "teamName": "Liberty",
                            "teamTricode": "NYL",
                        },
                    },
                    {
                        "gameId": "1042600212",
                        "gameDateTimeUTC": "2026-10-08T01:30:00Z",
                        "gameDateUTC": "2026-10-08T04:00:00Z",
                        "gameStatus": 1,
                        "homeTeam": {
                            "teamId": 1611661331,
                            "teamCity": "Golden State",
                            "teamName": "Valkyries",
                            "teamTricode": "GSV",
                        },
                        "awayTeam": {
                            "teamId": 1611661319,
                            "teamCity": "Las Vegas",
                            "teamName": "Aces",
                            "teamTricode": "LVA",
                        },
                    },
                ],
            }
        ]
    }
}


class FakeResponse:
    def __init__(self, *, status_code=200, payload=None, content=b""):
        self.status_code = status_code
        self._payload = payload
        self.content = content

    def json(self):
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload


def test_parses_server_rendered_official_game_with_exact_team_registry_and_void_img_tags():
    payload = fallback.parse_official_schedule_page(HTML)
    games = payload["leagueSchedule"]["gameDates"][0]["games"]
    assert len(games) == 1
    game = games[0]
    assert game["gameId"] == "1042600122"
    assert game["gameDateTimeUTC"] == "2026-09-29T22:30:00Z"
    assert game["gameStatus"] == 1
    assert game["awayTeam"] == {
        "teamId": "1611661319",
        "teamTricode": "LVA",
        "teamCity": "Las Vegas",
        "teamName": "Aces",
    }
    assert game["homeTeam"] == {
        "teamId": "1611661325",
        "teamTricode": "IND",
        "teamCity": "Indiana",
        "teamName": "Fever",
    }
    assert payload["wowScheduleProvenance"]["provider"] == fallback.WEB_PROVIDER


def test_bare_numeric_game_url_remains_supported():
    bare = HTML.replace("/game/lva-vs-ind-1042600122", "/game/1042600122")
    payload = fallback.parse_official_schedule_page(bare)
    game = payload["leagueSchedule"]["gameDates"][0]["games"][0]
    assert game["gameId"] == "1042600122"


def test_captured_official_schedule_api_shape_preserves_exact_current_game_identity():
    payload = fallback._validate_official_schedule_payload(CURRENT_API_PAYLOAD)
    games = payload["leagueSchedule"]["gameDates"][0]["games"]
    assert [
        (
            game["gameId"],
            game["gameDateTimeUTC"],
            str(game["awayTeam"]["teamId"]),
            str(game["homeTeam"]["teamId"]),
        )
        for game in games
    ] == [
        ("1042600202", "2026-10-07T23:30:00Z", "1611661313", "1611661330"),
        ("1042600212", "2026-10-08T01:30:00Z", "1611661319", "1611661331"),
    ]


def test_cdn_non_json_exhausts_same_source_recovery_before_official_schedule_api():
    calls = []

    def fetcher(url, **kwargs):
        calls.append((url, kwargs))
        if url == wnba.WNBA_SCHEDULE_URL:
            return FakeResponse(payload=ValueError("Expecting value"))
        if url == fallback.SCHEDULE_API_URL:
            return FakeResponse(payload=CURRENT_API_PAYLOAD)
        raise AssertionError(f"unexpected source: {url}")

    payload = fallback.request_with_official_web_fallback(
        wnba.WNBA_SCHEDULE_URL,
        http_get=fetcher,
        headers=wnba._cdn_headers(),
    )
    games = payload["leagueSchedule"]["gameDates"][0]["games"]
    assert [game["gameId"] for game in games] == ["1042600202", "1042600212"]

    cdn_calls = [kwargs for url, kwargs in calls if url == wnba.WNBA_SCHEDULE_URL]
    assert len(cdn_calls) == wnba.HTTP_ATTEMPTS * 2
    browser_calls = cdn_calls[:wnba.HTTP_ATTEMPTS]
    minimal_calls = cdn_calls[wnba.HTTP_ATTEMPTS:]
    assert all(call["headers"].get("Host") == "cdn.wnba.com" for call in browser_calls)
    assert all("Host" not in call["headers"] for call in minimal_calls)
    assert all("Origin" not in call["headers"] for call in minimal_calls)
    assert all("Referer" not in call["headers"] for call in minimal_calls)
    assert all("Accept-Encoding" not in call["headers"] for call in minimal_calls)

    api_calls = [kwargs for url, kwargs in calls if url == fallback.SCHEDULE_API_URL]
    assert len(api_calls) == 1
    assert api_calls[0]["params"]["regionId"] == "1"
    assert api_calls[0]["params"]["season"].isdigit()
    assert api_calls[0]["headers"]["Accept"].startswith("application/json")
    assert "Host" not in api_calls[0]["headers"]
    assert fallback.SCHEDULE_PAGE_URL not in [url for url, _kwargs in calls]


def test_both_official_schedule_transports_fail_closed_with_typed_source_error():
    def fetcher(url, **kwargs):
        if url == wnba.WNBA_SCHEDULE_URL:
            return FakeResponse(payload=ValueError("bad json"))
        return FakeResponse(status_code=502, content=b"Bad gateway")

    with pytest.raises(wnba.WNBAPropHydrationError) as excinfo:
        fallback.request_with_official_web_fallback(
            wnba.WNBA_SCHEDULE_URL,
            http_get=fetcher,
            headers=wnba._cdn_headers(),
        )
    assert excinfo.value.code == "WNBA_OFFICIAL_SOURCE_UNAVAILABLE"
    assert excinfo.value.detail["primary_source"] == fallback.CDN_PROVIDER
    assert excinfo.value.detail["fallback_source"] == fallback.API_PROVIDER
    assert excinfo.value.detail["fallback_url"] == fallback.SCHEDULE_API_URL
    assert len(excinfo.value.detail["primary_minimal_errors"]) == wnba.HTTP_ATTEMPTS
    assert "RuntimeError:HTTP_502" in excinfo.value.detail["fallback_errors"]


def test_api_failure_preserves_typed_code_without_remote_body_text():
    def fetcher(url, **kwargs):
        if url == wnba.WNBA_SCHEDULE_URL:
            return FakeResponse(payload=ValueError("cdn body detail must not leak"))
        if url == fallback.SCHEDULE_API_URL:
            return FakeResponse(payload=ValueError("api body detail must not leak"))
        raise AssertionError(f"unexpected source: {url}")

    with pytest.raises(wnba.WNBAPropHydrationError) as excinfo:
        fallback.request_with_official_web_fallback(
            wnba.WNBA_SCHEDULE_URL,
            http_get=fetcher,
            headers=wnba._cdn_headers(),
        )
    errors = excinfo.value.detail["fallback_errors"]
    assert errors == ["ValueError", "ValueError"]
    assert "cdn body detail must not leak" not in str(excinfo.value.detail)
    assert "api body detail must not leak" not in str(excinfo.value.detail)


def test_plain_schedule_shell_without_game_tiles_preserves_parse_empty_code():
    shell = (
        "<html><body>"
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(NEXT_DATA)}</script>'
        "<div>Loading games</div>"
        "</body></html>"
    )
    with pytest.raises(wnba.WNBAPropHydrationError) as excinfo:
        fallback.parse_official_schedule_page(shell)
    assert excinfo.value.code == "WNBA_OFFICIAL_SCHEDULE_WEB_PARSE_EMPTY"


def test_fallback_provenance_replaces_legacy_cdn_label_without_changing_governance():
    result = fallback._apply_schedule_provenance(
        {
            "captured_at": "2026-09-29T12:00:00+00:00",
            "source_timestamps": {
                fallback.CDN_PROVIDER: "2026-09-29T12:00:00+00:00",
                "WNBA_STATS_LEAGUE_GAME_LOG": "2026-09-29T12:00:00+00:00",
            },
            "role_status": {"source": "legacy"},
        },
        fallback.API_PROVIDER,
        fallback.SCHEDULE_API_URL,
    )
    assert fallback.CDN_PROVIDER not in result["source_timestamps"]
    assert result["source_timestamps"][fallback.API_PROVIDER] == result["captured_at"]
    assert result["schedule_source_provider"] == fallback.API_PROVIDER
    assert result["role_status"]["schedule_source_provider"] == fallback.API_PROVIDER
    assert "official WNBA injury-report PDF" in result["rate_provenance"]


def test_priority_control_plane_installs_fallback_before_acquisition_import():
    repo_root = Path(__file__).resolve().parents[3]
    text = (repo_root / "artifacts" / "wow-engine" / "v17" / "wnba_prop_evidence_control_plane.py").read_text()
    fallback_import = "import v17.wnba_official_schedule_web_fallback"
    acquisition_import = "from v17 import wnba_prop_evidence_acquisition as acquisition"
    assert fallback_import in text
    assert text.index(fallback_import) < text.index(acquisition_import)
