"""Governed read-only NFLMeta official inactives acquisition candidate. #2131.

Never a scorer or execution authorization. The authenticated provider's game
ID is independently resolved from an exact home/away, season/week and kickoff
join before accepting its game-day inactive report. Incomplete/ambiguous list
metadata, missing key, transport failure or stale evidence fail closed.

Provider use is opt-in: connection/terms and Class B independent review required
before setting WOW_NFL_INACTIVES_SHADOW_ENABLED=true. Even in shadow mode a
complete inactive list is CANDIDATE_ONLY, never a scoring permit.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

import httpx

from nfl_confirmed_inactives_attestation import (
    CANDIDATE_READY,
    UNVERIFIED,
    verify_confirmed_game_inactives,
)

BASE_URL = "https://nflmeta.org/api/v1"
TIMEOUT_SECONDS = 4.0
MAX_RESPONSE_BYTES = 2_000_000
MAX_KICKOFF_SKEW_SECONDS = 15 * 60


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.astimezone(timezone.utc) if d.utcoffset() is not None else None


def bind_nflmeta_game(
    response: Any,
    *,
    season: int,
    week: int,
    home_team: str,
    away_team: str,
    event_start: datetime,
) -> int | None:
    """Resolve one exact same-game vendor ID, never accept user-provided IDs."""
    if not isinstance(response, Mapping) or not isinstance(response.get("data"), list):
        return None
    meta = response.get("meta")
    if not isinstance(meta, Mapping):
        return None
    data_status = meta.get("data_status")
    if not isinstance(data_status, Mapping) or data_status.get("status") != "complete":
        return None
    if not isinstance(event_start, datetime) or event_start.tzinfo is None:
        return None
    t = event_start.astimezone(timezone.utc)
    hits: list[int] = []
    for item in response["data"]:
        if not isinstance(item, Mapping):
            continue
        if (
            item.get("seasonYear") != season
            or item.get("week") != week
            or item.get("isPlayoff") is not False
            or item.get("homeAbbr") != home_team
            or item.get("awayAbbr") != away_team
        ):
            continue
        kickoff = _parse_time(item.get("kickoffAt"))
        if kickoff is None or abs((kickoff - t).total_seconds()) > MAX_KICKOFF_SKEW_SECONDS:
            continue
        candidate_id = item.get("id")
        if not isinstance(candidate_id, int) or isinstance(candidate_id, bool) or candidate_id <= 0:
            continue
        hits.append(candidate_id)
    return hits[0] if len(hits) == 1 else None


def _request(
    path: str,
    *,
    params: dict[str, Any],
    key: str,
    http_get: Callable[..., Any],
) -> Mapping[str, Any] | None:
    """Fixed origin and HTTP method, credential never recorded in receipts."""
    try:
        response = http_get(
            BASE_URL + path,
            params=params,
            headers={
                "X-NFLMeta-Key": key,
                "Accept": "application/json",
                "User-Agent": "WOW-V17-ReadOnly-Inactives/1.0",
            },
            timeout=TIMEOUT_SECONDS,
            follow_redirects=False,
        )
        if getattr(response, "status_code", None) != 200:
            return None
        body = getattr(response, "content", b"")
        if not isinstance(body, bytes) or len(body) > MAX_RESPONSE_BYTES:
            return None
        payload = response.json()
        return payload if isinstance(payload, Mapping) else None
    except Exception:
        return None


def read_nflmeta_inactives_shadow(
    *,
    season: int,
    week: int,
    home_team: str,
    away_team: str,
    player_team: str,
    player_name: str,
    event_start: datetime,
    now: datetime | None = None,
    http_get: Callable[..., Any] = httpx.get,
    enabled: bool | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    base = {
        "code": UNVERIFIED,
        "source": "NFLMETA_OFFICIAL_INACTIVES_SHADOW",
        "probability_publishable": False,
        "can_execute": False,
    }
    if enabled is None:
        enabled = os.getenv("WOW_NFL_INACTIVES_SHADOW_ENABLED", "false").lower() == "true"
    if not enabled:
        return {**base, "reason": "SHADOW_NOT_ENABLED"}
    if api_key is None:
        api_key = os.getenv("WOW_NFLMETA_API_KEY", "")
    if not isinstance(api_key, str) or not api_key:
        return {**base, "reason": "PROVIDER_CREDENTIAL_UNCONFIGURED"}
    if any(not re.fullmatch(r"[A-Z]{2,3}", x) for x in (home_team, away_team, player_team)):
        return {**base, "reason": "GAME_IDENTITY_INVALID"}
    if (
        not isinstance(season, int) or isinstance(season, bool) or season < 2020 or season > 2100
        or not isinstance(week, int) or isinstance(week, bool) or not 1 <= week <= 18
    ):
        return {**base, "reason": "SEASON_WEEK_INVALID"}
    current = now or datetime.now(timezone.utc)
    if not isinstance(event_start, datetime) or event_start.tzinfo is None or current.tzinfo is None:
        return {**base, "reason": "EVENT_CLOCK_INVALID"}
    listing = _request(
        "/games",
        params={"season": season, "week": week, "team": home_team, "include_playoffs": False, "limit": 100},
        key=api_key,
        http_get=http_get,
    )
    if listing is None:
        return {**base, "reason": "PROVIDER_GAMES_UNAVAILABLE"}
    game_id = bind_nflmeta_game(
        listing, season=season, week=week,
        home_team=home_team, away_team=away_team, event_start=event_start,
    )
    if game_id is None:
        return {**base, "reason": "PROVIDER_GAME_IDENTITY_UNVERIFIED"}
    report = _request(f"/games/{game_id}/inactives", params={}, key=api_key, http_get=http_get)
    if report is None:
        return {**base, "reason": "PROVIDER_INACTIVES_UNAVAILABLE"}
    result = verify_confirmed_game_inactives(
        report,
        provider_game_id=game_id, home_team=home_team, away_team=away_team,
        player_team=player_team, player_name=player_name, event_start=event_start, now=current,
    )
    # Shadow candidates are NEVER permitted to score. Source receipts are
    # safe for auditing; omit vendor key, full roster and raw player profiles.
    return {
        **base,
        **result,
        "provider_game_id": game_id,
        "source": "NFLMETA_OFFICIAL_INACTIVES_SHADOW",
        "certification_status": "CANDIDATE_ONLY" if result["code"] == CANDIDATE_READY else "BLOCKED",
        "probability_publishable": False,
        "can_execute": False,
    }


__all__ = ["bind_nflmeta_game", "read_nflmeta_inactives_shadow"]
