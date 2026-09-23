"""Sport-aware automatic prop hydration router.

Only routes with reviewed automatic evidence providers are dispatched. Provider
identity is returned separately for telemetry and is never caller-controlled.

V17 event-identity contract:
- ``canonical_event_id`` is the WOW/provider-neutral event identity supplied by
  the governed request boundary;
- upstream source event ids remain typed aliases and never replace the canonical
  id by guess;
- opponent/team/start-time evidence may verify an alias against the target event,
  but it never creates a sporting probability;
- unsupported sports fail closed before entering an unrelated sport hydrator.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
import re
import unicodedata
from typing import Any, Callable, Iterable, Iterator, Mapping, Optional

import httpx

from prop_auto_hydration import (
    PropAutoHydrationError,
    auto_hydrate_prop_evidence as _hydrate_mlb,
    AUTO_HYDRATION_PROVIDER as MLB_PROVIDER,
)
from v17.mlb_pitcher_fantasy_score_hydration import (
    PROVIDER_ID as MLB_PITCHER_FANTASY_SCORE_PROVIDER,
    STAT_TYPE as MLB_PITCHER_FANTASY_SCORE_STAT,
    hydrate_mlb_pitcher_fantasy_score_evidence,
)
from v17.mlb_team_event_hydration import _mlb_team_key as _canonical_mlb_team_key
from v17.wnba_composite_auto_hydration import (
    COMPONENT_COLUMNS as WNBA_COMPOSITE_COLUMNS,
    PROVIDER_ID as WNBA_COMPOSITE_PROVIDER,
    canonical_stat as _canonical_wnba_composite_stat,
    hydrate_wnba_composite_evidence,
)
import wnba_prop_auto_hydration as _wnba
from wnba_injury_status import WNBAInjuryStatusError, availability_from_report as _strict_availability
import nfl_prop_auto_hydration as _nfl

WNBA_PROVIDER = _wnba.PROVIDER_ID
NFL_PROVIDER = _nfl.PROVIDER_ID
UNREGISTERED_PROVIDER = "UNREGISTERED_PROP_HYDRATION_PROVIDER"

# The canonical scorer historically called the router without event_id/opponent.
# A request-scoped map lets the facade preserve the exact row identity without
# mutating the old core call signature. Explicit function arguments always win.
_REQUEST_IDENTITIES: ContextVar[dict[tuple[str, str, str], dict[str, Any]]] = ContextVar(
    "wow_prop_hydration_request_identities",
    default={},
)
_WNBA_TARGET: ContextVar[Optional[dict[str, Any]]] = ContextVar(
    "wow_wnba_hydration_target",
    default=None,
)


def _identity_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^A-Za-z0-9]+", " ", text).strip().casefold()
    return " ".join(text.split())


def _opponent_identity_key(sport: Any, value: Any) -> str:
    normalized_sport = str(sport or "").strip().upper()
    if normalized_sport == "MLB":
        return str(_canonical_mlb_team_key(value) or "").strip().casefold()
    return _identity_key(value)


def _row_key(sport: Any, player: Any, event_start_time: Any) -> tuple[str, str, str]:
    return (
        str(sport or "").strip().upper(),
        _identity_key(player),
        str(event_start_time or "").strip(),
    )


@contextmanager
def hydration_request_context(rows: Iterable[Any]) -> Iterator[None]:
    """Bind canonical row identities while the canonical scorer runs.

    This is acquisition plumbing only. It carries no probability, model,
    calibration, market, ranking, or execution authority.
    """
    identities: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = _row_key(
            getattr(row, "sport", None),
            getattr(row, "player", None),
            getattr(row, "event_start_time", None),
        )
        event_id = str(getattr(row, "event_id", None) or "").strip() or None
        opponent = str(getattr(row, "opponent", None) or "").strip() or None
        candidate = {
            "canonical_event_id": event_id,
            "opponent": opponent,
            "identity_ambiguous": False,
            "conflicting_event_ids": [],
        }
        existing = identities.get(key)
        conflicts = bool(
            existing is not None
            and (
                existing.get("identity_ambiguous") is True
                or existing.get("canonical_event_id") != event_id
                or _opponent_identity_key(key[0], existing.get("opponent"))
                != _opponent_identity_key(key[0], opponent)
            )
        )
        if conflicts:
            # Never downgrade a caller-supplied canonical conflict into
            # PROVIDER_IDENTITY_ONLY during the legacy core fallback. Preserve a
            # typed ambiguity marker so the fallback fails closed before fetch.
            prior_ids = list(existing.get("conflicting_event_ids") or []) if existing else []
            if existing and existing.get("canonical_event_id"):
                prior_ids.append(str(existing["canonical_event_id"]))
            if event_id:
                prior_ids.append(event_id)
            identities[key] = {
                "canonical_event_id": None,
                "opponent": None,
                "identity_ambiguous": True,
                "conflicting_event_ids": sorted(set(prior_ids)),
            }
        else:
            identities[key] = candidate
    token = _REQUEST_IDENTITIES.set(identities)
    try:
        yield
    finally:
        _REQUEST_IDENTITIES.reset(token)


def _context_identity(
    *,
    sport: str,
    player: str,
    event_start_time: str,
    canonical_event_id: Optional[str],
    opponent: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    inherited = _REQUEST_IDENTITIES.get().get(_row_key(sport, player, event_start_time), {})
    explicit_canonical = str(canonical_event_id or "").strip() or None
    if inherited.get("identity_ambiguous") is True and explicit_canonical is None:
        raise PropAutoHydrationError(
            "PROP_EVENT_IDENTITY_CONFLICT",
            "request context contains conflicting canonical event identities for the same player/start time",
            detail={
                "sport": str(sport or "").strip().upper(),
                "player": player,
                "event_start_time": event_start_time,
                "conflicting_event_ids": list(inherited.get("conflicting_event_ids") or []),
                "identity_binding_status": "AMBIGUOUS_CANONICAL_EVENT",
            },
        )
    canonical = str(explicit_canonical or inherited.get("canonical_event_id") or "").strip() or None
    requested_opponent = str(opponent or inherited.get("opponent") or "").strip() or None
    return canonical, requested_opponent


def _strict_availability_adapter(*args, **kwargs):
    try:
        return _strict_availability(*args, **kwargs)
    except WNBAInjuryStatusError as exc:
        raise _wnba.WNBAPropHydrationError(exc.code, str(exc), detail=exc.detail) from exc


_wnba._availability_from_report = _strict_availability_adapter


def _is_wnba_composite(stat_type: str) -> bool:
    try:
        return _canonical_wnba_composite_stat(stat_type) in WNBA_COMPOSITE_COLUMNS
    except _wnba.WNBAPropHydrationError:
        return False


def provider_for_sport(sport: str, stat_type: Optional[str] = None) -> str:
    normalized_sport = str(sport or "").strip().upper()
    normalized_stat = str(stat_type or "").strip().upper()
    if normalized_sport == "WNBA":
        return WNBA_COMPOSITE_PROVIDER if _is_wnba_composite(normalized_stat) else WNBA_PROVIDER
    if normalized_sport == "NFL":
        return NFL_PROVIDER
    if normalized_sport == "MLB" and normalized_stat == MLB_PITCHER_FANTASY_SCORE_STAT:
        return MLB_PITCHER_FANTASY_SCORE_PROVIDER
    if normalized_sport == "MLB":
        return MLB_PROVIDER
    return UNREGISTERED_PROVIDER


_NFL_TEAM_ALIASES: dict[str, tuple[str, ...]] = {
    "ARI": ("ARIZONA CARDINALS", "CARDINALS", "ARIZONA"),
    "ATL": ("ATLANTA FALCONS", "FALCONS", "ATLANTA"),
    "BAL": ("BALTIMORE RAVENS", "RAVENS", "BALTIMORE"),
    "BUF": ("BUFFALO BILLS", "BILLS", "BUFFALO"),
    "CAR": ("CAROLINA PANTHERS", "PANTHERS", "CAROLINA"),
    "CHI": ("CHICAGO BEARS", "BEARS", "CHICAGO"),
    "CIN": ("CINCINNATI BENGALS", "BENGALS", "CINCINNATI"),
    "CLE": ("CLEVELAND BROWNS", "BROWNS", "CLEVELAND"),
    "DAL": ("DALLAS COWBOYS", "COWBOYS", "DALLAS"),
    "DEN": ("DENVER BRONCOS", "BRONCOS", "DENVER"),
    "DET": ("DETROIT LIONS", "LIONS", "DETROIT"),
    "GB": ("GREEN BAY PACKERS", "PACKERS", "GREEN BAY", "GNB"),
    "HOU": ("HOUSTON TEXANS", "TEXANS", "HOUSTON"),
    "IND": ("INDIANAPOLIS COLTS", "COLTS", "INDIANAPOLIS"),
    "JAX": ("JACKSONVILLE JAGUARS", "JAGUARS", "JACKSONVILLE", "JAC"),
    "KC": ("KANSAS CITY CHIEFS", "CHIEFS", "KANSAS CITY", "KAN"),
    "LV": ("LAS VEGAS RAIDERS", "RAIDERS", "LAS VEGAS", "OAK"),
    "LAC": ("LOS ANGELES CHARGERS", "CHARGERS", "LA CHARGERS"),
    "LAR": ("LOS ANGELES RAMS", "RAMS", "LA RAMS", "STL"),
    "MIA": ("MIAMI DOLPHINS", "DOLPHINS", "MIAMI"),
    "MIN": ("MINNESOTA VIKINGS", "VIKINGS", "MINNESOTA"),
    "NE": ("NEW ENGLAND PATRIOTS", "PATRIOTS", "NEW ENGLAND", "NWE"),
    "NO": ("NEW ORLEANS SAINTS", "SAINTS", "NEW ORLEANS", "NOR"),
    "NYG": ("NEW YORK GIANTS", "GIANTS", "NY GIANTS"),
    "NYJ": ("NEW YORK JETS", "JETS", "NY JETS"),
    "PHI": ("PHILADELPHIA EAGLES", "EAGLES", "PHILADELPHIA"),
    "PIT": ("PITTSBURGH STEELERS", "STEELERS", "PITTSBURGH"),
    "SEA": ("SEATTLE SEAHAWKS", "SEAHAWKS", "SEATTLE"),
    "SF": ("SAN FRANCISCO 49ERS", "49ERS", "SAN FRANCISCO", "SFO"),
    "TB": ("TAMPA BAY BUCCANEERS", "BUCCANEERS", "BUCS", "TAMPA BAY", "TBB"),
    "TEN": ("TENNESSEE TITANS", "TITANS", "TENNESSEE"),
    "WAS": ("WASHINGTON COMMANDERS", "COMMANDERS", "WASHINGTON", "WSH"),
}


def _opponent_keys(sport: str, role_status: Mapping[str, Any]) -> set[str]:
    values = {
        role_status.get("opponent"),
        role_status.get("opponent_tricode"),
        role_status.get("opponent_name"),
    }
    keys = {_opponent_identity_key(sport, value) for value in values if str(value or "").strip()}
    if sport == "NFL":
        abbreviation = str(role_status.get("opponent") or "").strip().upper()
        aliases = _NFL_TEAM_ALIASES.get(abbreviation, ())
        keys.update(_opponent_identity_key(sport, value) for value in aliases)
        if abbreviation:
            keys.add(_opponent_identity_key(sport, abbreviation))
    return {key for key in keys if key}


def _validate_requested_opponent(
    *,
    sport: str,
    requested_opponent: Optional[str],
    role_status: Mapping[str, Any],
) -> None:
    if not requested_opponent:
        return
    requested = _opponent_identity_key(sport, requested_opponent)
    allowed = _opponent_keys(sport, role_status)
    if requested and requested in allowed:
        return
    raise PropAutoHydrationError(
        "PROP_EVENT_IDENTITY_CONFLICT",
        "requested opponent did not match the hydrated official event",
        detail={
            "sport": sport,
            "requested_opponent": requested_opponent,
            "official_opponent": role_status.get("opponent"),
            "official_opponent_tricode": role_status.get("opponent_tricode"),
        },
    )


def _provider_event_ids(sport: str, role_status: Mapping[str, Any]) -> dict[str, str]:
    if sport == "NFL":
        value = role_status.get("provider_event_id") or role_status.get("event_id")
        return {"ESPN": str(value)} if str(value or "").strip() else {}
    if sport == "WNBA":
        value = role_status.get("official_game_id")
        return {"WNBA_OFFICIAL": str(value)} if str(value or "").strip() else {}
    if sport == "MLB":
        value = role_status.get("official_game_pk")
        return {"MLB_STATS_API": str(value)} if str(value or "").strip() else {}
    return {}


def _validate_parseable_canonical_alias(
    *,
    sport: str,
    canonical_event_id: Optional[str],
    provider_event_ids: Mapping[str, str],
) -> None:
    # Daily's MLB canonical identity is explicitly MLB:<gamePk>. That prefix is
    # safe to reconcile numerically. Opaque WOW NFL ids are deliberately NOT
    # compared to ESPN ids; ESPN remains an alias only.
    if sport != "MLB" or not canonical_event_id:
        return
    text = str(canonical_event_id).strip()
    if not text.upper().startswith("MLB:"):
        return
    suffix = text.split(":", 1)[1].strip()
    provider_value = str(provider_event_ids.get("MLB_STATS_API") or "").strip()
    if suffix and provider_value and suffix != provider_value:
        raise PropAutoHydrationError(
            "PROP_EVENT_IDENTITY_CONFLICT",
            "canonical MLB event id did not match the official MLB schedule alias",
            detail={
                "canonical_event_id": text,
                "provider": "MLB_STATS_API",
                "provider_event_id": provider_value,
            },
        )


def _bind_event_identity(
    result: Mapping[str, Any],
    *,
    sport: str,
    canonical_event_id: Optional[str],
    requested_opponent: Optional[str],
) -> dict[str, Any]:
    bound = dict(result)
    role = dict(bound.get("role_status") or {})
    _validate_requested_opponent(
        sport=sport,
        requested_opponent=requested_opponent,
        role_status=role,
    )
    aliases = _provider_event_ids(sport, role)
    _validate_parseable_canonical_alias(
        sport=sport,
        canonical_event_id=canonical_event_id,
        provider_event_ids=aliases,
    )
    if canonical_event_id:
        role["canonical_event_id"] = canonical_event_id
    existing_aliases = role.get("provider_event_ids")
    merged_aliases = dict(existing_aliases) if isinstance(existing_aliases, Mapping) else {}
    merged_aliases.update(aliases)
    role["provider_event_ids"] = merged_aliases
    role["identity_binding_status"] = "PASS" if canonical_event_id else "PROVIDER_IDENTITY_ONLY"
    bound["role_status"] = role
    return bound


_WNBA_ORIGINAL_SCHEDULE = _wnba._schedule


def _wnba_schedule_adapter(event_start: datetime, *, http_get: Callable[..., Any]) -> dict[str, Any]:
    """Resolve tied WNBA tip times with player/team identity, not time alone."""
    try:
        return _WNBA_ORIGINAL_SCHEDULE(event_start, http_get=http_get)
    except _wnba.WNBAPropHydrationError as exc:
        target = _WNBA_TARGET.get()
        if exc.code != "PROP_EVENT_IDENTITY_CONFLICT" or not target:
            raise

    payload = _wnba._request(
        _wnba.WNBA_SCHEDULE_URL,
        http_get=http_get,
        headers={"User-Agent": _wnba._stats_headers()["User-Agent"], "Accept": "application/json"},
    )
    league = payload.get("leagueSchedule")
    blocks = league.get("gameDates") if isinstance(league, Mapping) else None
    if not isinstance(blocks, list):
        raise _wnba.WNBAPropHydrationError("WNBA_SCHEDULE_INVALID", "leagueSchedule.gameDates was missing")

    candidates: list[tuple[float, dict[str, Any]]] = []
    for block in blocks:
        games = block.get("games") if isinstance(block, Mapping) else None
        if not isinstance(games, list):
            continue
        for game in games:
            if not isinstance(game, Mapping):
                continue
            raw_start = game.get("gameDateTimeUTC") or game.get("gameDateUTC")
            if not raw_start:
                continue
            try:
                scheduled = _wnba._aware(raw_start)
            except _wnba.WNBAPropHydrationError:
                continue
            delta = abs((scheduled - event_start).total_seconds())
            if delta <= _wnba.MAX_EVENT_START_DELTA_SECONDS:
                candidates.append((delta, dict(game)))
    if not candidates:
        raise _wnba.WNBAPropHydrationError(
            "PROP_EVENT_IDENTITY_CONFLICT",
            "no official WNBA schedule event matched the requested start time",
            detail={"event_start_time": event_start.isoformat()},
        )

    nearest = min(delta for delta, _game in candidates)
    tied = [game for delta, game in candidates if abs(delta - nearest) < 1.0]
    matches: list[dict[str, Any]] = []
    for game in tied:
        try:
            resolved = _wnba._resolve_player_and_team(
                game,
                str(target["player"]),
                int(target["season"]),
                http_get=http_get,
            )
            role = {
                "opponent": " ".join(
                    str(resolved["opponent"].get("teamCity") or "").split()
                    + str(resolved["opponent"].get("teamName") or "").split()
                ).strip(),
                "opponent_tricode": str(resolved["opponent"].get("teamTricode") or "").strip().upper(),
            }
            _validate_requested_opponent(
                sport="WNBA",
                requested_opponent=target.get("opponent"),
                role_status=role,
            )
        except (KeyError, TypeError, ValueError, _wnba.WNBAPropHydrationError, PropAutoHydrationError):
            continue
        matches.append(game)

    if len(matches) != 1:
        raise _wnba.WNBAPropHydrationError(
            "PROP_EVENT_IDENTITY_CONFLICT",
            "player/team identity did not resolve exactly one WNBA event at the requested tip time",
            detail={
                "event_start_time": event_start.isoformat(),
                "player": target.get("player"),
                "requested_opponent": target.get("opponent"),
                "matching_event_n": len(matches),
            },
        )
    game = matches[0]
    if int(game.get("gameStatus") or 0) != 1:
        raise _wnba.WNBAPropHydrationError(
            "EVENT_ALREADY_STARTED",
            "official WNBA schedule no longer marks the target event as scheduled",
            detail={"game_status": game.get("gameStatus"), "game_status_text": game.get("gameStatusText")},
        )
    return game


_wnba._schedule = _wnba_schedule_adapter


def auto_hydrate_prop_evidence(
    *,
    sport: str,
    player: str,
    stat_type: str,
    event_start_time: str,
    http_get: Callable[..., Any] = httpx.get,
    now: Optional[datetime] = None,
    source_capture_timestamp: Optional[str] = None,
    source_label: str = "NORMALIZED_PICK_REQUEST",
    opponent: Optional[str] = None,
    canonical_event_id: Optional[str] = None,
) -> dict[str, Any]:
    normalized_sport = str(sport or "").strip().upper()
    normalized_stat = str(stat_type or "").strip().upper()
    canonical_event_id, opponent = _context_identity(
        sport=normalized_sport,
        player=player,
        event_start_time=event_start_time,
        canonical_event_id=canonical_event_id,
        opponent=opponent,
    )

    if normalized_sport == "WNBA":
        token = _WNBA_TARGET.set({"player": player, "opponent": opponent, "season": _wnba._aware(event_start_time).year})
        try:
            try:
                if _is_wnba_composite(normalized_stat):
                    result = hydrate_wnba_composite_evidence(
                        player=player,
                        stat_type=stat_type,
                        event_start_time=event_start_time,
                        http_get=http_get,
                        now=now,
                        source_capture_timestamp=source_capture_timestamp,
                        source_label=source_label,
                        opponent=opponent,
                    )
                else:
                    result = _wnba.hydrate_wnba_prop_evidence(
                        player=player,
                        stat_type=stat_type,
                        event_start_time=event_start_time,
                        http_get=http_get,
                        now=now,
                        source_capture_timestamp=source_capture_timestamp,
                        source_label=source_label,
                        opponent=opponent,
                    )
            except _wnba.WNBAPropHydrationError as exc:
                raise PropAutoHydrationError(exc.code, str(exc), detail=exc.detail) from exc
        finally:
            _WNBA_TARGET.reset(token)
        result = dict(result)
        result.pop("hydration_provider", None)
        return _bind_event_identity(
            result,
            sport=normalized_sport,
            canonical_event_id=canonical_event_id,
            requested_opponent=opponent,
        )

    if normalized_sport == "NFL":
        try:
            result = _nfl.hydrate_nfl_prop_evidence(
                player=player,
                stat_type=stat_type,
                event_start_time=event_start_time,
                http_get=http_get,
                now=now,
                source_capture_timestamp=source_capture_timestamp,
                source_label=source_label,
                opponent=opponent,
            )
        except _nfl.NFLPropHydrationError as exc:
            raise PropAutoHydrationError(exc.code, str(exc), detail=exc.detail) from exc
        result = dict(result)
        result.pop("hydration_provider", None)
        return _bind_event_identity(
            result,
            sport=normalized_sport,
            canonical_event_id=canonical_event_id,
            requested_opponent=opponent,
        )

    if normalized_sport != "MLB":
        raise PropAutoHydrationError(
            "PROP_AUTO_HYDRATION_UNSUPPORTED_ROUTE",
            "automatic evidence hydration is not registered for this sport/stat route",
            detail={
                "sport": normalized_sport,
                "stat_type": normalized_stat,
                "provider": UNREGISTERED_PROVIDER,
            },
        )

    if normalized_stat == MLB_PITCHER_FANTASY_SCORE_STAT:
        result = hydrate_mlb_pitcher_fantasy_score_evidence(
            player=player,
            event_start_time=event_start_time,
            http_get=http_get,
            now=now,
            source_capture_timestamp=source_capture_timestamp,
            source_label=source_label,
        )
    else:
        result = _hydrate_mlb(
            sport=normalized_sport,
            player=player,
            stat_type=stat_type,
            event_start_time=event_start_time,
            http_get=http_get,
            now=now,
            source_capture_timestamp=source_capture_timestamp,
            source_label=source_label,
        )
    return _bind_event_identity(
        result,
        sport=normalized_sport,
        canonical_event_id=canonical_event_id,
        requested_opponent=opponent,
    )


__all__ = [
    "NFL_PROVIDER",
    "UNREGISTERED_PROVIDER",
    "WNBA_PROVIDER",
    "auto_hydrate_prop_evidence",
    "hydration_request_context",
    "provider_for_sport",
]
