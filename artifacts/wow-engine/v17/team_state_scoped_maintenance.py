"""Bounded per-sport maintenance for LLP dynamic team-state challengers.

The aggregate team-state maintenance route is useful for local/replay work, but
some source-backed lanes can exceed a single HTTP request budget in production.
This module exposes the same governed candidate training one scope at a time so
the protected workflow can advance independent lanes without one slow provider
blocking every other sport.

This is candidate maintenance only. It never certifies, promotes, publishes, or
executes a wager.
"""
from __future__ import annotations

from typing import Any, Callable

from v17.soccer_openfootball_candidate import COMPETITIONS
from v17.team_state_challenger_maintenance import (
    PROGRAM,
    TeamStateMaintenanceUnavailable,
    _basketball_events,
    _mlb_official_events,
    _ncaab_events,
    _ncaaf_events,
    _nfl_events,
    _paginate,
    _soccer_events,
)
from v17.team_state_challenger_training import (
    train_binary_challenger,
    train_multiclass_challenger,
)

CAN_EXECUTE = False


def supported_scopes() -> tuple[str, ...]:
    return (
        "NFL",
        "MLB",
        "NBA",
        "WNBA",
        "NCAAF",
        "NCAAB",
        *(f"SOCCER_{competition}" for competition in COMPETITIONS),
    )


def _mlb_persisted_events(client: Any) -> list[dict[str, Any]]:
    """Load settled regular-season MLB history already persisted by WOW.

    The multiseason table is pair-shaped (one row per team/game).  Reusing it
    removes repeated full-season StatsAPI downloads from routine challenger
    maintenance while preserving a traceable historical source manifest.
    """
    query = (
        client.table("wow_mlb_team_games_multiseason")
        .select(
            "game_key,game_date,site_key,team_alignment,team_key,opponent_key,"
            "team_runs,team_runs_allowed,season_year,season_phase"
        )
        .eq("season_phase", "R")
        .order("game_date")
        .order("game_key")
        .order("team_alignment")
    )
    rows = _paginate(query)
    by_game: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        game_key = str(row.get("game_key") or "").strip()
        if not game_key:
            continue
        by_game.setdefault(game_key, []).append(row)

    events: list[dict[str, Any]] = []
    for game_key, game_rows in by_game.items():
        home = next((row for row in game_rows if int(row.get("team_alignment") or 0) == 1), None)
        away = next((row for row in game_rows if int(row.get("team_alignment") or 0) == 0), None)
        if not home or not away:
            continue
        if home.get("team_runs") is None or away.get("team_runs") is None:
            continue
        home_team = str(home.get("team_key") or "").strip()
        away_team = str(away.get("team_key") or "").strip()
        game_date = str(home.get("game_date") or away.get("game_date") or "").strip()
        if not home_team or not away_team or not game_date:
            continue
        season = int(home.get("season_year") or away.get("season_year") or 0)
        events.append(
            {
                "event_id": f"MLB:HIST:{game_key}",
                "event_start_time": f"{game_date}T12:00:00+00:00",
                "season": season,
                "home_team": home_team,
                "away_team": away_team,
                "home_score": float(home["team_runs"]),
                "away_score": float(away["team_runs"]),
                "source_manifest": {
                    "source": "WOW_MLB_TEAM_GAMES_MULTI_SEASON",
                    "game_key": game_key,
                    "season": season,
                    "site_key": home.get("site_key") or away.get("site_key"),
                    "historical_reconstruction": True,
                },
            }
        )
    if not events:
        raise TeamStateMaintenanceUnavailable(
            "MLB_PERSISTED_HISTORY_EMPTY", "no settled regular-season MLB history"
        )
    return sorted(events, key=lambda row: (str(row["event_start_time"]), str(row["event_id"])))


def _mlb_team_state_events(client: Any, *, current_season: int = 2026) -> list[dict[str, Any]]:
    """Combine durable history with one current-season authoritative refresh."""
    historical = _mlb_persisted_events(client)
    current = _mlb_official_events(seasons=(int(current_season),))
    combined: dict[str, dict[str, Any]] = {str(row["event_id"]): row for row in historical}
    for row in current:
        combined[str(row["event_id"])] = row
    return sorted(combined.values(), key=lambda row: (str(row["event_start_time"]), str(row["event_id"])))


def _job(client: Any, scope: str, code: str) -> Callable[[], dict[str, Any]] | None:
    scope = str(scope or "").strip().upper()
    if scope == "NFL":
        return lambda: train_binary_challenger(
            client, sport="NFL", league="NFL", events=_nfl_events(client),
            expected_season_games=17, training_code_sha=code, min_rows=300,
        )
    if scope == "MLB":
        return lambda: train_binary_challenger(
            client, sport="MLB", league="MLB", events=_mlb_team_state_events(client),
            expected_season_games=162, training_code_sha=code, min_rows=500,
        )
    if scope == "NBA":
        return lambda: train_binary_challenger(
            client, sport="NBA", league="NBA", events=_basketball_events(client, "NBA"),
            expected_season_games=82, training_code_sha=code, min_rows=300,
        )
    if scope == "WNBA":
        return lambda: train_binary_challenger(
            client, sport="WNBA", league="WNBA", events=_basketball_events(client, "WNBA"),
            expected_season_games=44, training_code_sha=code, min_rows=250,
        )
    if scope == "NCAAF":
        return lambda: train_binary_challenger(
            client, sport="NCAAF", league="NCAAF", events=_ncaaf_events(client),
            expected_season_games=13, training_code_sha=code, min_rows=300,
        )
    if scope == "NCAAB":
        return lambda: train_binary_challenger(
            client, sport="NCAAB", league="NCAAB", events=_ncaab_events(),
            expected_season_games=31, training_code_sha=code, min_rows=500,
        )
    if scope.startswith("SOCCER_"):
        competition = scope.removeprefix("SOCCER_")
        provider_code = COMPETITIONS.get(competition)
        if provider_code is not None:
            return lambda: train_multiclass_challenger(
                client, sport="SOCCER", league=competition,
                events=_soccer_events(competition, provider_code),
                expected_season_games=38, training_code_sha=code, min_rows=500,
            )
    return None


def _blocked(scope: str, code: str, *, error_type: str | None = None, message: str | None = None) -> dict[str, Any]:
    detail: dict[str, Any] = {}
    if error_type:
        detail["error_type"] = error_type
    if message:
        detail["message"] = message[:600]
    return {
        "sport": scope,
        "status": "BLOCKED",
        "code": code,
        "detail": detail,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def run_team_state_scope(
    client: Any,
    *,
    scope: str,
    training_code_sha: str,
) -> dict[str, Any]:
    normalized = str(scope or "").strip().upper()
    code = str(training_code_sha or "").strip().lower()
    if len(code) < 7:
        row = _blocked(normalized or "UNKNOWN", "TEAM_STATE_TRAINING_CODE_SHA_UNAVAILABLE")
        return {
            "status": "BLOCKED",
            "program": PROGRAM,
            "scope": normalized,
            "candidate_rows_updated": 0,
            "candidate_rows_blocked": 1,
            "rows": [row],
            "automatic_certification": False,
            "automatic_promotion": False,
            "probability_publishable": False,
            "can_execute": False,
        }

    fn = _job(client, normalized, code)
    if fn is None:
        row = _blocked(normalized or "UNKNOWN", "TEAM_STATE_SCOPE_UNSUPPORTED")
        return {
            "status": "BLOCKED",
            "program": PROGRAM,
            "scope": normalized,
            "supported_scopes": list(supported_scopes()),
            "candidate_rows_updated": 0,
            "candidate_rows_blocked": 1,
            "rows": [row],
            "automatic_certification": False,
            "automatic_promotion": False,
            "probability_publishable": False,
            "can_execute": False,
        }

    try:
        row = dict(fn())
        row["status"] = "CANDIDATE_EVIDENCE_UPDATED"
        row["can_execute"] = False
        row["probability_publishable"] = False
        row["automatic_certification"] = False
        row["automatic_promotion"] = False
        updated = 1
    except Exception as exc:  # noqa: BLE001 - per-lane typed maintenance boundary
        row = _blocked(
            normalized,
            str(getattr(exc, "code", None) or f"{normalized}_TEAM_STATE_MAINTENANCE_FAILED"),
            error_type=type(exc).__name__,
            message=str(exc),
        )
        updated = 0

    return {
        "status": "COMPLETED_WITH_EVIDENCE" if updated else "COMPLETED_NO_CANDIDATE_SURVIVORS",
        "program": PROGRAM,
        "scope": normalized,
        "candidate_rows_updated": updated,
        "candidate_rows_blocked": 1 - updated,
        "rows": [row],
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "run_team_state_scope",
    "supported_scopes",
]
