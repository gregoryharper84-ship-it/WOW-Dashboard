"""Automatic raw-evidence acquisition for certified prop routes.

P0 begins with the one currently certified fitted prop route: MLB pitcher
strikeouts. The provider acquires official MLB player identity, regular-season
pitching game logs, probable-pitcher schedule context, and -- when the official
batting order is available -- opponent hitter strikeout evidence against the
starter's throwing hand.

This module does not persist evidence, calculate probability, or authorize any
execution. The canonical pick-request runtime validates, fingerprints, freezes,
and persists the returned evidence before delegating to /score-prop.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
import time
import unicodedata

import httpx

from prop_auto_hydration_workload import WORKLOAD_STATS, hydrate_mlb_workload_evidence


MLB_STATS_API_BASE = "https://statsapi.mlb.com/api/v1"
AUTO_HYDRATION_PROVIDER = "MLB_STATS_API_OFFICIAL_V1"
AUTO_HYDRATION_EVIDENCE_VERSION = "PROP_EVIDENCE_V1"
HTTP_TIMEOUT_SECONDS = 8.0
HTTP_ATTEMPTS = 2
MIN_STARTS = 10
MIN_LINEUP_HITTERS_FOR_OPP_CONTEXT = 6
MIN_LINEUP_SPLIT_PA = 100
MIN_HITTER_SPLIT_PA = 10


class PropAutoHydrationError(RuntimeError):
    def __init__(self, code: str, message: str, *, detail: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


def _aware(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise PropAutoHydrationError(
            "PROP_EVENT_START_INVALID",
            "event_start_time must be timezone-aware ISO 8601",
        ) from exc
    if parsed.utcoffset() is None:
        raise PropAutoHydrationError(
            "PROP_EVENT_START_INVALID",
            "event_start_time must include a timezone",
        )
    return parsed.astimezone(timezone.utc)


def _name_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(ascii_text.casefold().strip().split())


def _int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _outs_from_ip(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        raise PropAutoHydrationError("MLB_GAME_LOG_INVALID", "inningsPitched missing")
    if "." not in text:
        whole, frac = text, "0"
    else:
        whole, frac = text.split(".", 1)
    if frac not in {"0", "1", "2"}:
        raise PropAutoHydrationError(
            "MLB_GAME_LOG_INVALID",
            f"invalid inningsPitched fraction: {text}",
        )
    try:
        return int(whole) * 3 + int(frac)
    except ValueError as exc:
        raise PropAutoHydrationError(
            "MLB_GAME_LOG_INVALID",
            f"invalid inningsPitched: {text}",
        ) from exc


def _request_json(
    url: str,
    *,
    params: dict[str, Any],
    http_get: Callable[..., Any],
) -> dict[str, Any]:
    errors: list[str] = []
    for attempt in range(1, HTTP_ATTEMPTS + 1):
        try:
            response = http_get(url, params=params, timeout=HTTP_TIMEOUT_SECONDS)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise TypeError("response JSON was not an object")
            return payload
        except Exception as exc:
            errors.append(type(exc).__name__)
            if attempt < HTTP_ATTEMPTS:
                time.sleep(0.05)
    raise PropAutoHydrationError(
        "PROP_AUTO_HYDRATION_PROVIDER_UNAVAILABLE",
        "official MLB evidence source could not be retrieved",
        detail={
            "provider": AUTO_HYDRATION_PROVIDER,
            "attempts": HTTP_ATTEMPTS,
            "errors": errors,
        },
    )


def _resolve_player_id(player: str, *, http_get: Callable[..., Any]) -> tuple[int, str]:
    payload = _request_json(
        f"{MLB_STATS_API_BASE}/people/search",
        params={"names": player, "active": "true", "sportIds": "1"},
        http_get=http_get,
    )
    people = payload.get("people")
    if not isinstance(people, list):
        people = []
    exact = [
        person
        for person in people
        if isinstance(person, dict)
        and _name_key(person.get("fullName")) == _name_key(player)
    ]
    if len(exact) != 1:
        raise PropAutoHydrationError(
            "PROP_PLAYER_IDENTITY_UNRESOLVED",
            "official MLB player search did not produce one exact active match",
            detail={"player": player, "exact_match_count": len(exact)},
        )
    player_id = _int(exact[0].get("id"))
    if player_id <= 0:
        raise PropAutoHydrationError(
            "PROP_PLAYER_IDENTITY_UNRESOLVED",
            "official MLB player ID was missing",
        )
    return player_id, str(exact[0].get("fullName") or player)


def _schedule_context(
    player_id: int,
    *,
    event_start: datetime,
    http_get: Callable[..., Any],
) -> dict[str, Any]:
    payload = _request_json(
        f"{MLB_STATS_API_BASE}/schedule",
        params={
            "sportId": "1",
            "startDate": (event_start.date() - timedelta(days=1)).isoformat(),
            "endDate": event_start.date().isoformat(),
            "hydrate": "probablePitcher,team,venue",
        },
        http_get=http_get,
    )
    candidates: list[tuple[float, dict[str, Any], str]] = []
    dates = payload.get("dates")
    if not isinstance(dates, list):
        dates = []
    for date_block in dates:
        games = date_block.get("games") if isinstance(date_block, dict) else None
        if not isinstance(games, list):
            continue
        for game in games:
            if not isinstance(game, dict):
                continue
            teams = game.get("teams") if isinstance(game.get("teams"), dict) else {}
            for side in ("home", "away"):
                side_data = teams.get(side) if isinstance(teams.get(side), dict) else {}
                probable = side_data.get("probablePitcher") if isinstance(side_data.get("probablePitcher"), dict) else {}
                if _int(probable.get("id")) != player_id:
                    continue
                try:
                    game_start = _aware(str(game.get("gameDate") or ""))
                except PropAutoHydrationError:
                    continue
                candidates.append((abs((game_start - event_start).total_seconds()), game, side))

    if not candidates:
        raise PropAutoHydrationError(
            "MLB_STARTER_STATUS_UNRESOLVED",
            "official MLB schedule did not list the player as a probable pitcher for the target event window",
            detail={"player_id": player_id},
        )
    candidates.sort(key=lambda item: item[0])
    delta, game, side = candidates[0]
    if delta > 12 * 60 * 60:
        raise PropAutoHydrationError(
            "PROP_EVENT_IDENTITY_CONFLICT",
            "official probable-pitcher game was too far from the requested event start",
            detail={"start_delta_seconds": delta},
        )

    teams = game.get("teams") if isinstance(game.get("teams"), dict) else {}
    other = "away" if side == "home" else "home"
    team_node = teams.get(side) if isinstance(teams.get(side), dict) else {}
    opp_node = teams.get(other) if isinstance(teams.get(other), dict) else {}
    team = team_node.get("team") if isinstance(team_node.get("team"), dict) else {}
    opponent = opp_node.get("team") if isinstance(opp_node.get("team"), dict) else {}
    venue = game.get("venue") if isinstance(game.get("venue"), dict) else {}
    status = game.get("status") if isinstance(game.get("status"), dict) else {}
    return {
        "official_game_pk": game.get("gamePk"),
        "official_game_date": game.get("gameDate"),
        "side": side.upper(),
        "team": team.get("abbreviation") or team.get("name") or "UNKNOWN",
        "team_id": _int(team.get("id")),
        "opponent": opponent.get("abbreviation") or opponent.get("name") or "UNKNOWN",
        "opponent_team_id": _int(opponent.get("id")),
        "venue": venue.get("name") or "UNKNOWN",
        "schedule_status": status.get("detailedState") or status.get("abstractGameState") or "UNKNOWN",
        "starter_status": "STARTER_PROBABLE_OFFICIAL_SCHEDULE",
    }


def _game_log(
    player_id: int,
    *,
    season: int,
    event_start: datetime,
    http_get: Callable[..., Any],
) -> tuple[list[float], list[dict[str, Any]]]:
    payload = _request_json(
        f"{MLB_STATS_API_BASE}/people/{player_id}/stats",
        params={"stats": "gameLog", "group": "pitching", "season": str(season), "gameType": "R"},
        http_get=http_get,
    )
    blocks = payload.get("stats")
    splits: list[Any] = []
    if isinstance(blocks, list):
        for block in blocks:
            if isinstance(block, dict) and isinstance(block.get("splits"), list):
                splits.extend(block["splits"])

    parsed: list[tuple[str, dict[str, Any]]] = []
    for split in splits:
        if not isinstance(split, dict):
            continue
        stat = split.get("stat")
        if not isinstance(stat, dict) or _int(stat.get("gamesStarted")) < 1:
            continue
        date_value = str(split.get("date") or "")
        try:
            game_date = datetime.fromisoformat(date_value).date()
        except ValueError:
            continue
        if game_date >= event_start.date():
            continue
        strikeouts = _int(stat.get("strikeOuts"), default=-1)
        if strikeouts < 0:
            continue
        ip = stat.get("inningsPitched")
        opponent = split.get("opponent") if isinstance(split.get("opponent"), dict) else {}
        parsed.append(
            (
                date_value,
                {
                    "date": date_value,
                    "opponent": opponent.get("abbreviation") or opponent.get("name") or "UNKNOWN",
                    "ip": str(ip),
                    "outs": _outs_from_ip(ip),
                    "bf": _int(stat.get("battersFaced")),
                    "so": strikeouts,
                    "bb": _int(stat.get("baseOnBalls")),
                    "er": _int(stat.get("earnedRuns")),
                },
            )
        )

    parsed.sort(key=lambda item: item[0], reverse=True)
    recent = [row for _, row in parsed[:MIN_STARTS]]
    if len(recent) < MIN_STARTS:
        raise PropAutoHydrationError(
            "MLB_RECENT_STARTS_INSUFFICIENT",
            "fewer than ten official regular-season starts were available before the event",
            detail={"starts_found": len(recent), "required": MIN_STARTS},
        )
    return [float(row["so"]) for row in recent], recent


def _pitcher_throwing_hand(player_id: int, *, http_get: Callable[..., Any]) -> str | None:
    payload = _request_json(
        f"{MLB_STATS_API_BASE}/people/{player_id}",
        params={},
        http_get=http_get,
    )
    people = payload.get("people") if isinstance(payload.get("people"), list) else []
    if not people or not isinstance(people[0], dict):
        return None
    hand = people[0].get("pitchHand") if isinstance(people[0].get("pitchHand"), dict) else {}
    code = str(hand.get("code") or "").upper().strip()
    return code if code in {"L", "R"} else None


def _official_opponent_lineup(
    game_pk: int,
    *,
    pitcher_side: str,
    http_get: Callable[..., Any],
) -> list[int]:
    payload = _request_json(
        f"{MLB_STATS_API_BASE}/game/{game_pk}/boxscore",
        params={},
        http_get=http_get,
    )
    teams = payload.get("teams") if isinstance(payload.get("teams"), dict) else {}
    opponent_side = "away" if pitcher_side.upper() == "HOME" else "home"
    team = teams.get(opponent_side) if isinstance(teams.get(opponent_side), dict) else {}
    batting_order = team.get("battingOrder") if isinstance(team.get("battingOrder"), list) else []
    ids = [_int(player_id) for player_id in batting_order if _int(player_id) > 0]
    return ids[:9] if len(ids) >= 9 else []


def _hitter_hand_split_k_counts(
    player_id: int,
    *,
    season: int,
    pitcher_hand: str,
    http_get: Callable[..., Any],
) -> tuple[int, int] | None:
    # MLB StatsAPI situation codes are from the hitter's perspective: vr =
    # versus right-handed pitching, vl = versus left-handed pitching.
    sit_code = "vr" if pitcher_hand == "R" else "vl"
    payload = _request_json(
        f"{MLB_STATS_API_BASE}/people/{player_id}/stats",
        params={
            "stats": "season",
            "group": "hitting",
            "season": str(season),
            "gameType": "R",
            "sitCodes": sit_code,
        },
        http_get=http_get,
    )
    blocks = payload.get("stats") if isinstance(payload.get("stats"), list) else []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        splits = block.get("splits") if isinstance(block.get("splits"), list) else []
        for split in splits:
            if not isinstance(split, dict):
                continue
            stat = split.get("stat") if isinstance(split.get("stat"), dict) else {}
            pa = _int(stat.get("plateAppearances"))
            so = _int(stat.get("strikeOuts"), default=-1)
            if pa >= MIN_HITTER_SPLIT_PA and so >= 0:
                return so, pa
    return None


def _hydrate_opponent_context(
    *,
    pitcher_id: int,
    schedule: dict[str, Any],
    season: int,
    box_score_log: list[dict[str, Any]],
    http_get: Callable[..., Any],
) -> tuple[dict[str, Any] | None, str]:
    """Hydrate optional opponent evidence without making it a hard dependency.

    The fitted model is explicitly neutral when opponent_context is absent.
    Therefore any failure in this additive official-source path returns None
    rather than converting a previously scorable row into infrastructure/model
    unavailability. No fallback rate is invented.
    """
    try:
        pitcher_hand = _pitcher_throwing_hand(pitcher_id, http_get=http_get)
        game_pk = _int(schedule.get("official_game_pk"))
        if pitcher_hand not in {"L", "R"} or game_pk <= 0:
            return None, "NEUTRAL_PITCHER_HAND_OR_GAME_UNRESOLVED"
        lineup_ids = _official_opponent_lineup(
            game_pk,
            pitcher_side=str(schedule.get("side") or ""),
            http_get=http_get,
        )
        if len(lineup_ids) < 9:
            return None, "NEUTRAL_OFFICIAL_LINEUP_NOT_CONFIRMED"

        total_so = 0
        total_pa = 0
        hitter_n = 0
        for hitter_id in lineup_ids:
            try:
                counts = _hitter_hand_split_k_counts(
                    hitter_id,
                    season=season,
                    pitcher_hand=pitcher_hand,
                    http_get=http_get,
                )
            except Exception:
                counts = None
            if counts is None:
                continue
            so, pa = counts
            total_so += so
            total_pa += pa
            hitter_n += 1

        if hitter_n < MIN_LINEUP_HITTERS_FOR_OPP_CONTEXT or total_pa < MIN_LINEUP_SPLIT_PA:
            return None, "NEUTRAL_LINEUP_HAND_SPLIT_SAMPLE_INSUFFICIENT"

        bfs = [float(row.get("bf")) for row in box_score_log if _int(row.get("bf")) > 0]
        expected_bf = (sum(bfs) / len(bfs)) if bfs else None
        context: dict[str, Any] = {
            "k_rate_per_pa": total_so / total_pa,
            "pitcher_hand": pitcher_hand,
            "lineup_status": "CONFIRMED",
            "lineup_player_ids": lineup_ids,
            "lineup_hitter_split_n": hitter_n,
            "split_plate_appearances": total_pa,
            "source": "MLB_STATS_API_OFFICIAL_LINEUP_HAND_SPLITS_V1",
            "rate_provenance": "official battingOrder; hitter season hitting split vs starter hand",
        }
        if expected_bf is not None:
            context["expected_batters_faced"] = expected_bf
        return context, "CONFIRMED_LINEUP_HAND_SPLIT"
    except Exception:
        return None, "NEUTRAL_OPTIONAL_OPPONENT_CONTEXT_UNAVAILABLE"


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
) -> dict[str, Any]:
    """Return raw auditable evidence for one supported exact prop route."""
    normalized_sport = str(sport or "").strip().upper()
    normalized_stat = str(stat_type or "").strip().upper()
    if normalized_sport == "MLB" and normalized_stat in WORKLOAD_STATS:
        return hydrate_mlb_workload_evidence(
            player=player,
            stat_type=normalized_stat,
            event_start_time=event_start_time,
            resolve_player_id=_resolve_player_id,
            schedule_context=_schedule_context,
            request_json=_request_json,
            outs_from_ip=_outs_from_ip,
            int_value=_int,
            error_type=PropAutoHydrationError,
            mlb_stats_api_base=MLB_STATS_API_BASE,
            evidence_version=AUTO_HYDRATION_EVIDENCE_VERSION,
            min_starts=MIN_STARTS,
            http_get=http_get,
            now=now,
            source_capture_timestamp=source_capture_timestamp,
            source_label=source_label,
        )
    if normalized_sport != "MLB" or normalized_stat != "PITCHER_STRIKEOUTS":
        raise PropAutoHydrationError(
            "PROP_AUTO_HYDRATION_UNSUPPORTED_ROUTE",
            "automatic evidence hydration is not certified for this sport/stat route",
            detail={"sport": normalized_sport, "stat_type": normalized_stat},
        )
    normalized_player = " ".join(str(player or "").strip().split())
    if not normalized_player:
        raise PropAutoHydrationError(
            "PROP_PLAYER_IDENTITY_REQUIRED",
            "player is required for prop hydration",
        )

    captured_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    event_start = _aware(event_start_time)
    if event_start <= captured_at:
        raise PropAutoHydrationError(
            "EVENT_ALREADY_STARTED",
            "pregame evidence cannot be hydrated after event start",
        )

    player_id, official_name = _resolve_player_id(normalized_player, http_get=http_get)
    schedule = _schedule_context(player_id, event_start=event_start, http_get=http_get)
    game_log, box_score_log = _game_log(
        player_id,
        season=event_start.year,
        event_start=event_start,
        http_get=http_get,
    )
    opponent_context, opponent_context_status = _hydrate_opponent_context(
        pitcher_id=player_id,
        schedule=schedule,
        season=event_start.year,
        box_score_log=box_score_log,
        http_get=http_get,
    )

    timestamp = captured_at.isoformat()
    source_timestamps = {
        "MLB_STATS_API_PLAYER_SEARCH": timestamp,
        "MLB_STATS_API_PITCHING_GAME_LOG": timestamp,
        "MLB_STATS_API_SCHEDULE_PROBABLE_PITCHER": timestamp,
    }
    if opponent_context is not None:
        source_timestamps["MLB_STATS_API_OPPONENT_LINEUP"] = timestamp
        source_timestamps["MLB_STATS_API_HITTER_HAND_SPLITS"] = timestamp
    if source_capture_timestamp:
        source_timestamps[f"INPUT_CAPTURE_{str(source_label).strip().upper()}"] = source_capture_timestamp

    payload: dict[str, Any] = {
        "captured_at": timestamp,
        "game_log": game_log,
        "box_score_log": box_score_log,
        "role_status": {
            "status": schedule["starter_status"],
            "role": "STARTING_PITCHER",
            "confirmation_strength": "OFFICIAL_PROBABLE_PITCHER",
            "team": schedule["team"],
            "opponent": schedule["opponent"],
            "venue": schedule["venue"],
            "official_game_pk": schedule["official_game_pk"],
            "official_game_date": schedule["official_game_date"],
            "schedule_status": schedule["schedule_status"],
            "source": "MLB StatsAPI official schedule/probablePitcher",
        },
        "role_timestamp": timestamp,
        "opportunity_ledger": {
            "status": "READY",
            "game_log_stat": "pitcher strikeouts",
            "box_score_alignment": "1:1",
            "regular_season_prior_starts": len(box_score_log),
            "starter_confirmation": "OFFICIAL_PROBABLE_PITCHER",
            "model_opponent_context": opponent_context_status,
        },
        "source_timestamps": source_timestamps,
        "evidence_version": AUTO_HYDRATION_EVIDENCE_VERSION,
        "rate_provenance": "MLB StatsAPI official pitching gameLog; outs derived from inningsPitched",
    }
    if opponent_context is not None:
        payload["opponent_context"] = opponent_context
    return payload
