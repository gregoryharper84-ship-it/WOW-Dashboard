"""
WOW-PATCH-2026-08-16-MONEYLINE-TEAM-ACQUISITION

Acquires auditable non-market team probability components for supported
MONEYLINE_V1 sports before the row reaches the independence gate.

Supported sports
----------------
NBA  — BallDontLie /v1/standings (season win-percentage)
MLB  — MLB Stats API /api/v1/standings (public, no key required)

Unsupported sports
------------------
All other sports return None immediately; the caller surfaces a precise
MONEYLINE_ACQUISITION_UNAVAILABLE:sport_not_supported reason.

Output fields (populated in enrichment dict)
--------------------------------------------
home_win_pct    float  Season win-percentage for the home team (0.0–1.0)
away_win_pct    float  Season win-percentage for the away team (0.0–1.0)
home_power      float  = home_win_pct (sport_model.py power-rating alias)
away_power      float  = away_win_pct
team_acq_source str    Data source identifier for audit trail

These map directly to the inputs consumed by
gate_engine.moneyline.sport_model.compute_independent_probability so the
ensemble model can compute a non-market probability without sportsbook odds.

Invariants
----------
- can_execute = False
- Never derives probability from sportsbook odds
- Fail-closed: exceptions return None; caller emits explicit blocker
- Timeout: 5 seconds per HTTP call; returns None on timeout
"""
from __future__ import annotations

import logging
import urllib.error
import urllib.parse
import urllib.request
import json
from typing import Any

logger = logging.getLogger(__name__)

can_execute = False
PATCH_ID    = "WOW-PATCH-2026-08-16-MONEYLINE-TEAM-ACQUISITION"

_HTTP_TIMEOUT = 5  # seconds

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def acquire_team_data(
    row: dict[str, Any],
    sport: str,
) -> dict[str, float | str] | None:
    """
    Fetch non-market team data for a moneyline row.

    Returns a dict with home_win_pct, away_win_pct, home_power, away_power,
    team_acq_source, or None when acquisition is unavailable.
    """
    sport = sport.upper()
    team     = (row.get("team") or row.get("player") or "").strip()
    opponent = (row.get("opponent") or "").strip()

    if not team or not opponent:
        logger.debug("team_acquisition: team or opponent missing — skipping")
        return None

    if sport == "NBA":
        return _acquire_nba(team, opponent)
    if sport == "MLB":
        return _acquire_mlb(team, opponent)

    # Unsupported sport — caller handles
    return None


# ---------------------------------------------------------------------------
# NBA — BallDontLie /v1/standings
# ---------------------------------------------------------------------------

def _acquire_nba(team: str, opponent: str) -> dict | None:
    try:
        from gate_engine.balldontlie.client import fetch_all as _bdl_fetch_all
        resp = _bdl_fetch_all(
            "https://api.balldontlie.io/v1/standings",
            params={"season": _current_nba_season()},
            max_pages=1,
        )
        if not resp.ok or not resp.data:
            return None

        standings: dict[str, float] = {}  # abbr/name → win_pct
        for entry in resp.data:
            t = entry.get("team") or {}
            name  = (t.get("full_name") or t.get("name") or "").lower()
            abbr  = (t.get("abbreviation") or "").upper()
            wins  = int(entry.get("wins") or 0)
            losses = int(entry.get("losses") or 0)
            total = wins + losses
            if total == 0:
                continue
            wp = wins / total
            if name:
                standings[name] = wp
            if abbr:
                standings[abbr] = wp

        home_wp = _fuzzy_lookup(team, standings)
        away_wp = _fuzzy_lookup(opponent, standings)

        if home_wp is None or away_wp is None:
            logger.debug(
                "nba_team_acq: no standings match for team=%r opp=%r", team, opponent
            )
            return None

        return {
            "home_win_pct":    home_wp,
            "away_win_pct":    away_wp,
            "home_power":      home_wp,
            "away_power":      away_wp,
            "team_acq_source": "balldontlie_nba_standings",
        }
    except Exception as exc:
        logger.warning("NBA team acquisition failed: %s", exc)
        return None


def _current_nba_season() -> int:
    """Return start year of current/most recent NBA season."""
    import datetime
    today = datetime.date.today()
    # NBA season starts in October; season year = calendar year of season start
    return today.year if today.month >= 10 else today.year - 1


# ---------------------------------------------------------------------------
# MLB — MLB Stats API (public, no key)
# ---------------------------------------------------------------------------

_MLB_STANDINGS_URL = (
    "https://statsapi.mlb.com/api/v1/standings"
    "?leagueId=103,104&hydrate=team&season={season}&standingsTypes=regularSeason"
)


def _acquire_mlb(team: str, opponent: str) -> dict | None:
    try:
        import datetime
        season = datetime.date.today().year
        url    = _MLB_STANDINGS_URL.format(season=season)
        data   = _http_get_json(url)
        if not data:
            return None

        standings: dict[str, float] = {}
        for record in data.get("records") or []:
            for entry in record.get("teamRecords") or []:
                t    = entry.get("team") or {}
                name = (t.get("name") or "").lower()
                abbr = (t.get("abbreviationOrCode") or t.get("abbreviation") or "").upper()
                wins   = int((entry.get("wins") or 0))
                losses = int((entry.get("losses") or 0))
                total  = wins + losses
                if total == 0:
                    continue
                wp = wins / total
                if name:
                    standings[name] = wp
                if abbr:
                    standings[abbr] = wp

        home_wp = _fuzzy_lookup(team, standings)
        away_wp = _fuzzy_lookup(opponent, standings)

        if home_wp is None or away_wp is None:
            logger.debug(
                "mlb_team_acq: no standings match for team=%r opp=%r", team, opponent
            )
            return None

        return {
            "home_win_pct":    home_wp,
            "away_win_pct":    away_wp,
            "home_power":      home_wp,
            "away_power":      away_wp,
            "team_acq_source": "mlb_stats_api_standings",
        }
    except Exception as exc:
        logger.warning("MLB team acquisition failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fuzzy_lookup(query: str, standings: dict[str, float]) -> float | None:
    """
    Look up team win-pct by exact abbreviation or substring name match.
    Returns None when no match found.
    """
    q = query.strip().lower()
    if not q:
        return None
    # Exact key match (case-insensitive)
    if q in standings:
        return standings[q]
    q_upper = q.upper()
    if q_upper in standings:
        return standings[q_upper]
    # Abbreviation match: last 3 uppercase chars of query
    abbr = q_upper[-3:] if len(q_upper) >= 3 else q_upper
    if abbr in standings:
        return standings[abbr]
    # Substring: query contains a known name key
    for key, wp in standings.items():
        if key in q or q in key:
            return wp
    return None


def _http_get_json(url: str) -> dict | None:
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "WOW-ScoringEngine/1.0"}, method="GET"
        )
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.debug("_http_get_json %s failed: %s", url, exc)
        return None
