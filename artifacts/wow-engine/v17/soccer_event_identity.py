"""Canonical SOCCER current-event reconciliation for V17 Free-Core.

Discovery event ids (odds-proxy aliases or ESPN scoreboard ids) remain provider
*aliases*. WOW assigns the canonical event id ``SOCCER:<espn_event_id>`` only
after the public ESPN competition scoreboard proves exactly one unique
participant + date match inside one competition.

Competition is part of the identity question, not a decoration: the same two
clubs can meet in a league and a cup, so a match is proven *within* a
competition. Only competitions with a verified ESPN slug and an attested
discovery key are mapped; an unknown competition stays alias-only rather than
being guessed.

SOCCER IS THREE-WAY. Identity resolution never decides or implies a winner, and
never collapses a draw into an away/loser outcome. The governed outcome space is
``HOME_DRAW_AWAY`` with settlement
``REGULATION_TIME_THREE_WAY_UNLESS_MARKET_EXPLICIT``; a drawn match is a
terminal outcome, not a two-way miss. This module carries that contract forward
but grants no probability, calibration, ranking or execution authority.
can_execute is false unconditionally.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading
import time
from typing import Any, Callable, Mapping
import unicodedata

import requests

CAN_EXECUTE = False
SOURCE_PROVIDER = "ESPN_SOCCER_SCOREBOARD"
BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{competition}/scoreboard"
CANONICAL_EVENT_PREFIX = "SOCCER:"
IDENTITY_RESOLUTION = "ESPN_COMPETITION_SCOREBOARD_EXACT_PARTICIPANTS_DATE_MATCH"

# The ±1-day scoreboard fetch exists for UTC/local date partitioning, not to
# permit a different fixture day. The discovered kickoff and the official
# fixture timestamp must agree inside this drift budget; an adjacent-day
# fixture is ~24 hours away and can never satisfy it. Two hours absorbs
# ordinary broadcast/reschedule movement without merging two same-day fixtures.
MAX_KICKOFF_DRIFT_MINUTES = 120.0

# Governed outcome space. Kept here so the identity layer cannot silently become
# two-way; the parity tests assert this agrees with the SOCCER governance
# profile rather than trusting the duplicate.
OUTCOME_SPACE = "HOME_DRAW_AWAY"
DRAW_POSSIBLE = True
SETTLEMENT_CONTRACT = "REGULATION_TIME_THREE_WAY_UNLESS_MARKET_EXPLICIT"

# Only verified (HTTP 200 + ESPN-reported competition name) ESPN slugs, and only
# discovery keys that this repository actually uses. A key we cannot attest is
# deliberately absent: the row stays alias-only instead of being invented.
COMPETITION_SLUGS: dict[str, str] = {
    "eng.1": "eng.1",
    "epl": "eng.1",
    "soccer_epl": "eng.1",
    "usa.1": "usa.1",
    "mls": "usa.1",
    "soccer_usa_mls": "usa.1",
    "esp.1": "esp.1",
    "soccer_spain_la_liga": "esp.1",
    "ger.1": "ger.1",
    "soccer_germany_bundesliga": "ger.1",
    "ita.1": "ita.1",
    "soccer_italy_serie_a": "ita.1",
    "fra.1": "fra.1",
}

# Successful reads and typed failures are both cached. A failure entry keeps one
# unreachable source from being re-requested for every discovered soccer row.
_CACHE_TTL_SECONDS = 300.0
_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, list[Mapping[str, Any]] | None, str | None]] = {}

Fetcher = Callable[[str, str], list[Mapping[str, Any]]]


class SoccerEventIdentityError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(ch for ch in text if ch.isalnum() and not unicodedata.combining(ch))


def normalize_competition(value: Any) -> str | None:
    """Map a discovery key or league label to a verified ESPN competition slug."""
    token = str(value or "").strip().lower()
    return COMPETITION_SLUGS.get(token)


def _aware(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise SoccerEventIdentityError("SOCCER_EVENT_TIME_INVALID", str(value)) from exc
    if parsed.utcoffset() is None:
        raise SoccerEventIdentityError("SOCCER_EVENT_TIME_INVALID", str(value))
    return parsed.astimezone(timezone.utc)


def _team_tokens(team: Mapping[str, Any]) -> frozenset[str]:
    """Source-provided name forms for one club.

    Bare ``location`` and bare ``name`` are excluded on purpose: "Manchester"
    spans two clubs and "United" spans many, so accepting a fragment would let
    an unrelated fixture inherit this identity. Concatenating location + name
    still covers the common "Leeds United" form.
    """
    location = _norm(team.get("location"))
    tokens = {
        _norm(team.get("displayName")),
        _norm(team.get("shortDisplayName")),
        location + _norm(team.get("name")),
    }
    return frozenset(token for token in tokens if token)


def _sides(row: Mapping[str, Any]) -> dict[str, Mapping[str, Any]] | None:
    competitions = row.get("competitions")
    if not isinstance(competitions, list) or not competitions:
        return None
    competition = competitions[0]
    if not isinstance(competition, Mapping):
        return None
    sides: dict[str, Mapping[str, Any]] = {}
    for competitor in competition.get("competitors") or []:
        if not isinstance(competitor, Mapping):
            continue
        side = str(competitor.get("homeAway") or "").strip().lower()
        team = competitor.get("team")
        if side in {"home", "away"} and isinstance(team, Mapping):
            sides[side] = team
    if "home" not in sides or "away" not in sides:
        return None
    return sides


def _fetch_scoreboard(competition: str, date_key: str) -> list[Mapping[str, Any]]:
    try:
        response = requests.get(
            BASE_URL.format(competition=competition),
            params={"dates": date_key, "limit": 500},
            timeout=15,
            headers={"Accept": "application/json", "User-Agent": "WOW-V17-Free-Core/1.0"},
        )
    except Exception as exc:  # noqa: BLE001
        raise SoccerEventIdentityError(
            "SOCCER_OFFICIAL_SOURCE_TRANSPORT_FAILED", type(exc).__name__
        ) from exc

    status = int(getattr(response, "status_code", 0) or 0)
    if status == 429:
        raise SoccerEventIdentityError("SOCCER_OFFICIAL_SOURCE_RATE_LIMITED")
    if status != 200:
        raise SoccerEventIdentityError(f"SOCCER_OFFICIAL_SOURCE_HTTP_{status or 'UNKNOWN'}")
    try:
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        raise SoccerEventIdentityError(
            "SOCCER_OFFICIAL_SOURCE_RESPONSE_INVALID", type(exc).__name__
        ) from exc
    events = payload.get("events") if isinstance(payload, Mapping) else None
    if not isinstance(events, list) or any(not isinstance(event, Mapping) for event in events):
        raise SoccerEventIdentityError("SOCCER_OFFICIAL_SOURCE_SCHEMA_DRIFT")
    return list(events)


def _scoreboard_rows(
    competition: str,
    date_key: str,
    *,
    fetcher: Fetcher | None = None,
    now_monotonic: float | None = None,
) -> list[Mapping[str, Any]]:
    if fetcher is not None:
        return list(fetcher(competition, date_key))

    cache_key = f"{competition}|{date_key}"
    now_value = time.monotonic() if now_monotonic is None else float(now_monotonic)
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached is not None and (now_value - cached[0]) <= _CACHE_TTL_SECONDS:
            if cached[2] is not None:
                raise SoccerEventIdentityError(cached[2])
            return list(cached[1] or ())
    try:
        rows = list(_fetch_scoreboard(competition, date_key))
    except SoccerEventIdentityError as exc:
        with _CACHE_LOCK:
            _CACHE[cache_key] = (now_value, None, exc.code)
        raise
    with _CACHE_LOCK:
        _CACHE[cache_key] = (now_value, list(rows), None)
    return rows


def date_keys_for(event_time: datetime) -> tuple[str, ...]:
    """Scoreboard date window around the UTC kickoff (evening games cross UTC)."""
    return tuple(
        (event_time.date() + timedelta(days=offset)).strftime("%Y%m%d")
        for offset in (-1, 0, 1)
    )


def resolve_soccer_current_event_identity(
    *,
    competition: str,
    event_start_time: str,
    home_team: str,
    away_team: str,
    fetcher: Fetcher | None = None,
) -> dict[str, Any]:
    slug = normalize_competition(competition)
    if slug is None:
        # No verified competition ⇒ no legal fixture corpus to match against.
        raise SoccerEventIdentityError("SOCCER_COMPETITION_UNSUPPORTED", str(competition))

    event_time = _aware(event_start_time)
    home_norm = _norm(home_team)
    away_norm = _norm(away_team)
    if not home_norm or not away_norm or home_norm == away_norm:
        raise SoccerEventIdentityError(
            "SOCCER_PROVIDER_TEAM_ALIAS_INVALID", f"{home_team}|{away_team}"
        )

    rows: list[Mapping[str, Any]] = []
    for date_key in date_keys_for(event_time):
        rows.extend(_scoreboard_rows(slug, date_key, fetcher=fetcher))

    matches: dict[str, tuple[datetime, Mapping[str, Any]]] = {}
    for row in rows:
        event_id = str(row.get("id") or "").strip()
        if not event_id:
            continue
        sides = _sides(row)
        if sides is None:
            continue
        home_team_row = sides["home"]
        away_team_row = sides["away"]
        if home_norm not in _team_tokens(home_team_row):
            continue
        if away_norm not in _team_tokens(away_team_row):
            continue
        try:
            start = _aware(row.get("date"))
        except SoccerEventIdentityError as exc:
            # This participant match cannot be excluded from the candidate set.
            # Neither absence nor uniqueness is proven while its time is invalid.
            raise SoccerEventIdentityError(
                "SOCCER_OFFICIAL_SOURCE_SCHEMA_DRIFT", "matching fixture timestamp invalid"
            ) from exc
        # Participants alone do not prove the fixture: the same clubs can meet
        # on another day. Require the official fixture timestamp to match the
        # requested kickoff inside the declared drift budget before accepting.
        if abs((start - event_time).total_seconds()) / 60.0 > MAX_KICKOFF_DRIFT_MINUTES:
            continue
        # The ±1 day window can surface the same fixture twice; a repeated
        # source row is still one event, not an ambiguity.
        matches.setdefault(event_id, (start, row))

    if not matches:
        raise SoccerEventIdentityError("SOCCER_CANONICAL_EVENT_NOT_FOUND")
    if len(matches) != 1:
        raise SoccerEventIdentityError("SOCCER_CANONICAL_EVENT_AMBIGUOUS")

    event_id = next(iter(matches))
    row = matches[event_id][1]
    sides = _sides(row) or {}
    start = matches[event_id][0]
    return {
        "event_id": f"{CANONICAL_EVENT_PREFIX}{event_id}",
        "espn_event_id": event_id,
        "competition": slug,
        "event_start_time": start.isoformat(),
        "home_team": str((sides.get("home") or {}).get("displayName") or ""),
        "away_team": str((sides.get("away") or {}).get("displayName") or ""),
        "home_team_id": f"espn-{(sides.get('home') or {}).get('id')}",
        "away_team_id": f"espn-{(sides.get('away') or {}).get('id')}",
        "outcome_space": OUTCOME_SPACE,
        "draw_possible": DRAW_POSSIBLE,
        "settlement_contract": SETTLEMENT_CONTRACT,
        "winner_determined_by_resolver": False,
        "identity_provider": SOURCE_PROVIDER,
        "identity_resolution": IDENTITY_RESOLUTION,
        "requested_event_start_time": event_time.isoformat(),
        "kickoff_drift_minutes": round(abs((start - event_time).total_seconds()) / 60.0, 3),
        "market_features_used": False,
        "prediction_authority": False,
        "can_execute": False,
    }


def reset_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


__all__ = [
    "BASE_URL",
    "CAN_EXECUTE",
    "CANONICAL_EVENT_PREFIX",
    "COMPETITION_SLUGS",
    "DRAW_POSSIBLE",
    "IDENTITY_RESOLUTION",
    "OUTCOME_SPACE",
    "SETTLEMENT_CONTRACT",
    "SOURCE_PROVIDER",
    "SoccerEventIdentityError",
    "date_keys_for",
    "normalize_competition",
    "reset_cache",
    "resolve_soccer_current_event_identity",
]

