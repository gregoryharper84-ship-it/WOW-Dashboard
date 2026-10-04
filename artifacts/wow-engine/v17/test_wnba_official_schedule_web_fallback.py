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



PLAYOFF_NEXT_DATA = {
    "props": {
        "pageProps": {
            "playoffsData": {
                "playoffBracketSeries": [
                    {
                        "roundNumber": 2,
                        "seriesNumber": 0,
                        "highSeedId": 1611661330,
                        "highSeedCity": "Atlanta",
                        "highSeedName": "Dream",
                        "highSeedTricode": "ATL",
                        "lowSeedId": 1611661313,
                        "lowSeedCity": "New York",
                        "lowSeedName": "Liberty",
                        "lowSeedTricode": "NYL",
                        "nextGameId": "1042600201",
                        "nextGameNumber": "Game 1",
                        "nextGameDateTimeUTC": "2026-10-04T18:00:00Z",
                        "nextGameStatus": 1,
                        "nextGameStatusText": "Sun 2:00 pm ET",
                        "nextGameNeutralSite": False,
                    },
                    {
                        "roundNumber": 2,
                        "seriesNumber": 1,
                        "highSeedId": 1611661331,
                        "highSeedCity": "Golden State",
                        "highSeedName": "Valkyries",
                        "highSeedTricode": "GSV",
                        "lowSeedId": 1611661319,
                        "lowSeedCity": "Las Vegas",
                        "lowSeedName": "Aces",
                        "lowSeedTricode": "LVA",
                        "nextGameId": "1042600211",
                        "nextGameNumber": "Game 1",
                        "nextGameDateTimeUTC": "2026-10-04T20:00:00Z",
                        "nextGameStatus": 1,
                        "nextGameStatusText": "Sun 4:00 pm ET",
                        "nextGameNeutralSite": False,
                    },
                    {
                        "roundNumber": 3,
                        "seriesNumber": 0,
                        "highSeedId": 0,
                        "highSeedCity": "",
                        "highSeedName": "",
                        "highSeedTricode": "",
                        "lowSeedId": 0,
                        "lowSeedCity": "",
                        "lowSeedName": "",
                        "lowSeedTricode": "",
                        "nextGameId": "1042600301",
                        "nextGameNumber": "Game 1",
                        "nextGameDateTimeUTC": "2026-10-17T19:30:00Z",
                        "nextGameStatus": 1,
                        "nextGameStatusText": "Oct 17 3:30 pm ET",
                        "nextGameNeutralSite": False,
                    },
                ]
            }
        }
    }
}
PLAYOFF_HTML = (
    "<html><body><script id=\"__NEXT_DATA__\" type=\"application/json\">"
    + json.dumps(PLAYOFF_NEXT_DATA)
    + "</script></body></html>"
)

class FakeResponse:
    def __init__(self, *, status_code=200, payload=None, content=b""):
        self.status_code = status_code
        self._payload = payload
        self.content = content

    def json(self):
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload



def test_parses_official_playoff_bracket_next_games_with_exact_identity():
    payload = fallback.parse_official_playoffs_page(PLAYOFF_HTML)
    games = payload["leagueSchedule"]["gameDates"][0]["games"]
    assert [game["gameId"] for game in games] == ["1042600201", "1042600211"]
    first, second = games
    assert first["gameDateTimeUTC"] == "2026-10-04T18:00:00Z"
    assert first["homeTeam"]["teamTricode"] == "ATL"
    assert first["awayTeam"]["teamTricode"] == "NYL"
    assert second["gameDateTimeUTC"] == "2026-10-04T20:00:00Z"
    assert second["homeTeam"]["teamTricode"] == "GSV"
    assert second["awayTeam"]["teamTricode"] == "LVA"
    provenance = payload["wowScheduleProvenance"]
    assert provenance["provider"] == fallback.PLAYOFFS_PROVIDER
    assert provenance["market_features_used"] is False
    assert provenance["probability_authority"] is False
    assert provenance["can_execute"] is False


def test_playoff_bracket_runs_after_empty_schedule_ssr():
    calls = []

    def fetcher(url, **kwargs):
        calls.append(url)
        if url == wnba.WNBA_SCHEDULE_URL:
            return FakeResponse(payload=ValueError("bad json"))
        if url == fallback.SCHEDULE_PAGE_URL:
            return FakeResponse(content=b"<html><body>No Games Matched Your Search</body></html>")
        if url == fallback.PLAYOFFS_PAGE_URL:
            return FakeResponse(content=PLAYOFF_HTML.encode("utf-8"))
        raise AssertionError(url)

    payload = fallback.request_with_official_web_fallback(
        wnba.WNBA_SCHEDULE_URL,
        http_get=fetcher,
        headers=wnba._cdn_headers(),
    )
    games = payload["leagueSchedule"]["gameDates"][0]["games"]
    assert [game["gameId"] for game in games] == ["1042600201", "1042600211"]
    assert calls[-1] == fallback.PLAYOFFS_PAGE_URL


def test_playoff_bracket_fails_closed_on_neutral_site_ambiguity():
    altered = json.loads(json.dumps(PLAYOFF_NEXT_DATA))
    altered["props"]["pageProps"]["playoffsData"]["playoffBracketSeries"][0][
        "nextGameNeutralSite"
    ] = True
    html = (
        "<script id=\"__NEXT_DATA__\" type=\"application/json\">"
        + json.dumps(altered)
        + "</script>"
    )
    with pytest.raises(wnba.WNBAPropHydrationError) as excinfo:
        fallback.parse_official_playoffs_page(html)
    assert excinfo.value.code == "WNBA_OFFICIAL_PLAYOFF_BRACKET_NEUTRAL_SITE_UNSUPPORTED"


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


def test_cdn_non_json_exhausts_same_source_recovery_before_official_web_fallback():
    calls = []

    def fetcher(url, **kwargs):
        calls.append((url, kwargs))
        if url == wnba.WNBA_SCHEDULE_URL:
            return FakeResponse(payload=ValueError("Expecting value"))
        if url == fallback.SCHEDULE_PAGE_URL:
            return FakeResponse(content=HTML.encode("utf-8"))
        raise AssertionError(f"unexpected source: {url}")

    payload = fallback.request_with_official_web_fallback(
        wnba.WNBA_SCHEDULE_URL,
        http_get=fetcher,
        headers=wnba._cdn_headers(),
    )
    game = payload["leagueSchedule"]["gameDates"][0]["games"][0]
    assert game["gameId"] == "1042600122"

    cdn_calls = [kwargs for url, kwargs in calls if url == wnba.WNBA_SCHEDULE_URL]
    assert len(cdn_calls) == wnba.HTTP_ATTEMPTS * 2
    browser_calls = cdn_calls[:wnba.HTTP_ATTEMPTS]
    minimal_calls = cdn_calls[wnba.HTTP_ATTEMPTS:]
    assert all(call["headers"].get("Host") == "cdn.wnba.com" for call in browser_calls)
    assert all("Host" not in call["headers"] for call in minimal_calls)
    assert all("Origin" not in call["headers"] for call in minimal_calls)
    assert all("Referer" not in call["headers"] for call in minimal_calls)
    assert all("Accept-Encoding" not in call["headers"] for call in minimal_calls)

    assert [url for url, _kwargs in calls][-1] == fallback.SCHEDULE_PAGE_URL
    assert all(
        url in {wnba.WNBA_SCHEDULE_URL, fallback.SCHEDULE_PAGE_URL}
        for url, _kwargs in calls
    )
    web_call = next(kwargs for url, kwargs in calls if url == fallback.SCHEDULE_PAGE_URL)
    assert web_call["headers"]["Host"] == "www.wnba.com"
    assert "text/html" in web_call["headers"]["Accept"]
    assert web_call["follow_redirects"] is True


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
    assert excinfo.value.detail["fallback_source"] == fallback.WEB_PROVIDER
    assert len(excinfo.value.detail["primary_minimal_errors"]) == wnba.HTTP_ATTEMPTS
    assert "RuntimeError:HTTP_502" in excinfo.value.detail["fallback_errors"]


def test_parse_failure_preserves_typed_code_without_remote_body_text():
    malformed_html = "<html><body><a href='/game/lva-vs-ind-1042600122'></a></body></html>"

    def fetcher(url, **kwargs):
        if url == wnba.WNBA_SCHEDULE_URL:
            return FakeResponse(payload=ValueError("remote body detail must not leak"))
        return FakeResponse(content=malformed_html.encode("utf-8"))

    with pytest.raises(wnba.WNBAPropHydrationError) as excinfo:
        fallback.request_with_official_web_fallback(
            wnba.WNBA_SCHEDULE_URL,
            http_get=fetcher,
            headers=wnba._cdn_headers(),
        )
    errors = excinfo.value.detail["fallback_errors"]
    assert errors == ["WNBAPropHydrationError:WNBA_OFFICIAL_SCHEDULE_WEB_PARSE_EMPTY"]
    assert "remote body detail must not leak" not in str(excinfo.value.detail)


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
        fallback.WEB_PROVIDER,
        fallback.SCHEDULE_PAGE_URL,
    )
    assert fallback.CDN_PROVIDER not in result["source_timestamps"]
    assert result["source_timestamps"][fallback.WEB_PROVIDER] == result["captured_at"]
    assert result["schedule_source_provider"] == fallback.WEB_PROVIDER
    assert result["role_status"]["schedule_source_provider"] == fallback.WEB_PROVIDER
    assert "official WNBA injury-report PDF" in result["rate_provenance"]


def test_priority_control_plane_installs_fallback_before_acquisition_import():
    repo_root = Path(__file__).resolve().parents[3]
    text = (repo_root / "artifacts" / "wow-engine" / "v17" / "wnba_prop_evidence_control_plane.py").read_text()
    fallback_import = "import v17.wnba_official_schedule_web_fallback"
    acquisition_import = "from v17 import wnba_prop_evidence_acquisition as acquisition"
    assert fallback_import in text
    assert text.index(fallback_import) < text.index(acquisition_import)
