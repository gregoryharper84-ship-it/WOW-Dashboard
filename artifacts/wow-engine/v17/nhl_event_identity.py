"""First-party NHL current-event identity reconciliation for V17 Free-Core.

Provider discovery ids (for example ESPN ids) remain aliases. Canonical event
identity is assigned only after an exact participant + start-time match against
the NHL-owned club season schedule endpoint already used by WOW research. The
response body is read and parsed once. The identity fingerprint covers only
the canonical matched-game identity fields. It is NOT a source-body snapshot
digest or a substitute for a persisted, independently revalidated receipt.

This module has no sporting-probability, market-price, calibration, ranking, or
execution authority.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import datetime, timezone
from typing import Any, Mapping

import requests

CAN_EXECUTE = False
SOURCE_PROVIDER = "NHL_PUBLIC_WEB_API"
BASE_URL = "https://api-web.nhle.com/v1/club-schedule-season/{team}/{season_id}"
START_TOLERANCE_MINUTES = 30.0

_TEAM_ALIASES = {
    "anaheimducks": "ANA",
    "bostonbruins": "BOS",
    "buffalosabres": "BUF",
    "carolinahurricanes": "CAR",
    "columbusbluejackets": "CBJ",
    "calgaryflames": "CGY",
    "chicagoblackhawks": "CHI",
    "coloradoavalanche": "COL",
    "dallasstars": "DAL",
    "detroitredwings": "DET",
    "edmontonoilers": "EDM",
    "floridapanthers": "FLA",
    "losangeleskings": "LAK",
    "minnesotawild": "MIN",
    "montrealcanadiens": "MTL",
    "newjerseydevils": "NJD",
    "nashvillepredators": "NSH",
    "newyorkislanders": "NYI",
    "newyorkrangers": "NYR",
    "ottawasenators": "OTT",
    "philadelphiaflyers": "PHI",
    "pittsburghpenguins": "PIT",
    "seattlekraken": "SEA",
    "sanjosesharks": "SJS",
    "stlouisblues": "STL",
    "tampabaylightning": "TBL",
    "torontomapleleafs": "TOR",
    "utahmammoth": "UTA",
    "utahhockeyclub": "UTA",
    "vancouvercanucks": "VAN",
    "vegasgoldenknights": "VGK",
    "winnipegjets": "WPG",
    "washingtoncapitals": "WSH",
    # Historical alias retained only for old slates.
    "arizonacoyotes": "ARI",
}


_TEAM_ABBREVIATIONS = frozenset(_TEAM_ALIASES.values())


class NHLEventIdentityError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def _norm(value: Any) -> str:
    """ASCII-fold a participant name to the alias table's key form.

    NFKD decomposes accented Latin letters and the combining marks are dropped,
    so "Montréal" folds to "montreal". This is never fuzzy or wildcard
    matching: the folded token must still equal a known full-name key exactly.
    """
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return "".join(
        ch for ch in text if ch.isalnum() and not unicodedata.combining(ch)
    )


def team_abbreviation(value: Any) -> str | None:
    token = _norm(value)
    if not token:
        return None
    if len(token) in {2, 3} and token.upper() in _TEAM_ABBREVIATIONS:
        return token.upper()
    return _TEAM_ALIASES.get(token)


def _aware(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise NHLEventIdentityError("NHL_EVENT_TIME_INVALID", str(value)) from exc
    if parsed.utcoffset() is None:
        raise NHLEventIdentityError("NHL_EVENT_TIME_INVALID", str(value))
    return parsed.astimezone(timezone.utc)


def _response_body(response: Any) -> bytes:
    """Return the raw body bytes, or fail closed.

    Read once and parse these same bytes. This prevents competing .json()
    and .content reads, but does not imply that season-feed bytes were
    persisted or can be re-fetched unchanged after unrelated games finish.
    """
    body = getattr(response, "content", None)
    if not isinstance(body, (bytes, bytearray)) or not body:
        raise NHLEventIdentityError("NHL_OFFICIAL_SOURCE_BODY_UNAVAILABLE")
    return bytes(body)


def season_start_year(event_time: datetime) -> int:
    return event_time.year if event_time.month >= 7 else event_time.year - 1


def season_id(start_year: int) -> int:
    return int(f"{int(start_year)}{int(start_year) + 1}")


def resolve_nhl_current_event_identity(
    *,
    event_start_time: str,
    home_team: str,
    away_team: str,
    session: Any = requests,
    timeout: int = 12,
) -> dict[str, Any]:
    event_time = _aware(event_start_time)
    home = team_abbreviation(home_team)
    away = team_abbreviation(away_team)
    if not home or not away or home == away:
        raise NHLEventIdentityError(
            "NHL_EVENT_PARTICIPANT_IDENTITY_UNRESOLVED",
            f"{home_team}|{away_team}",
        )

    sid = season_id(season_start_year(event_time))
    url = BASE_URL.format(team=home, season_id=sid)
    try:
        response = session.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "WOW-V17-Free-Core/1.0"},
        )
    except Exception as exc:  # noqa: BLE001
        raise NHLEventIdentityError(
            "NHL_OFFICIAL_SOURCE_TRANSPORT_FAILED",
            type(exc).__name__,
        ) from exc

    if int(getattr(response, "status_code", 0) or 0) == 429:
        raise NHLEventIdentityError("NHL_OFFICIAL_SOURCE_RATE_LIMITED")
    if int(getattr(response, "status_code", 0) or 0) != 200:
        raise NHLEventIdentityError(
            f"NHL_OFFICIAL_SOURCE_HTTP_{getattr(response, 'status_code', 'UNKNOWN')}"
        )
    body = _response_body(response)
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise NHLEventIdentityError(
            "NHL_OFFICIAL_SOURCE_RESPONSE_INVALID",
            type(exc).__name__,
        ) from exc

    games = payload.get("games") if isinstance(payload, Mapping) else None
    if not isinstance(games, list):
        raise NHLEventIdentityError("NHL_OFFICIAL_SOURCE_SCHEMA_DRIFT")

    match: Mapping[str, Any] = {}
    matched_ids: set[str] = set()
    for game in games:
        if not isinstance(game, Mapping):
            continue
        home_payload = game.get("homeTeam")
        away_payload = game.get("awayTeam")
        if not isinstance(home_payload, Mapping) or not isinstance(away_payload, Mapping):
            continue
        if str(home_payload.get("abbrev") or "").strip().upper() != home:
            continue
        if str(away_payload.get("abbrev") or "").strip().upper() != away:
            continue
        game_id = str(game.get("id") or "").strip()
        start_raw = str(game.get("startTimeUTC") or "").strip()
        if not game_id or not start_raw:
            continue
        try:
            start = _aware(start_raw)
        except NHLEventIdentityError:
            continue
        delta = abs((event_time - start).total_seconds()) / 60.0
        if delta <= START_TOLERANCE_MINUTES:
            matched_ids.add(game_id)
            match = game

    if not matched_ids:
        raise NHLEventIdentityError("NHL_CANONICAL_EVENT_NOT_FOUND")
    if len(matched_ids) != 1:
        raise NHLEventIdentityError("NHL_CANONICAL_EVENT_AMBIGUOUS")

    # Canonicalize ONLY the actual matched game's six identity fields.
    # Do not hash the mutable entire season schedule, whose other 80+ games
    # change states, scores and times independently of this fixture.
    identity_match = {
        "id": str(match.get("id")),
        "startTimeUTC": str(match.get("startTimeUTC")),
        "homeTeam": {"abbrev": home},
        "awayTeam": {"abbrev": away},
        "gameType": match.get("gameType"),
        "gameState": match.get("gameState"),
    }
    identity_match_sha256 = hashlib.sha256(
        json.dumps(identity_match, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()

    return {
        "event_id": next(iter(matched_ids)),
        "event_start_time": _aware(match.get("startTimeUTC")).isoformat(),
        "home_team": home,
        "away_team": away,
        "game_type": match.get("gameType"),
        "game_state": match.get("gameState"),
        "identity_provider": SOURCE_PROVIDER,
        "identity_source": url,
        "identity_match_sha256": identity_match_sha256,
        "identity_match": identity_match,
        "identity_verified_at": datetime.now(timezone.utc).isoformat(),
        "market_features_used": False,
        "prediction_authority": False,
        "can_execute": False,
    }


__all__ = [
    "BASE_URL",
    "CAN_EXECUTE",
    "NHLEventIdentityError",
    "SOURCE_PROVIDER",
    "resolve_nhl_current_event_identity",
    "season_id",
    "season_start_year",
    "team_abbreviation",
]
