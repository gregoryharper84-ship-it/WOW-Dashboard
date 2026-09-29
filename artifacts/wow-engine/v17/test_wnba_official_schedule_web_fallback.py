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

LIVE_CSS_MODULE_HTML = f"""
<html><body>
<script id="__NEXT_DATA__" type="application/json">{json.dumps(NEXT_DATA)}</script>
<a class="_GameTile__game_7rsre_30" href="https://www.wnba.com/game/1042600122">
  <time datetime="2026-09-29T22:30:00Z">6:30 PM</time>
  <div class="_GameTile__team_7rsre_60 _GameTile__team--away_7rsre_75">
    <img src="https://cdn.wnba.com/logos/wnba/1611661319/primary/L/logo.svg">
    <p class="_TeamName__name_1jlxy_11" aria-label="Las Vegas Aces">Las Vegas Aces</p>
  </div>
  <div class="_GameTile__team_7rsre_60 _GameTile__team--home_7rsre_78">
    <img src="https://cdn.wnba.com/logos/wnba/1611661325/primary/L/logo.svg">
    <p class="_TeamName__name_1jlxy_11" aria-label="Indiana Fever">Indiana Fever</p>
  </div>
</a>
</body></html>
"""


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


def test_parses_live_css_module_team_roles_and_bare_game_id():
    payload = fallback.parse_official_schedule_page(LIVE_CSS_MODULE_HTML)
    games = payload["leagueSchedule"]["gameDates"][0]["games"]
    assert len(games) == 1
    game = games[0]
    assert game["gameId"] == "1042600122"
    assert game["gameDateTimeUTC"] == "2026-09-29T22:30:00Z"
    assert game["awayTeam"]["teamId"] == "1611661319"
    assert game["awayTeam"]["teamTricode"] == "LVA"
    assert game["homeTeam"]["teamId"] == "1611661325"
    assert game["homeTeam"]["teamTricode"] == "IND"


def test_similar_non_wnba_team_role_classes_remain_fail_closed():
    ambiguous = LIVE_CSS_MODULE_HTML.replace(
        "_GameTile__team--away_7rsre_75",
        "_OtherTile__team--away_7rsre_75",
    ).replace(
        "_GameTile__team--home_7rsre_78",
        "_OtherTile__team--home_7rsre_78",
    )
    with pytest.raises(wnba.WNBAPropHydrationError) as excinfo:
        fallback.parse_official_schedule_page(ambiguous)
    assert excinfo.value.code == "WNBA_OFFICIAL_SCHEDULE_WEB_PARSE_EMPTY"


def test_bare_numeric_game_url_remains_supported():
    bare = HTML.replace("/game/lva-vs-ind-1042600122", "/game/1042600122")
    payload = fallback.parse_official_schedule_page(bare)
    game = payload["leagueSchedule"]["gameDates"][0]["games"][0]
    assert game["gameId"] == "1042600122"


def test_cdn_non_json_fails_over_only_to_official_wnba_schedule_page():
    calls = []

    def fetcher(url, **kwargs):
        calls.append((url, kwargs))
        if url == wnba.WNBA_SCHEDULE_URL:
            return FakeResponse(payload=ValueError("Expecting value"))
        if url == fallback.SCHEDULE_PAGE_URL:
            return FakeResponse(content=LIVE_CSS_MODULE_HTML.encode("utf-8"))
        raise AssertionError(f"unexpected source: {url}")

    payload = fallback.request_with_official_web_fallback(
        wnba.WNBA_SCHEDULE_URL,
        http_get=fetcher,
        headers=wnba._cdn_headers(),
    )
    game = payload["leagueSchedule"]["gameDates"][0]["games"][0]
    assert game["gameId"] == "1042600122"
    assert [url for url, _kwargs in calls].count(wnba.WNBA_SCHEDULE_URL) == wnba.HTTP_ATTEMPTS
    assert fallback.SCHEDULE_PAGE_URL in [url for url, _kwargs in calls]
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
