"""Read-only ESPN NFL roster *negative* availability proof. #2131

This producer never attests game-day ACTIVE. ESPN roster group membership is
not a certified, complete, event-specific inactive list. It can supply fresh,
explicit injured-reserve/out evidence, but absence never authorizes scoring.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

import httpx

ESPN_ROSTER_BASE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams"
MAX_RESPONSE_BYTES = 2_000_000
MAX_SOURCE_AGE_SECONDS = 24 * 60 * 60
TIMEOUT_SECONDS = 3.0


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None


def read_espn_nfl_roster_negative(
    *,
    team: str,
    espn_athlete_id: str,
    now: datetime | None = None,
    http_get: Callable[..., Any] = httpx.get,
) -> dict[str, Any]:
    """Read one fixed-host roster; emit negative proof only after strict checks.

    This source is ESPN's published roster, not an official NFL game-day active
    list. A provider error, malformed data, old data, wrong team, or absence of
    a player returns UNVERIFIED. Caller controls only bounded path identifiers.
    """
    result: dict[str, Any] = {
        "code": "PLAYER_AVAILABILITY_UNVERIFIED",
        "source": "ESPN_NFL_TEAM_ROSTER_NEGATIVE_ONLY",
        "can_execute": False,
        "probability_publishable": False,
    }
    team_code = str(team or "").strip().upper()
    athlete_id = str(espn_athlete_id or "").strip()
    if not re.fullmatch(r"[A-Z]{2,3}", team_code) or not re.fullmatch(r"[0-9]{3,12}", athlete_id):
        return {**result, "reason": "ATHLETE_TEAM_IDENTITY_MISSING"}
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        return {**result, "reason": "SOURCE_CLOCK_UNVERIFIED"}
    current = current.astimezone(timezone.utc)
    try:
        response = http_get(
            f"{ESPN_ROSTER_BASE}/{team_code.lower()}/roster",
            params={},
            headers={"User-Agent": "WOW-Research/1.0", "Accept": "application/json"},
            timeout=TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        if int(getattr(response, "status_code", 0)) != 200:
            return {**result, "reason": "SOURCE_HTTP_NOT_OK"}
        if len(getattr(response, "content", b"")) > MAX_RESPONSE_BYTES:
            return {**result, "reason": "SOURCE_RESPONSE_OVERSIZE"}
        payload = response.json()
    except Exception:
        return {**result, "reason": "SOURCE_TRANSPORT_UNAVAILABLE"}
    if not isinstance(payload, Mapping):
        return {**result, "reason": "SOURCE_SCHEMA_INVALID"}
    source_time = _utc(payload.get("timestamp"))
    if source_time is None or not 0 <= (current - source_time).total_seconds() <= MAX_SOURCE_AGE_SECONDS:
        return {**result, "reason": "SOURCE_TIMESTAMP_STALE_OR_MISSING"}
    provider_team = payload.get("team")
    if not isinstance(provider_team, Mapping) or str(provider_team.get("abbreviation") or "").strip().upper() != team_code:
        return {**result, "reason": "SOURCE_TEAM_MISMATCH"}
    groups = payload.get("athletes")
    if not isinstance(groups, list):
        return {**result, "reason": "SOURCE_SCHEMA_INVALID"}
    found: list[tuple[str, Mapping[str, Any]]] = []
    for group in groups:
        if not isinstance(group, Mapping):
            continue
        label = str(group.get("position") or group.get("name") or "").strip().lower()
        athletes = group.get("items")
        if not isinstance(athletes, list):
            continue
        for athlete in athletes:
            if not isinstance(athlete, Mapping):
                continue
            if str(athlete.get("id") or "") == athlete_id:
                found.append((label, athlete))
    if len(found) != 1:
        return {**result, "reason": "ATHLETE_NOT_UNIQUELY_PRESENT"}
    group_label, athlete = found[0]
    status = athlete.get("status")
    status_type = str(status.get("type") or "").lower() if isinstance(status, Mapping) else ""
    explicit_out = group_label == "injuredreserveorout" or status_type in {
        "injured-reserve", "inactive", "out", "suspended", "pup", "nfi"
    }
    if explicit_out:
        return {
            **result,
            "code": "PLAYER_UNAVAILABLE",
            "reason": "ESPN_ROSTER_IR_OR_OUT",
            "source_timestamp": source_time.isoformat(),
            "espn_athlete_id": athlete_id,
            "source_team": team_code,
        }
    return {
        **result,
        "reason": "GAME_DAY_ACTIVE_STATUS_NOT_ATTESTED",
        "source_timestamp": source_time.isoformat(),
    }


__all__ = ["read_espn_nfl_roster_negative"]
