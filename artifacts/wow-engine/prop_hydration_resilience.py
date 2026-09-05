"""Shared fail-closed helpers for MLB prop evidence hydration resilience.

These helpers only reconcile official player identity and retrieve official prior
MLB regular-season game-log splits. They do not estimate missing values, alter
model math, calibrate probabilities, or authorize execution.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Optional
import unicodedata

COMMON_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
MAX_HISTORY_SEASONS = 3  # event season plus two prior seasons


def normalized_name(value: Any, *, strip_suffix: bool = False) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = "".join(ch for ch in text if not unicodedata.combining(ch))
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in ascii_text.casefold())
    parts = [part for part in cleaned.split() if part]
    if strip_suffix and parts and parts[-1] in COMMON_SUFFIXES:
        parts = parts[:-1]
    return " ".join(parts)


def _iter_schedule_games(payload: dict[str, Any]):
    dates = payload.get("dates") if isinstance(payload.get("dates"), list) else []
    for date_block in dates:
        games = date_block.get("games") if isinstance(date_block, dict) else None
        if not isinstance(games, list):
            continue
        for game in games:
            if isinstance(game, dict):
                yield game


def _matching_people(payload: dict[str, Any], *, strict_key: str, suffixless_key: str) -> dict[int, dict[str, Any]]:
    people = payload.get("people") if isinstance(payload.get("people"), list) else []
    matches: dict[int, dict[str, Any]] = {}
    for person in people:
        if not isinstance(person, dict):
            continue
        pid = _positive_int(person.get("id"))
        if pid <= 0:
            continue
        full_name = str(person.get("fullName") or "").strip()
        if normalized_name(full_name) == strict_key or normalized_name(full_name, strip_suffix=True) == suffixless_key:
            matches[pid] = person
    return matches


def resolve_official_mlb_player(
    player: str,
    *,
    event_start: Optional[datetime],
    request_json: Callable[..., dict[str, Any]],
    http_get: Callable[..., Any],
    mlb_stats_api_base: str,
    error_type: type[RuntimeError],
) -> tuple[int, str, str]:
    """Resolve one MLB player with event identity as the ambiguity breaker.

    The established baseline contract performs active MLB people search first.
    When that search is ambiguous or unresolved, the target event's official
    probable-pitcher identity is used to prove one stable MLB ID. A broader
    people search is the final fallback. Ambiguity always fails closed.
    """
    requested = " ".join(str(player or "").strip().split())
    if not requested:
        raise error_type("PROP_PLAYER_IDENTITY_REQUIRED", "player is required for prop hydration")

    strict_key = normalized_name(requested)
    suffixless_key = normalized_name(requested, strip_suffix=True)

    active_payload = request_json(
        f"{mlb_stats_api_base}/people/search",
        params={"names": requested, "active": "true", "sportIds": "1"},
        http_get=http_get,
    )
    active_matches = _matching_people(active_payload, strict_key=strict_key, suffixless_key=suffixless_key)
    if len(active_matches) == 1:
        person = next(iter(active_matches.values()))
        return _positive_int(person.get("id")), str(person.get("fullName") or requested), "MLB_STATS_API_ACTIVE_PLAYER_SEARCH"

    if event_start is not None:
        schedule = request_json(
            f"{mlb_stats_api_base}/schedule",
            params={
                "sportId": "1",
                "startDate": (event_start.date() - timedelta(days=1)).isoformat(),
                "endDate": event_start.date().isoformat(),
                "hydrate": "probablePitcher,team,venue",
            },
            http_get=http_get,
        )
        schedule_matches: dict[int, str] = {}
        active_candidate_ids = set(active_matches)
        for game in _iter_schedule_games(schedule):
            teams = game.get("teams") if isinstance(game.get("teams"), dict) else {}
            for side in ("home", "away"):
                side_data = teams.get(side) if isinstance(teams.get(side), dict) else {}
                probable = side_data.get("probablePitcher") if isinstance(side_data.get("probablePitcher"), dict) else {}
                pid = _positive_int(probable.get("id"))
                if pid <= 0:
                    continue
                official_name = str(probable.get("fullName") or "").strip()
                name_matches = bool(
                    official_name
                    and (
                        normalized_name(official_name) == strict_key
                        or normalized_name(official_name, strip_suffix=True) == suffixless_key
                    )
                )
                id_disambiguates_active_search = bool(active_candidate_ids and pid in active_candidate_ids)
                if name_matches or id_disambiguates_active_search:
                    schedule_matches[pid] = official_name or str(active_matches.get(pid, {}).get("fullName") or requested)
        if len(schedule_matches) == 1:
            pid, official_name = next(iter(schedule_matches.items()))
            return pid, official_name, "MLB_STATS_API_SCHEDULE_PROBABLE_PITCHER_ID"
        if len(schedule_matches) > 1:
            raise error_type(
                "PROP_PLAYER_IDENTITY_UNRESOLVED",
                "official target-event schedule produced multiple matching probable pitchers",
                detail={"player": requested, "candidate_ids": sorted(schedule_matches)},
            )

    broad_payload = request_json(
        f"{mlb_stats_api_base}/people/search",
        params={"names": requested, "sportIds": "1"},
        http_get=http_get,
    )
    broad_matches = _matching_people(broad_payload, strict_key=strict_key, suffixless_key=suffixless_key)
    combined = {**active_matches, **broad_matches}
    if len(combined) == 1:
        person = next(iter(combined.values()))
        return _positive_int(person.get("id")), str(person.get("fullName") or requested), "MLB_STATS_API_PLAYER_SEARCH"

    raise error_type(
        "PROP_PLAYER_IDENTITY_UNRESOLVED",
        "official MLB identity sources did not produce one provable player identity",
        detail={"player": requested, "candidate_ids": sorted(combined)},
    )


def _split_identity(split: dict[str, Any]) -> tuple[Any, ...]:
    game = split.get("game") if isinstance(split.get("game"), dict) else {}
    game_pk = _positive_int(game.get("gamePk"))
    if game_pk:
        return ("game_pk", game_pk)
    opponent = split.get("opponent") if isinstance(split.get("opponent"), dict) else {}
    stat = split.get("stat") if isinstance(split.get("stat"), dict) else {}
    return (
        "fallback",
        str(split.get("date") or ""),
        opponent.get("id") or opponent.get("abbreviation") or opponent.get("name"),
        stat.get("gamesStarted"),
        stat.get("inningsPitched"),
        stat.get("strikeOuts"),
        stat.get("numberOfPitches"),
        stat.get("strikes"),
        stat.get("battersFaced"),
        stat.get("baseOnBalls"),
        stat.get("earnedRuns"),
    )


def fetch_cross_season_pitching_splits(
    player_id: int,
    *,
    event_start: datetime,
    request_json: Callable[..., dict[str, Any]],
    http_get: Callable[..., Any],
    mlb_stats_api_base: str,
    seasons_back: int = MAX_HISTORY_SEASONS,
) -> tuple[list[tuple[int, dict[str, Any]]], list[int]]:
    """Fetch de-duplicated official regular-season pitching gameLog splits.

    The caller remains responsible for filtering to starts and validating the target
    stat. Results are not padded or imputed. Seasons are queried newest first.
    A repeated provider response cannot manufacture additional historical starts.
    """
    splits: list[tuple[int, dict[str, Any]]] = []
    seasons_queried: list[int] = []
    seen_split_ids: set[tuple[Any, ...]] = set()
    for offset in range(max(1, seasons_back)):
        season = event_start.year - offset
        payload = request_json(
            f"{mlb_stats_api_base}/people/{player_id}/stats",
            params={"stats": "gameLog", "group": "pitching", "season": str(season), "gameType": "R"},
            http_get=http_get,
        )
        seasons_queried.append(season)
        blocks = payload.get("stats") if isinstance(payload.get("stats"), list) else []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_splits = block.get("splits") if isinstance(block.get("splits"), list) else []
            for split in block_splits:
                if not isinstance(split, dict):
                    continue
                split_id = _split_identity(split)
                if split_id in seen_split_ids:
                    continue
                seen_split_ids.add(split_id)
                splits.append((season, split))
    return splits, seasons_queried


def _positive_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0
