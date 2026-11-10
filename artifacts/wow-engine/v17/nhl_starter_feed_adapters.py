"""Server-owned NHL starting-goalie source adapters.

Two independent adapters emit the canonical receipt consumed by
``v17.nhl_pregame_orchestration``:

- ``NHL_OFFICIAL_CLUB_FEED`` reads a club pregame lineup contract;
- ``NHL_OFFICIAL_LINEUP_FEED`` reads a league roster designation contract.

Both endpoints are deployment-supplied and host-allowlisted; this module never
invents an NHL URL. Every failure is a typed ``NHLStarterSourceError``, and no
adapter can produce a probability, rank or execution grant.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import re
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from v17.nhl_event_identity import team_abbreviation
from v17.nhl_pregame_orchestration import StarterReceiptAdapter
from v17.nhl_starter_goalie_confirmation import MAX_CONFIRMATION_AGE_MINUTES

CAN_EXECUTE = False
TIMEOUT_SECONDS = 8.0
MAX_BYTES = 512 * 1024
CLUB_ENDPOINT_ENV = "WOW_NHL_CLUB_LINEUP_FEED_URL"
LEAGUE_ENDPOINT_ENV = "WOW_NHL_LEAGUE_ROSTER_FEED_URL"
CLUB_ALLOWED_HOSTS = ("www.nhl.com",)
LEAGUE_ALLOWED_HOSTS = ("api-web.nhle.com",)

_EVENT_ID = re.compile(r"[0-9]{10}\Z")
_ABBREV = re.compile(r"[A-Z]{3}\Z")
_MAX_CLOCK_DRIFT_SECONDS = 60


class NHLStarterSourceError(RuntimeError):
    """Typed source failure; the orchestration turns it into a HOLD blocker."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(f"{code}:{detail}" if detail else code)


def _time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except (TypeError, ValueError, OverflowError):
            return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def _default_get(url: str, **kwargs: Any) -> Any:
    import requests

    return requests.get(url, **kwargs)


def _validate_endpoint(endpoint: str, allowed_hosts: tuple[str, ...], env_var: str) -> str:
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise NHLStarterSourceError("NHL_STARTER_FEED_ENDPOINT_UNCONFIGURED", env_var)
    url = endpoint.strip()
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in allowed_hosts
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise NHLStarterSourceError(
            "NHL_STARTER_FEED_ENDPOINT_NOT_ALLOWLISTED", f"{parsed.scheme}://{parsed.hostname}"
        )
    return url


def _fetch(
    url: str,
    *,
    get: Callable[..., Any],
    timeout_seconds: float,
    max_bytes: int,
) -> tuple[Mapping[str, Any], bytes]:
    try:
        response = get(
            url,
            timeout=timeout_seconds,
            allow_redirects=False,
            headers={
                "Accept": "application/json",
                "User-Agent": "WOW-V17-Research/1.0",
            },
        )
    except Exception as exc:  # noqa: BLE001 - transport failure is a typed hold
        raise NHLStarterSourceError(
            "NHL_STARTER_FEED_TRANSPORT_FAILED", type(exc).__name__
        ) from exc
    status = getattr(response, "status_code", None)
    if isinstance(status, int) and 300 <= status < 400:
        raise NHLStarterSourceError("NHL_STARTER_FEED_REDIRECT_REJECTED", str(status))
    if status != 200 or str(getattr(response, "url", url)) != url:
        raise NHLStarterSourceError("NHL_STARTER_FEED_HTTP_STATUS", str(status))
    content = getattr(response, "content", None)
    if not isinstance(content, bytes) or not content:
        raise NHLStarterSourceError("NHL_STARTER_FEED_PAYLOAD_INVALID", "empty")
    if len(content) > max_bytes:
        raise NHLStarterSourceError(
            "NHL_STARTER_FEED_PAYLOAD_TOO_LARGE", str(len(content))
        )
    try:
        payload = json.loads(content)
    except (ValueError, UnicodeError) as exc:
        raise NHLStarterSourceError(
            "NHL_STARTER_FEED_PAYLOAD_INVALID", "json"
        ) from exc
    if not isinstance(payload, Mapping):
        raise NHLStarterSourceError("NHL_STARTER_FEED_SCHEMA_DRIFT", "root")
    return payload, content


def _abbrev(value: Any, *, error: str = "NHL_STARTER_FEED_SCHEMA_DRIFT") -> str:
    team = value.get("abbrev") if isinstance(value, Mapping) else value
    token = str(team or "").strip().upper()
    canonical = team_abbreviation(token) if token else None
    if canonical is None or _ABBREV.fullmatch(canonical) is None:
        raise NHLStarterSourceError(error, token or "missing")
    return canonical


def _goalie_id(value: Any, side: str) -> str:
    if not isinstance(value, Mapping):
        raise NHLStarterSourceError("NHL_STARTER_FEED_SCHEMA_DRIFT", f"{side}_goalie")
    goalie_id = str(value.get("playerId") or "").strip()
    if not goalie_id.isdigit():
        raise NHLStarterSourceError("NHL_STARTER_FEED_GOALIE_MISSING", side)
    return goalie_id


def _status(value: Any, side: str) -> str:
    if not isinstance(value, Mapping):
        raise NHLStarterSourceError("NHL_STARTER_FEED_SCHEMA_DRIFT", f"{side}_goalie")
    status = str(value.get("status") or "").strip().upper()
    if not status:
        raise NHLStarterSourceError("NHL_STARTER_FEED_STATUS_MISSING", side)
    return status


def _receipt(
    *,
    provider: str,
    source_kind: str,
    event_id: str,
    source_start: datetime,
    home: str,
    away: str,
    home_goalie: str,
    away_goalie: str,
    status: str,
    observed: datetime,
    identity: Mapping[str, Any],
    content: bytes,
    source_url: str,
    as_of_utc: datetime,
) -> dict[str, Any]:
    """Bind one parsed feed row to the canonical event and stamp its receipt."""
    if _EVENT_ID.fullmatch(event_id) is None:
        raise NHLStarterSourceError("NHL_STARTER_FEED_SCHEMA_DRIFT", "event_id")
    if str(identity.get("event_id") or "") != event_id:
        raise NHLStarterSourceError("NHL_STARTER_FEED_EVENT_MISMATCH", event_id)
    canonical_start = _time(identity.get("event_start_time"))
    if (
        canonical_start is None
        or abs((canonical_start - source_start).total_seconds()) > _MAX_CLOCK_DRIFT_SECONDS
    ):
        raise NHLStarterSourceError("NHL_STARTER_FEED_START_TIME_MISMATCH", event_id)
    if (
        team_abbreviation(str(identity.get("home_team") or "")) != home
        or team_abbreviation(str(identity.get("away_team") or "")) != away
    ):
        raise NHLStarterSourceError("NHL_STARTER_FEED_TEAM_MISMATCH", f"{home}@{away}")
    now = as_of_utc.astimezone(timezone.utc)
    if observed > now:
        raise NHLStarterSourceError("NHL_STARTER_FEED_TIMESTAMP_IN_FUTURE", event_id)
    if observed >= canonical_start:
        raise NHLStarterSourceError("NHL_STARTER_FEED_AFTER_KICKOFF", event_id)
    if (now - observed).total_seconds() > MAX_CONFIRMATION_AGE_MINUTES * 60:
        raise NHLStarterSourceError("NHL_STARTER_FEED_STALE", event_id)
    if home_goalie == away_goalie:
        raise NHLStarterSourceError("NHL_STARTER_FEED_GOALIE_AMBIGUOUS", event_id)
    sha = hashlib.sha256(content).hexdigest()
    return {
        "official_event_id": event_id,
        "event_id": event_id,
        "provider": provider,
        "source_kind": source_kind,
        "source_snapshot_id": f"{provider.lower().replace('_', '-')}-{sha[:32]}",
        "source_payload_sha256": sha,
        "observed_at": observed.isoformat(),
        "event_start_time_utc": canonical_start.isoformat(),
        "home_team": home,
        "away_team": away,
        "home_goalie_id": home_goalie,
        "away_goalie_id": away_goalie,
        "starting_status": status,
        "source_url": source_url,
        "can_execute": False,
        "prediction_authority": False,
    }


def _observed_at(payload_value: Any, *, event_id: str) -> datetime:
    # Only the payload may say when the source observed the lineup. An HTTP
    # `Date` header is the server's clock, not observation evidence, so it can
    # never stand in for a missing or unparseable payload timestamp.
    observed = _time(payload_value)
    if observed is None:
        raise NHLStarterSourceError("NHL_STARTER_FEED_TIMESTAMP_MISSING", event_id)
    return observed


def collect_club_lineup_receipt(
    *,
    identity: Mapping[str, Any],
    endpoint: str,
    as_of_utc: datetime,
    get: Callable[..., Any] | None = None,
    timeout_seconds: float = TIMEOUT_SECONDS,
    max_bytes: int = MAX_BYTES,
) -> dict[str, Any]:
    """Collect one club pregame lineup contract into a canonical receipt."""
    url = _validate_endpoint(endpoint, CLUB_ALLOWED_HOSTS, CLUB_ENDPOINT_ENV)
    payload, content = _fetch(
        url,
        get=get or _default_get,
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes,
    )
    event_id = str(payload.get("eventId") or "").strip()
    source_start = _time(payload.get("startTimeUTC"))
    if source_start is None:
        raise NHLStarterSourceError("NHL_STARTER_FEED_SCHEMA_DRIFT", "startTimeUTC")
    home = _abbrev(payload.get("homeTeam"))
    away = _abbrev(payload.get("awayTeam"))
    goalies = payload.get("startingGoalies")
    if not isinstance(goalies, Mapping):
        raise NHLStarterSourceError("NHL_STARTER_FEED_SCHEMA_DRIFT", "startingGoalies")
    home_row = goalies.get("home")
    away_row = goalies.get("away")
    home_status = _status(home_row, "home")
    away_status = _status(away_row, "away")
    if home_status != away_status:
        raise NHLStarterSourceError(
            "NHL_STARTER_FEED_STATUS_DIVERGENT", f"{home_status}/{away_status}"
        )
    observed = _observed_at(payload.get("observedAt"), event_id=event_id)
    return _receipt(
        provider="NHL_OFFICIAL_CLUB_FEED",
        source_kind="STARTING_LINEUP",
        event_id=event_id,
        source_start=source_start,
        home=home,
        away=away,
        home_goalie=_goalie_id(home_row, "home"),
        away_goalie=_goalie_id(away_row, "away"),
        status=home_status,
        observed=observed,
        identity=identity,
        content=content,
        source_url=url,
        as_of_utc=as_of_utc,
    )


def collect_league_roster_receipt(
    *,
    identity: Mapping[str, Any],
    endpoint: str,
    as_of_utc: datetime,
    get: Callable[..., Any] | None = None,
    timeout_seconds: float = TIMEOUT_SECONDS,
    max_bytes: int = MAX_BYTES,
) -> dict[str, Any]:
    """Collect one league roster designation contract into a canonical receipt."""
    url = _validate_endpoint(endpoint, LEAGUE_ALLOWED_HOSTS, LEAGUE_ENDPOINT_ENV)
    payload, content = _fetch(
        url,
        get=get or _default_get,
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes,
    )
    game = payload.get("game")
    teams = payload.get("teams")
    goaltenders = payload.get("goaltenders")
    if not isinstance(game, Mapping) or not isinstance(teams, Mapping):
        raise NHLStarterSourceError("NHL_STARTER_FEED_SCHEMA_DRIFT", "game/teams")
    if not isinstance(goaltenders, list) or not goaltenders:
        raise NHLStarterSourceError("NHL_STARTER_FEED_SCHEMA_DRIFT", "goaltenders")
    event_id = str(game.get("id") or "").strip()
    source_start = _time(game.get("startTimeUTC"))
    if source_start is None:
        raise NHLStarterSourceError("NHL_STARTER_FEED_SCHEMA_DRIFT", "startTimeUTC")
    home = _abbrev(teams.get("home"))
    away = _abbrev(teams.get("away"))
    starters: dict[str, tuple[str, str]] = {}
    for row in goaltenders:
        if not isinstance(row, Mapping):
            raise NHLStarterSourceError("NHL_STARTER_FEED_SCHEMA_DRIFT", "goaltender_row")
        if str(row.get("designation") or "").strip().upper() != "STARTER":
            continue
        side = team_abbreviation(str(row.get("teamAbbrev") or ""))
        if side not in {home, away}:
            raise NHLStarterSourceError("NHL_STARTER_FEED_TEAM_NOT_IN_EVENT", side or "?")
        if side in starters:
            raise NHLStarterSourceError("NHL_STARTER_FEED_GOALIE_AMBIGUOUS", side)
        starters[side] = (_goalie_id(row, side), _status(row, side))
    if set(starters) != {home, away}:
        raise NHLStarterSourceError(
            "NHL_STARTER_FEED_GOALIE_MISSING", sorted({home, away} - set(starters))[0]
        )
    home_goalie, home_status = starters[home]
    away_goalie, away_status = starters[away]
    if home_status != away_status:
        raise NHLStarterSourceError(
            "NHL_STARTER_FEED_STATUS_DIVERGENT", f"{home_status}/{away_status}"
        )
    observed = _observed_at(payload.get("updatedAt"), event_id=event_id)
    return _receipt(
        provider="NHL_OFFICIAL_LINEUP_FEED",
        source_kind="OFFICIAL_ROSTER_DESIGNATION",
        event_id=event_id,
        source_start=source_start,
        home=home,
        away=away,
        home_goalie=home_goalie,
        away_goalie=away_goalie,
        status=home_status,
        observed=observed,
        identity=identity,
        content=content,
        source_url=url,
        as_of_utc=as_of_utc,
    )


def club_lineup_adapter(
    *,
    endpoint: str | None = None,
    get: Callable[..., Any] | None = None,
    timeout_seconds: float = TIMEOUT_SECONDS,
    max_bytes: int = MAX_BYTES,
) -> StarterReceiptAdapter:
    """Build the club-feed adapter; an unconfigured endpoint fails closed."""
    configured = (
        endpoint if endpoint is not None else os.getenv(CLUB_ENDPOINT_ENV, "")
    )
    url = _validate_endpoint(str(configured), CLUB_ALLOWED_HOSTS, CLUB_ENDPOINT_ENV)

    def collect(identity: Mapping[str, Any], as_of_utc: datetime) -> dict[str, Any]:
        return collect_club_lineup_receipt(
            identity=identity,
            endpoint=url,
            as_of_utc=as_of_utc,
            get=get,
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
        )

    return StarterReceiptAdapter(
        provider="NHL_OFFICIAL_CLUB_FEED",
        source_kind="STARTING_LINEUP",
        collect=collect,
    )


def league_roster_adapter(
    *,
    endpoint: str | None = None,
    get: Callable[..., Any] | None = None,
    timeout_seconds: float = TIMEOUT_SECONDS,
    max_bytes: int = MAX_BYTES,
) -> StarterReceiptAdapter:
    """Build the league-feed adapter; an unconfigured endpoint fails closed."""
    configured = (
        endpoint if endpoint is not None else os.getenv(LEAGUE_ENDPOINT_ENV, "")
    )
    url = _validate_endpoint(str(configured), LEAGUE_ALLOWED_HOSTS, LEAGUE_ENDPOINT_ENV)

    def collect(identity: Mapping[str, Any], as_of_utc: datetime) -> dict[str, Any]:
        return collect_league_roster_receipt(
            identity=identity,
            endpoint=url,
            as_of_utc=as_of_utc,
            get=get,
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
        )

    return StarterReceiptAdapter(
        provider="NHL_OFFICIAL_LINEUP_FEED",
        source_kind="OFFICIAL_ROSTER_DESIGNATION",
        collect=collect,
    )


def build_official_starter_adapters(
    *,
    club_endpoint: str | None = None,
    league_endpoint: str | None = None,
    get: Callable[..., Any] | None = None,
    timeout_seconds: float = TIMEOUT_SECONDS,
    max_bytes: int = MAX_BYTES,
) -> list[StarterReceiptAdapter]:
    """Build both independent adapters from deployment configuration."""
    return [
        club_lineup_adapter(
            endpoint=club_endpoint,
            get=get,
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
        ),
        league_roster_adapter(
            endpoint=league_endpoint,
            get=get,
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
        ),
    ]


__all__ = [
    "CAN_EXECUTE",
    "CLUB_ALLOWED_HOSTS",
    "CLUB_ENDPOINT_ENV",
    "LEAGUE_ALLOWED_HOSTS",
    "LEAGUE_ENDPOINT_ENV",
    "MAX_BYTES",
    "NHLStarterSourceError",
    "build_official_starter_adapters",
    "club_lineup_adapter",
    "collect_club_lineup_receipt",
    "collect_league_roster_receipt",
    "league_roster_adapter",
]
