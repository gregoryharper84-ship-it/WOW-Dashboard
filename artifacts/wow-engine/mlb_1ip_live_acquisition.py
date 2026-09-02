"""Live MLB 1IP evidence acquisition.

Official-source only. Produces hydrated evidence for the existing
mlb_1ip_specialist without calculating or publishing probability.
can_execute remains false.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import mean, pstdev
from typing import Any, Callable
import httpx

from prop_auto_hydration import (
    MLB_STATS_API_BASE,
    PropAutoHydrationError,
    _aware,
    _int,
    _name_key,
    _request_json,
    _resolve_player_id,
    _schedule_context,
)

PROVIDER = "MLB_STATS_API_OFFICIAL_1IP_V1"
CAN_EXECUTE = False
MIN_PRIOR_STARTS = 5
MAX_PRIOR_STARTS = 10


def _schedule_games_for_pitcher(player_id: int, event_start: datetime, http_get: Callable[..., Any]) -> list[int]:
    start = event_start.date() - timedelta(days=120)
    end = event_start.date() - timedelta(days=1)
    payload = _request_json(
        f"{MLB_STATS_API_BASE}/schedule",
        params={"sportId": "1", "startDate": start.isoformat(), "endDate": end.isoformat(), "hydrate": "probablePitcher,team"},
        http_get=http_get,
    )
    found: list[tuple[datetime, int]] = []
    for block in payload.get("dates") or []:
        for game in (block or {}).get("games") or []:
            teams = game.get("teams") or {}
            for side in ("home", "away"):
                probable = ((teams.get(side) or {}).get("probablePitcher") or {})
                if _int(probable.get("id")) == player_id:
                    try:
                        dt = _aware(str(game.get("gameDate")))
                    except Exception:
                        continue
                    if dt < event_start and _int(game.get("gamePk")) > 0:
                        found.append((dt, _int(game.get("gamePk"))))
    found.sort(reverse=True)
    return [pk for _, pk in found[:MAX_PRIOR_STARTS]]


def _first_inning_pitch_counts(game_pk: int, pitcher_id: int, http_get: Callable[..., Any]) -> tuple[int, list[int]]:
    payload = _request_json(
        f"{MLB_STATS_API_BASE}/game/{game_pk}/playByPlay",
        params={},
        http_get=http_get,
    )
    pa_pitch_counts: list[int] = []
    for play in payload.get("allPlays") or []:
        about = play.get("about") or {}
        matchup = play.get("matchup") or {}
        if _int(about.get("inning")) != 1 or _int(((matchup.get("pitcher") or {}).get("id"))) != pitcher_id:
            continue
        events = play.get("playEvents") or []
        pitches = sum(1 for ev in events if isinstance(ev, dict) and ev.get("isPitch") is True)
        if pitches > 0:
            pa_pitch_counts.append(pitches)
    return len(pa_pitch_counts), pa_pitch_counts


def _official_or_recent_lineup(game_pk: int, opponent_side: str, http_get: Callable[..., Any]) -> tuple[str, list[int]]:
    payload = _request_json(
        f"{MLB_STATS_API_BASE}/game/{game_pk}/boxscore",
        params={},
        http_get=http_get,
    )
    teams = payload.get("teams") or {}
    side_key = "away" if opponent_side == "AWAY" else "home"
    team = teams.get(side_key) or {}
    order = team.get("battingOrder") or []
    ids = [_int(v) for v in order if _int(v) > 0]
    return ("CONFIRMED" if len(ids) >= 9 else "TBD"), ids[:4]


def _recent_lineup_projection(team_id: int, event_start: datetime, http_get: Callable[..., Any]) -> list[int]:
    payload = _request_json(
        f"{MLB_STATS_API_BASE}/schedule",
        params={"sportId": "1", "teamId": team_id, "startDate": (event_start.date()-timedelta(days=10)).isoformat(), "endDate": (event_start.date()-timedelta(days=1)).isoformat()},
        http_get=http_get,
    )
    games: list[tuple[datetime,int,str]] = []
    for block in payload.get("dates") or []:
        for game in (block or {}).get("games") or []:
            pk = _int(game.get("gamePk"))
            if pk <= 0:
                continue
            teams = game.get("teams") or {}
            home_id = _int((((teams.get("home") or {}).get("team") or {}).get("id")))
            side = "HOME" if home_id == team_id else "AWAY"
            try:
                dt = _aware(str(game.get("gameDate")))
            except Exception:
                continue
            games.append((dt, pk, side))
    games.sort(reverse=True)
    for _, pk, side in games:
        status, order = _official_or_recent_lineup(pk, side, http_get)
        if len(order) >= 3:
            return order[:4]
    return []


def _person_profile(player_id: int, http_get: Callable[..., Any]) -> dict[str, Any]:
    person = _request_json(f"{MLB_STATS_API_BASE}/people/{player_id}", params={"hydrate": "stats(group=[hitting],type=[season])"}, http_get=http_get)
    people = person.get("people") or []
    if not people:
        return {}
    p = people[0]
    bat_side = ((p.get("batSide") or {}).get("code") or "U")
    pitches = None
    pa = None
    for block in p.get("stats") or []:
        for split in block.get("splits") or []:
            stat = split.get("stat") or {}
            if stat.get("plateAppearances") is not None:
                pa = _int(stat.get("plateAppearances"))
                pitches = _int(stat.get("numberOfPitches"))
    ppa = (float(pitches) / pa) if pa and pitches and pa > 0 else 4.0
    return {"player": p.get("fullName") or str(player_id), "player_id": player_id, "handedness": bat_side, "p_pa_vs_pitcher_profile": round(ppa, 4)}


def hydrate_mlb_1ip_evidence(*, player: str, event_start_time: str, http_get: Callable[..., Any] = httpx.get, now: datetime | None = None) -> dict[str, Any]:
    captured = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    event_start = _aware(event_start_time)
    if event_start <= captured:
        raise PropAutoHydrationError("EVENT_ALREADY_STARTED", "1IP hydration is pregame only")

    pitcher_id, official_name = _resolve_player_id(player, http_get=http_get)
    sched = _schedule_context(pitcher_id, event_start=event_start, http_get=http_get)
    current_game_pk = _int(sched.get("official_game_pk"))
    pitcher_side = str(sched.get("side") or "").upper()
    opponent_side = "AWAY" if pitcher_side == "HOME" else "HOME"

    # current game teams
    current = _request_json(f"{MLB_STATS_API_BASE}/schedule", params={"sportId":"1", "gamePk": current_game_pk, "hydrate":"team,probablePitcher"}, http_get=http_get)
    game = (((current.get("dates") or [{}])[0].get("games") or [{}])[0])
    teams = game.get("teams") or {}
    opp_node = teams.get("away" if opponent_side == "AWAY" else "home") or {}
    opponent_team_id = _int(((opp_node.get("team") or {}).get("id")))

    lineup_status, top_ids = _official_or_recent_lineup(current_game_pk, opponent_side, http_get)
    if len(top_ids) < 3 and opponent_team_id > 0:
        top_ids = _recent_lineup_projection(opponent_team_id, event_start, http_get)
        lineup_status = "TBD"
    projected_top_four = [_person_profile(pid, http_get) for pid in top_ids[:4]]
    projected_top_four = [x for x in projected_top_four if x]

    bfs: list[int] = []
    pitch_counts: list[int] = []
    for pk in _schedule_games_for_pitcher(pitcher_id, event_start, http_get):
        bf, ppb = _first_inning_pitch_counts(pk, pitcher_id, http_get)
        if bf >= 3:
            bfs.append(bf)
            pitch_counts.extend(ppb)
    if len(bfs) < MIN_PRIOR_STARTS or not pitch_counts:
        raise PropAutoHydrationError("MLB_1IP_PRIOR_SAMPLE_INSUFFICIENT", "insufficient official first-inning play-by-play", detail={"starts": len(bfs)})

    n = len(bfs)
    p3 = sum(1 for x in bfs if x == 3) / n
    p4 = sum(1 for x in bfs if x == 4) / n
    p5 = sum(1 for x in bfs if x >= 5) / n
    ppb_mean = mean(pitch_counts)
    ppb_std = pstdev(pitch_counts) if len(pitch_counts) > 1 else 1.1
    ts = captured.isoformat()
    return {
        "provider": PROVIDER,
        "captured_at": ts,
        "starter_name": official_name,
        "starter_name_at_capture": official_name,
        "starter_status": "CONFIRMED",
        "official_lineup_status": lineup_status,
        "projected_top_four": projected_top_four,
        "pitcher_bf_distribution": {"p_bf_3": p3, "p_bf_4": p4, "p_bf_gte5": p5, "sample_n": n},
        "baseline_pitches_per_batter": {"mean": round(ppb_mean, 4), "std": round(max(ppb_std, 0.25), 4), "sample_n": len(pitch_counts)},
        "failure_path_prior": {"status": "RESOLVED_FROM_OFFICIAL_PRIOR_STARTS", "sample_n": n},
        "source_timestamps": {"MLB_STATS_API_1IP_PLAYBYPLAY": ts, "MLB_STATS_API_LINEUP": ts, "MLB_STATS_API_PROBABLE_PITCHER": ts},
        "final_refresh_required": lineup_status != "CONFIRMED",
        "probability_publishable": False,
        "can_execute": False,
    }
