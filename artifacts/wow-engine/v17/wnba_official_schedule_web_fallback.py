"""Official WNBA schedule-web fallback for V17 prop hydration.

The legacy public WNBA CDN schedule endpoint intermittently returns an HTTP-success
non-JSON body or 502.  This adapter keeps the evidence boundary league-owned: it
tries the existing CDN JSON contract first, then parses the server-rendered
official wnba.com schedule page into the exact minimal ScheduleLeagueV2 shape
consumed by the existing hydrator.

No third-party schedule, market data, probability, calibration, certification,
publication, ranking, promotion, or execution authority is introduced.
``can_execute`` remains false.
"""
from __future__ import annotations

from contextvars import ContextVar
from html import unescape
from html.parser import HTMLParser
import json
import re
import time
from typing import Any, Callable, Mapping

import wnba_prop_auto_hydration as wnba

CAN_EXECUTE = False
SCHEDULE_PAGE_URL = "https://www.wnba.com/schedule?month=all"
CDN_PROVIDER = "WNBA_CDN_SCHEDULE_CURRENT"
WEB_PROVIDER = "WNBA_OFFICIAL_SCHEDULE_WEB_SSR"

_ORIGINAL_REQUEST = wnba._request
_ORIGINAL_HYDRATE = wnba.hydrate_wnba_prop_evidence
_INSTALLED = False
_SCHEDULE_SOURCE: ContextVar[tuple[str, str]] = ContextVar(
    "wow_wnba_schedule_source",
    default=(CDN_PROVIDER, wnba.WNBA_SCHEDULE_URL),
)
_GAME_HREF = re.compile(r"/game/(\d+)(?:[/?#]|$)")
_LOGO_TEAM_ID = re.compile(r"/logos/wnba/(\d+)/")
_NEXT_DATA = re.compile(
    r"<script[^>]*\bid=[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)


def _web_headers() -> dict[str, str]:
    return {
        "Host": "www.wnba.com",
        "User-Agent": wnba._stats_headers()["User-Agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Referer": "https://www.wnba.com/",
        "Pragma": "no-cache",
        "Cache-Control": "no-cache",
    }


def _request_json_once(url: str, *, http_get: Callable[..., Any], headers: Mapping[str, str]) -> dict[str, Any]:
    response = http_get(
        url,
        params={},
        headers=dict(headers),
        timeout=wnba.HTTP_TIMEOUT_SECONDS,
        follow_redirects=True,
    )
    status = int(getattr(response, "status_code", 200))
    if status >= 400:
        raise RuntimeError(f"HTTP_{status}")
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise TypeError("JSON_NOT_OBJECT")
    return dict(payload)


def _request_html_once(url: str, *, http_get: Callable[..., Any]) -> str:
    response = http_get(
        url,
        params={},
        headers=_web_headers(),
        timeout=wnba.HTTP_TIMEOUT_SECONDS,
        follow_redirects=True,
    )
    status = int(getattr(response, "status_code", 200))
    if status >= 400:
        raise RuntimeError(f"HTTP_{status}")
    raw = getattr(response, "content", None)
    if raw is None:
        text = str(getattr(response, "text", ""))
    else:
        text = bytes(raw).decode("utf-8", errors="replace")
    if not text.strip():
        raise TypeError("EMPTY_BODY")
    return text


def _retry(call: Callable[[], Any]) -> tuple[Any | None, list[str]]:
    errors: list[str] = []
    for attempt in range(1, wnba.HTTP_ATTEMPTS + 1):
        try:
            return call(), errors
        except Exception as exc:
            errors.append(f"{type(exc).__name__}:{exc}")
            if attempt < wnba.HTTP_ATTEMPTS:
                time.sleep(0.05)
    return None, errors


def _find_team_registry(node: Any, output: dict[str, dict[str, str]]) -> None:
    if isinstance(node, Mapping):
        tid = str(node.get("tid") or "").strip()
        tricode = str(node.get("ta") or "").strip().upper()
        name = str(node.get("tn") or "").strip()
        city = str(node.get("tc") or "").strip()
        if tid and tricode and name:
            output[tid] = {
                "teamId": tid,
                "teamTricode": tricode,
                "teamCity": city,
                "teamName": name,
            }
        for value in node.values():
            _find_team_registry(value, output)
    elif isinstance(node, list):
        for value in node:
            _find_team_registry(value, output)


def _team_registry(html: str) -> dict[str, dict[str, str]]:
    match = _NEXT_DATA.search(html)
    if not match:
        return {}
    try:
        payload = json.loads(unescape(match.group(1)))
    except Exception:
        return {}
    output: dict[str, dict[str, str]] = {}
    _find_team_registry(payload, output)
    return output


class _GameTileParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.current: dict[str, Any] | None = None
        self.game_depth: int | None = None
        self.side: str | None = None
        self.side_depth: int | None = None
        self.games: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.depth += 1
        values = {str(key): value for key, value in attrs}
        if self.current is None and tag == "a":
            match = _GAME_HREF.search(str(values.get("href") or ""))
            if match:
                self.current = {
                    "gameId": match.group(1),
                    "gameStatus": 1,
                    "gameStatusText": "Scheduled",
                    "homeTeam": {},
                    "awayTeam": {},
                }
                self.game_depth = self.depth

        if self.current is None:
            return
        if tag == "time" and values.get("datetime"):
            self.current["gameDateTimeUTC"] = str(values["datetime"])
            self.current["gameDateUTC"] = str(values["datetime"])
        elif tag == "div":
            classes = str(values.get("class") or "")
            if "team--away" in classes:
                self.side = "awayTeam"
                self.side_depth = self.depth
            elif "team--home" in classes:
                self.side = "homeTeam"
                self.side_depth = self.depth
        elif self.side and tag == "img":
            match = _LOGO_TEAM_ID.search(str(values.get("src") or ""))
            if match:
                self.current[self.side]["teamId"] = match.group(1)
        elif self.side and tag == "p" and values.get("aria-label"):
            self.current[self.side]["displayName"] = " ".join(
                str(values["aria-label"]).split()
            )

    def handle_endtag(self, tag: str) -> None:
        if (
            self.current is not None
            and tag == "div"
            and self.side_depth is not None
            and self.depth == self.side_depth
        ):
            self.side = None
            self.side_depth = None
        if (
            self.current is not None
            and tag == "a"
            and self.game_depth is not None
            and self.depth == self.game_depth
        ):
            self.games.append(self.current)
            self.current = None
            self.game_depth = None
            self.side = None
            self.side_depth = None
        self.depth = max(0, self.depth - 1)


def parse_official_schedule_page(html: str) -> dict[str, Any]:
    parser = _GameTileParser()
    parser.feed(html)
    registry = _team_registry(html)
    games: list[dict[str, Any]] = []
    for raw in parser.games:
        game = dict(raw)
        valid = bool(game.get("gameId") and game.get("gameDateTimeUTC"))
        for side in ("awayTeam", "homeTeam"):
            node = dict(game.get(side) or {})
            team_id = str(node.get("teamId") or "").strip()
            official = registry.get(team_id)
            if not official:
                valid = False
                break
            display = str(node.get("displayName") or "").strip()
            official_full = " ".join(
                [official.get("teamCity", "").strip(), official.get("teamName", "").strip()]
            ).strip()
            if display and wnba._name_key(display) != wnba._name_key(official_full):
                valid = False
                break
            game[side] = dict(official)
        if valid:
            games.append(game)
    if not games:
        raise wnba.WNBAPropHydrationError(
            "WNBA_OFFICIAL_SCHEDULE_WEB_PARSE_EMPTY",
            "official WNBA schedule page did not yield exact game/team identities",
            detail={"source": WEB_PROVIDER, "url": SCHEDULE_PAGE_URL},
        )
    return {
        "leagueSchedule": {"gameDates": [{"games": games}]},
        "wowScheduleProvenance": {
            "provider": WEB_PROVIDER,
            "url": SCHEDULE_PAGE_URL,
            "game_n": len(games),
        },
    }


def request_with_official_web_fallback(
    url: str,
    *,
    http_get: Callable[..., Any],
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    expect_json: bool = True,
) -> Any:
    if str(url) != wnba.WNBA_SCHEDULE_URL or not expect_json:
        return _ORIGINAL_REQUEST(
            url,
            http_get=http_get,
            params=params,
            headers=headers,
            expect_json=expect_json,
        )

    payload, primary_errors = _retry(
        lambda: _request_json_once(
            wnba.WNBA_SCHEDULE_URL,
            http_get=http_get,
            headers=headers or wnba._cdn_headers(),
        )
    )
    if payload is not None:
        _SCHEDULE_SOURCE.set((CDN_PROVIDER, wnba.WNBA_SCHEDULE_URL))
        return payload

    html, fallback_errors = _retry(
        lambda: _request_html_once(SCHEDULE_PAGE_URL, http_get=http_get)
    )
    if html is not None:
        try:
            payload = parse_official_schedule_page(str(html))
            _SCHEDULE_SOURCE.set((WEB_PROVIDER, SCHEDULE_PAGE_URL))
            return payload
        except Exception as exc:
            fallback_errors.append(f"{type(exc).__name__}:{exc}")

    raise wnba.WNBAPropHydrationError(
        "WNBA_OFFICIAL_SOURCE_UNAVAILABLE",
        "both official WNBA schedule transports were unavailable or invalid",
        detail={
            "primary_source": CDN_PROVIDER,
            "fallback_source": WEB_PROVIDER,
            "primary_errors": primary_errors[-4:],
            "fallback_errors": fallback_errors[-4:],
        },
    )


def _apply_schedule_provenance(result: dict[str, Any], provider: str, url: str) -> dict[str, Any]:
    timestamp = str(result.get("captured_at") or "")
    sources = dict(result.get("source_timestamps") or {})
    sources.pop(CDN_PROVIDER, None)
    if timestamp:
        sources[provider] = timestamp
    result["source_timestamps"] = sources
    result["schedule_source_provider"] = provider
    result["schedule_source_url"] = url

    role = result.get("role_status")
    if isinstance(role, dict):
        role["schedule_source_provider"] = provider
        role["schedule_source_url"] = url
        role["source"] = (
            f"{provider} + WNBA Stats roster + official WNBA injury report"
        )
    result["rate_provenance"] = (
        "Official WNBA LeagueGameLog player rows; current event/team from "
        f"{provider}; roster from CommonTeamRoster; availability from official "
        "WNBA injury-report PDF"
    )
    return result


def hydrate_with_official_schedule_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
    token = _SCHEDULE_SOURCE.set((CDN_PROVIDER, wnba.WNBA_SCHEDULE_URL))
    try:
        result = _ORIGINAL_HYDRATE(*args, **kwargs)
        provider, url = _SCHEDULE_SOURCE.get()
        return _apply_schedule_provenance(dict(result), provider, url)
    finally:
        _SCHEDULE_SOURCE.reset(token)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    wnba._request = request_with_official_web_fallback
    wnba.hydrate_wnba_prop_evidence = hydrate_with_official_schedule_fallback
    _INSTALLED = True


install()


__all__ = [
    "CAN_EXECUTE",
    "CDN_PROVIDER",
    "SCHEDULE_PAGE_URL",
    "WEB_PROVIDER",
    "hydrate_with_official_schedule_fallback",
    "install",
    "parse_official_schedule_page",
    "request_with_official_web_fallback",
]
