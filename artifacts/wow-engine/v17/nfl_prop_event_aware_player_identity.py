"""Probability-neutral NFL prop player-identity disambiguation.

ESPN search can return multiple exact-name NFL athlete identities. The legacy
hydrator failed before using the already-known target event, causing valid rows
to terminate with PROP_PLAYER_IDENTITY_UNRESOLVED. This overlay keeps the
failure closed but narrows exact-name candidates using current team + target
pregame event (+ requested opponent when supplied).

No probability, model, calibration, ranking, publication, or execution behavior
is changed. can_execute remains false.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Callable, Mapping, Optional

import nfl_prop_auto_hydration as nfl
from v17 import nfl_prop_boundary_integrity as boundary

_PATCH_FLAG = "_wow_nfl_event_aware_player_identity_installed"
_SELECTED: ContextVar[Optional[tuple[str, str, str]]] = ContextVar(
    "wow_nfl_selected_player_identity", default=None
)

_ORIGINAL_RESOLVE = nfl._resolve_espn_athlete
_ORIGINAL_HYDRATE = nfl.hydrate_nfl_prop_evidence
_ORIGINAL_BOUNDARY_RESOLVE = boundary.resolve_nfl_event_identity


def _search_exact_nfl_candidates(player: str, *, http_get: Callable[..., Any]) -> list[tuple[str, str]]:
    payload = nfl._request(
        nfl.ESPN_SEARCH_URL,
        params={"query": player, "limit": 10, "type": "player"},
        http_get=http_get,
    )
    matches: list[tuple[str, str]] = []
    for result in payload.get("results", []):
        if not isinstance(result, Mapping) or result.get("type") != "player":
            continue
        for item in result.get("contents", []):
            if not isinstance(item, Mapping):
                continue
            if nfl._name_key(item.get("displayName")) != nfl._name_key(player):
                continue
            if "NFL" not in str(item.get("description") or "").upper():
                continue
            uid = str(item.get("uid") or "")
            if "~a:" not in uid:
                continue
            matches.append((uid.split("~a:")[-1], str(item.get("displayName") or player)))
    return list(dict.fromkeys(matches))


def _opponent_matches(requested: Optional[str], official_abbr: Any) -> bool:
    if not requested:
        return True
    requested_key = nfl._name_key(requested)
    abbr = str(official_abbr or "").upper().strip()
    if requested_key == nfl._name_key(abbr):
        return True
    try:
        import prop_auto_hydration_router as router
        aliases = router._NFL_TEAM_ALIASES.get(abbr, ())
    except Exception:
        aliases = ()
    return requested_key in {nfl._name_key(value) for value in aliases}


def select_espn_athlete_for_event(
    *,
    player: str,
    event_start_time: str,
    opponent: Optional[str],
    http_get: Callable[..., Any],
    provider_event_id: Optional[str] = None,
    current_event_id: Optional[str] = None,
) -> tuple[str, str]:
    """Resolve an exact-name ESPN athlete by the already-known target event."""
    candidates = _search_exact_nfl_candidates(player, http_get=http_get)
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise nfl.NFLPropHydrationError(
            "PROP_PLAYER_IDENTITY_UNRESOLVED",
            "ESPN NFL athlete search produced no exact-name NFL player",
            detail={"player": player, "match_n": 0},
        )

    event_start = nfl._aware(event_start_time)
    survivors: list[tuple[str, str, str, dict[str, Any]]] = []
    candidate_teams: list[str] = []
    for athlete_id, official_name in candidates:
        try:
            team = nfl._athlete_team(athlete_id, http_get=http_get)
            candidate_teams.append(team)
            target = nfl._target_event(
                event_start=event_start,
                team=team,
                opponent=opponent,
                http_get=http_get,
            )
        except nfl.NFLPropHydrationError:
            continue
        if provider_event_id and str(target.get("event_id") or "") != str(provider_event_id):
            continue
        canonical = str(target.get("verified_canonical_event_id") or "").upper()
        current = str(current_event_id or "").upper().strip()
        if current and boundary._canonical_nfl_event_id(current) and current != canonical:
            continue
        if not _opponent_matches(opponent, target.get("opponent")):
            continue
        survivors.append((athlete_id, official_name, team, target))

    if len(survivors) != 1:
        raise nfl.NFLPropHydrationError(
            "PROP_PLAYER_IDENTITY_UNRESOLVED",
            "ESPN exact-name NFL identities did not resolve to exactly one target-event player",
            detail={
                "player": player,
                "search_match_n": len(candidates),
                "match_n": len(survivors),
                "candidate_teams": sorted(set(candidate_teams)),
                "requested_opponent": opponent,
                "provider_event_id": provider_event_id,
                "current_event_id": current_event_id,
            },
        )
    athlete_id, official_name, _team, _target = survivors[0]
    return athlete_id, official_name


def _context_resolve(player: str, *, http_get: Callable[..., Any]) -> tuple[str, str]:
    selected = _SELECTED.get()
    if selected and nfl._name_key(selected[2]) == nfl._name_key(player):
        return selected[0], selected[1]
    return _ORIGINAL_RESOLVE(player, http_get=http_get)


def _hydrate_wrapper(*, player: str, stat_type: str, event_start_time: str, http_get=nfl.httpx.get,
                     now=None, source_capture_timestamp=None, source_label="NORMALIZED_PICK_REQUEST",
                     opponent=None):
    athlete_id, official_name = select_espn_athlete_for_event(
        player=player,
        event_start_time=event_start_time,
        opponent=opponent,
        http_get=http_get,
    )
    token = _SELECTED.set((athlete_id, official_name, player))
    try:
        return _ORIGINAL_HYDRATE(
            player=player,
            stat_type=stat_type,
            event_start_time=event_start_time,
            http_get=http_get,
            now=now,
            source_capture_timestamp=source_capture_timestamp,
            source_label=source_label,
            opponent=opponent,
        )
    finally:
        _SELECTED.reset(token)


def _boundary_wrapper(*, player: str, event_start_time: str, opponent=None,
                      provider_event_id=None, current_event_id=None, http_get=nfl.httpx.get):
    athlete_id, official_name = select_espn_athlete_for_event(
        player=player,
        event_start_time=event_start_time,
        opponent=opponent,
        http_get=http_get,
        provider_event_id=provider_event_id,
        current_event_id=current_event_id,
    )
    token = _SELECTED.set((athlete_id, official_name, player))
    try:
        return _ORIGINAL_BOUNDARY_RESOLVE(
            player=player,
            event_start_time=event_start_time,
            opponent=opponent,
            provider_event_id=provider_event_id,
            current_event_id=current_event_id,
            http_get=http_get,
        )
    finally:
        _SELECTED.reset(token)


def install_nfl_event_aware_player_identity() -> bool:
    if getattr(nfl, _PATCH_FLAG, False):
        return True
    nfl._resolve_espn_athlete = _context_resolve
    nfl.hydrate_nfl_prop_evidence = _hydrate_wrapper
    boundary.resolve_nfl_event_identity = _boundary_wrapper
    setattr(nfl, _PATCH_FLAG, True)
    return True


__all__ = ["install_nfl_event_aware_player_identity", "select_espn_athlete_for_event"]
