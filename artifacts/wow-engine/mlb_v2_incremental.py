from __future__ import annotations

"""Transactional strict-pregame catch-up for the MLB V2 state artifact.

The bundled state is immutable evidence at deploy time. If the Render process
survives into a later scoring date (or restarts with an older bundled cutoff),
this module can advance the in-memory state using *official final MLB results
strictly before the target date*.

Safety properties:
- never consumes target-date results;
- fetches only regular-season games;
- de-duplicates schedule repeats by gamePk;
- explicit postponed/cancelled games contribute no result;
- any live/preview/delayed/suspended prior-day game blocks the entire refresh;
- any missing/invalid boxscore blocks the entire refresh;
- same-date Elo changes are calculated from the pre-date ratings and applied
  only after every game on that date, matching the validated training builder;
- the caller's state is mutated only after the complete multi-day refresh passes.

This is a publication-availability mechanism only. It never authorizes wagering
or execution.
"""

import copy
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Any

import requests

can_execute: bool = False
can_approve_bets: bool = False

_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
_BOXSCORE_URL = "https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
_USER_AGENT = "WOW-MLB-V2-Incremental/20260827"
_MAX_CATCHUP_DAYS = 75
_ELO_K = 20.0
_ELO_HOME_ADVANTAGE = 35.0

MLB_ID_TO_RETRO = {
    108: "LAA", 109: "ARI", 110: "BAL", 111: "BOS", 112: "CHN",
    113: "CIN", 114: "CLE", 115: "COL", 116: "DET", 117: "HOU",
    118: "KCA", 119: "LAN", 120: "WAS", 121: "NYN", 133: "OAK",
    134: "PIT", 135: "SDN", 136: "SEA", 137: "SFN", 138: "SLN",
    139: "TBA", 140: "TEX", 141: "TOR", 142: "MIN", 143: "PHI",
    144: "ATL", 145: "CHA", 146: "MIA", 147: "NYA", 158: "MIL",
}


def _stat_int(d: dict[str, Any], *keys: str) -> int:
    for key in keys:
        if key in d and d[key] not in (None, ""):
            try:
                return int(float(d[key]))
            except (TypeError, ValueError):
                continue
    return 0


def _innings_to_outs(value: Any) -> int:
    if value in (None, ""):
        return 0
    text = str(value)
    if "." not in text:
        try:
            return int(float(text) * 3)
        except (TypeError, ValueError):
            return 0
    whole, frac = text.split(".", 1)
    try:
        return 3 * int(whole) + int(frac[:1] or "0")
    except (TypeError, ValueError):
        return 0


def _elo_expected(home_elo: float, away_elo: float) -> float:
    return 1.0 / (
        1.0
        + 10.0
        ** (-((float(home_elo) + _ELO_HOME_ADVANTAGE) - float(away_elo)) / 400.0)
    )


def _schedule_day(day: date) -> list[dict[str, Any]]:
    response = requests.get(
        _SCHEDULE_URL,
        params={"sportId": 1, "gameTypes": "R", "date": day.isoformat()},
        timeout=15,
        headers={"User-Agent": _USER_AGENT},
    )
    response.raise_for_status()
    payload = response.json()
    by_game_pk: dict[int, dict[str, Any]] = {}
    for date_row in payload.get("dates", []):
        for game in date_row.get("games", []):
            if game.get("gameType") != "R":
                continue
            game_pk = int(game.get("gamePk") or 0)
            if not game_pk:
                raise RuntimeError(f"MLB_V2_INCREMENTAL_GAMEPK_MISSING:{day.isoformat()}")
            home = (game.get("teams") or {}).get("home") or {}
            away = (game.get("teams") or {}).get("away") or {}
            home_id = int((home.get("team") or {}).get("id") or 0)
            away_id = int((away.get("team") or {}).get("id") or 0)
            home_code = MLB_ID_TO_RETRO.get(home_id)
            away_code = MLB_ID_TO_RETRO.get(away_id)
            if not home_code or not away_code:
                raise RuntimeError(
                    f"MLB_V2_INCREMENTAL_TEAM_MAPPING_MISSING:{game_pk}:{home_id}:{away_id}"
                )
            status = game.get("status") or {}
            by_game_pk[game_pk] = {
                "gamePk": game_pk,
                "game_date": str(game.get("officialDate") or day.isoformat()),
                "game_number": int(game.get("gameNumber") or 1),
                "abstract_state": str(status.get("abstractGameState") or ""),
                "detailed_state": str(status.get("detailedState") or ""),
                "home_team": home_code,
                "away_team": away_code,
                "home_score": home.get("score"),
                "away_score": away.get("score"),
            }
    return [by_game_pk[k] for k in sorted(by_game_pk)]


def _validate_day_terminal(day: date, games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    finals: list[dict[str, Any]] = []
    for game in games:
        abstract = (game.get("abstract_state") or "").strip().casefold()
        detailed = (game.get("detailed_state") or "").strip().casefold()
        if abstract == "final":
            finals.append(game)
            continue
        if detailed in {"postponed", "cancelled", "canceled"}:
            # No outcome exists for this calendar date. The rescheduled game will
            # appear under its playable date/gamePk and will be consumed then.
            continue
        raise RuntimeError(
            "MLB_V2_INCREMENTAL_PRIOR_DAY_NOT_TERMINAL:"
            f"date={day.isoformat()}:gamePk={game.get('gamePk')}:"
            f"abstract={game.get('abstract_state')}:detailed={game.get('detailed_state')}"
        )
    return finals


def _side_shell(game: dict[str, Any], *, is_home: bool) -> dict[str, Any]:
    team = game["home_team"] if is_home else game["away_team"]
    opponent = game["away_team"] if is_home else game["home_team"]
    return {
        "team": team,
        "opponent": opponent,
        "is_home": bool(is_home),
        "runs": int(game["home_score"] if is_home else game["away_score"]),
        "hits": 0,
        "hr": 0,
        "bb": 0,
        "so": 0,
        "tb": 0,
        "bp_out": 0,
        "bp_er": 0,
        "bp_so": 0,
        "bp_bb": 0,
        "bp_pitch": 0,
        "bp_relief_appearances": 0,
        "starter_id": None,
        "starter_out": 0,
        "starter_er": 0,
        "starter_so": 0,
        "starter_bb": 0,
        "starter_hr": 0,
        "starter_h": 0,
        "starter_pitch": 0,
        "starter_tbf": 0,
    }


def _parse_box_side(box_side: dict[str, Any], shell: dict[str, Any]) -> dict[str, Any]:
    batting = (box_side.get("teamStats") or {}).get("batting") or {}
    shell["hits"] = _stat_int(batting, "hits")
    shell["hr"] = _stat_int(batting, "homeRuns")
    shell["bb"] = _stat_int(batting, "baseOnBalls")
    shell["so"] = _stat_int(batting, "strikeOuts")
    total_bases = _stat_int(batting, "totalBases")
    if total_bases <= 0 and shell["hits"] > 0:
        doubles = _stat_int(batting, "doubles")
        triples = _stat_int(batting, "triples")
        total_bases = shell["hits"] + doubles + 2 * triples + 3 * shell["hr"]
    shell["tb"] = total_bases

    pitcher_ids = [int(pid) for pid in (box_side.get("pitchers") or [])]
    if not pitcher_ids:
        raise RuntimeError(f"MLB_V2_INCREMENTAL_PITCHERS_MISSING:{shell['team']}")
    players = box_side.get("players") or {}
    for idx, pitcher_id in enumerate(pitcher_ids):
        player = players.get(f"ID{pitcher_id}") or {}
        pitching = ((player.get("stats") or {}).get("pitching") or {})
        outs = _stat_int(pitching, "outs") or _innings_to_outs(pitching.get("inningsPitched"))
        earned_runs = _stat_int(pitching, "earnedRuns")
        strikeouts = _stat_int(pitching, "strikeOuts")
        walks = _stat_int(pitching, "baseOnBalls")
        homers = _stat_int(pitching, "homeRuns")
        hits = _stat_int(pitching, "hits")
        pitches = _stat_int(pitching, "numberOfPitches", "pitchesThrown")
        batters_faced = _stat_int(pitching, "battersFaced")
        if idx == 0:
            shell["starter_id"] = f"mlbam:{pitcher_id}"
            shell["starter_out"] = outs
            shell["starter_er"] = earned_runs
            shell["starter_so"] = strikeouts
            shell["starter_bb"] = walks
            shell["starter_hr"] = homers
            shell["starter_h"] = hits
            shell["starter_pitch"] = pitches
            shell["starter_tbf"] = batters_faced
        else:
            shell["bp_out"] += outs
            shell["bp_er"] += earned_runs
            shell["bp_so"] += strikeouts
            shell["bp_bb"] += walks
            shell["bp_pitch"] += pitches
            shell["bp_relief_appearances"] += 1
    return shell


def _fetch_game_rows(game: dict[str, Any]) -> dict[str, Any]:
    try:
        home_score = int(game["home_score"])
        away_score = int(game["away_score"])
    except (TypeError, ValueError, KeyError) as exc:
        raise RuntimeError(
            f"MLB_V2_INCREMENTAL_FINAL_SCORE_MISSING:{game.get('gamePk')}"
        ) from exc
    if home_score == away_score:
        raise RuntimeError(f"MLB_V2_INCREMENTAL_FINAL_TIE_INVALID:{game.get('gamePk')}")

    response = requests.get(
        _BOXSCORE_URL.format(game_pk=game["gamePk"]),
        timeout=15,
        headers={"User-Agent": _USER_AGENT},
    )
    response.raise_for_status()
    payload = response.json()
    teams = payload.get("teams") or {}
    if "home" not in teams or "away" not in teams:
        raise RuntimeError(f"MLB_V2_INCREMENTAL_BOXSCORE_SIDES_MISSING:{game['gamePk']}")
    home = _parse_box_side(teams["home"], _side_shell(game, is_home=True))
    away = _parse_box_side(teams["away"], _side_shell(game, is_home=False))
    return {
        "gamePk": int(game["gamePk"]),
        "game_date": str(game["game_date"]),
        "game_number": int(game.get("game_number") or 1),
        "home": home,
        "away": away,
        "y": int(home_score > away_score),
    }


def _fetch_day_rows(final_games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not final_games:
        return []
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=min(12, len(final_games))) as executor:
        futures = {executor.submit(_fetch_game_rows, game): game for game in final_games}
        for future in as_completed(futures):
            game = futures[future]
            try:
                rows.append(future.result())
            except Exception as exc:
                failures.append(f"gamePk={game.get('gamePk')}:{type(exc).__name__}:{exc}")
    if failures:
        raise RuntimeError("MLB_V2_INCREMENTAL_BOXSCORE_FAILURE:" + "|".join(sorted(failures)))
    return sorted(rows, key=lambda row: (row["game_date"], row["gamePk"]))


def _apply_day(clone: dict[str, Any], day: date, games: list[dict[str, Any]]) -> int:
    team_hist = clone.setdefault("team_hist", {})
    pitcher_hist = clone.setdefault("pitcher_hist", {})
    elo = clone.setdefault("elo", {})

    # Match the validated research builder: every game on a date reads the same
    # pre-date Elo state. Deltas are accumulated, then applied after the date.
    pre_day_elo = {team: float(value) for team, value in elo.items()}
    deltas: dict[str, float] = defaultdict(float)
    for game in games:
        home = game["home"]
        away = game["away"]
        home_elo = float(pre_day_elo.get(home["team"], 1500.0))
        away_elo = float(pre_day_elo.get(away["team"], 1500.0))
        expected = _elo_expected(home_elo, away_elo)
        delta = _ELO_K * (float(game["y"]) - expected)
        deltas[home["team"]] += delta
        deltas[away["team"]] -= delta

    for game in games:
        for side, opponent in ((game["home"], game["away"]), (game["away"], game["home"])):
            team_hist.setdefault(side["team"], []).append(
                {
                    "date": day,
                    "is_home": bool(side["is_home"]),
                    "win": float(side["runs"] > opponent["runs"]),
                    "runs": float(side["runs"]),
                    "runs_allowed": float(opponent["runs"]),
                    "run_diff": float(side["runs"] - opponent["runs"]),
                    "hits": float(side["hits"]),
                    "hr": float(side["hr"]),
                    "bb": float(side["bb"]),
                    "so": float(side["so"]),
                    "tb": float(side["tb"]),
                    "bp_out": float(side["bp_out"]),
                    "bp_er": float(side["bp_er"]),
                    "bp_so": float(side["bp_so"]),
                    "bp_bb": float(side["bp_bb"]),
                    "bp_pitch": float(side["bp_pitch"]),
                    "bp_relief_appearances": float(side["bp_relief_appearances"]),
                }
            )
            starter_id = side.get("starter_id")
            if starter_id:
                pitcher_hist.setdefault(starter_id, []).append(
                    {
                        "date": day,
                        "out": float(side["starter_out"]),
                        "er": float(side["starter_er"]),
                        "so": float(side["starter_so"]),
                        "bb": float(side["starter_bb"]),
                        "hr": float(side["starter_hr"]),
                        "h": float(side["starter_h"]),
                        "pitch": float(side["starter_pitch"]),
                        "tbf": float(side["starter_tbf"]),
                    }
                )

    for team, delta in deltas.items():
        elo[team] = float(pre_day_elo.get(team, 1500.0)) + float(delta)
    return len(games)


def advance_state_to_target(
    state: dict[str, Any],
    target: date,
    *,
    max_catchup_days: int = _MAX_CATCHUP_DAYS,
) -> dict[str, Any]:
    """Advance ``state`` to an exclusive cutoff equal to ``target``.

    The original object is untouched unless every missing date validates and all
    required official boxscores parse successfully.
    """
    if not bool(state.get("strict_prior_date_only")):
        raise RuntimeError("MLB_V2_INCREMENTAL_LEAKAGE_ATTESTATION_MISSING")
    try:
        cutoff = date.fromisoformat(str(state.get("cutoff_exclusive") or ""))
    except ValueError as exc:
        raise RuntimeError("MLB_V2_INCREMENTAL_CUTOFF_INVALID") from exc
    if cutoff > target:
        raise RuntimeError(
            f"MLB_V2_INCREMENTAL_STATE_FROM_FUTURE:cutoff={cutoff}:target={target}"
        )
    gap_days = (target - cutoff).days
    if gap_days == 0:
        return {
            "status": "NOT_NEEDED",
            "from_cutoff": cutoff.isoformat(),
            "to_cutoff": target.isoformat(),
            "days_advanced": 0,
            "games_added": 0,
            "source": "BUNDLED_OR_ALREADY_ADVANCED_STATE",
        }
    if gap_days > int(max_catchup_days):
        raise RuntimeError(
            f"MLB_V2_INCREMENTAL_GAP_TOO_LARGE:{gap_days}>{int(max_catchup_days)}"
        )

    clone = copy.deepcopy(state)
    total_games = 0
    skipped_no_result = 0
    day_cursor = cutoff
    while day_cursor < target:
        scheduled = _schedule_day(day_cursor)
        finals = _validate_day_terminal(day_cursor, scheduled)
        skipped_no_result += len(scheduled) - len(finals)
        parsed = _fetch_day_rows(finals)
        total_games += _apply_day(clone, day_cursor, parsed)
        if parsed:
            clone["results_through"] = day_cursor.isoformat()
        day_cursor += timedelta(days=1)

    clone["cutoff_exclusive"] = target.isoformat()
    clone["strict_prior_date_only"] = True
    clone["incremental_refresh"] = {
        "status": "PASS",
        "from_cutoff": cutoff.isoformat(),
        "to_cutoff": target.isoformat(),
        "days_advanced": gap_days,
        "games_added": total_games,
        "explicit_no_result_games_skipped": skipped_no_result,
        "source": "OFFICIAL_MLB_STATS_API_FINAL_BOXSCORES",
        "same_day_results_used": False,
        "can_execute": False,
    }

    # Atomic publication of the refreshed in-memory state.
    state.clear()
    state.update(clone)
    return dict(clone["incremental_refresh"])
