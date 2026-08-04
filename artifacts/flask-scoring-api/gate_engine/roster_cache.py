"""
roster_cache.py — WOW Slip Normalization: Roster & Schedule Cache

Fetches and daily-caches per-sport rosters and today's schedule.

Each record includes BOTH field-name conventions for compatibility:
  - name / name_raw         — full name as returned by source
  - name_normalized / name_norm — normalized (diacritics stripped, lowercased)
  - team / team_abbr        — team abbreviation (e.g. "LAL", "NYY")
  - player_id, position, sport, source

Data sources:
  NBA          — ESPN site API (team rosters with abbreviation); nba_api fallback
  WNBA         — BallDontLie (if key set) → ESPN → nba_api
  MLB          — statsapi.mlb.com (official, free, no auth)
  NFL / NHL    — ESPN site API

Cache key = (sport, date_str). TTL = 24 h per worker process.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
from datetime import date, datetime, timezone
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level daily cache (reset by date key)
# ---------------------------------------------------------------------------
_roster_cache: dict[str, Any] = {}        # {"{sport}:{date}": [player_dict, ...]}
_schedule_cache: dict[str, Any] = {}      # {"{sport}:{date}": [game_dict, ...]}
_cache_lock = threading.Lock()

_REQUEST_TIMEOUT = 10   # seconds per HTTP call


# ---------------------------------------------------------------------------
# Name normalization (shared with normalizer.py)
# ---------------------------------------------------------------------------

_TRAILING_PERIOD_RE = re.compile(r"\.+$")
_ROMAN_SUFFIX_RE    = re.compile(r"\b(iii|iv|vi|vii|viii|ix)\s*$", re.IGNORECASE)
_GEN_SUFFIX_RE      = re.compile(r"\b(jr\.?|sr\.?)\s*$", re.IGNORECASE)
_OCR_SUBS = str.maketrans({"0": "o", "1": "l", "!": "i"})


def normalize_name(raw: str) -> str:
    """
    Lowercase, strip diacritics, apply common OCR digit substitutions
    (0→o, 1→l, !→i), remove Jr/Sr/III/IV suffixes, collapse whitespace.
    """
    # Apply OCR digit substitutions first (before unicode normalize strips context)
    fixed = raw.translate(_OCR_SUBS)
    nfkd = unicodedata.normalize("NFKD", fixed.strip())
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    lower = ascii_str.lower()
    # Strip trailing periods from each token ("Jr." → "jr", "Sr." → "sr")
    lower = " ".join(_TRAILING_PERIOD_RE.sub("", tok) for tok in lower.split())
    # Remove pure Roman-numeral suffixes (III, IV…) — these don't disambiguate
    # NOTE: Jr/Sr are kept because they DO disambiguate (different people)
    lower = _ROMAN_SUFFIX_RE.sub("", lower).strip()
    return re.sub(r"\s+", " ", lower)


def normalize_ocr(raw: str) -> str:
    """
    Like normalize_name but also applies common OCR character substitutions
    (0→o, 1→l, !→i) before matching.
    """
    fixed = raw.translate(_OCR_SUBS)
    return normalize_name(fixed)


# ---------------------------------------------------------------------------
# Nickname dictionaries (merged from both versions)
# ---------------------------------------------------------------------------

NICKNAMES: dict[str, str] = {
    # NBA
    "steph":          "stephen curry",
    "steph curry":    "stephen curry",
    "pg13":           "paul george",
    "pg-13":          "paul george",
    "kd":             "kevin durant",
    "lebron":         "lebron james",
    "bron":           "lebron james",
    "ad":             "anthony davis",
    "cp3":            "chris paul",
    "giannis":        "giannis antetokounmpo",
    "greek freak":    "giannis antetokounmpo",
    "the greek freak":"giannis antetokounmpo",
    "dame":           "damian lillard",
    "dame dolla":     "damian lillard",
    "russ":           "russell westbrook",
    "spida":          "donovan mitchell",
    "joker":          "nikola jokic",
    "jokic":          "nikola jokic",
    "ja":             "ja morant",
    "luka":           "luka doncic",
    "tatum":          "jayson tatum",
    "jt":             "jayson tatum",
    "embiid":         "joel embiid",
    "jojo":           "joel embiid",
    "bam":            "bam adebayo",
    "demar":          "demar derozan",
    "sga":            "shai gilgeous-alexander",
    "shai":           "shai gilgeous-alexander",
    "ant":            "anthony edwards",
    "ant-man":        "anthony edwards",
    "chet":           "chet holmgren",
    "wemby":          "victor wembanyama",
    "payton":         "payton pritchard",
    "melo":           "carmelo anthony",
    "kawhi":          "kawhi leonard",
    "dlo":            "d'angelo russell",
    "lamelo":         "lamelo ball",
    "trae":           "trae young",
    "tyrese":         "tyrese haliburton",
    # WNBA
    "a'ja":           "a'ja wilson",
    "aja":            "a'ja wilson",
    "breanna":        "breanna stewart",
    "stewie":         "breanna stewart",
    "sabrina":        "sabrina ionescu",
    "caitlin":        "caitlin clark",
    "arike":          "arike ogunbowale",
    # MLB
    "trout":          "mike trout",
    "ohtani":         "shohei ohtani",
    "shohei":         "shohei ohtani",
    "judge":          "aaron judge",
    "mookie":         "mookie betts",
    "trea":           "trea turner",
    "vlad":           "vladimir guerrero",
    "vlad jr":        "vladimir guerrero",
    "vladdy":         "vladimir guerrero jr",
    "acuna":          "ronald acuna jr",
    "tatis":          "fernando tatis jr",
    "bo":             "bo bichette",
    # NFL
    "mahomes":        "patrick mahomes",
    "cmac":           "christian mccaffrey",
    "c-mac":          "christian mccaffrey",
    "tyreek":         "tyreek hill",
    "cheetah":        "tyreek hill",
    "diggs":          "stefon diggs",
    "davante":        "davante adams",
    "ja'marr":        "ja'marr chase",
    "chase":          "ja'marr chase",
    "kupp":           "cooper kupp",
    "najee":          "najee harris",
    "josh allen":     "josh allen",
    "lamar":          "lamar jackson",
    "hurts":          "jalen hurts",
    "burrow":         "joe burrow",
    "sauce":          "sauce gardner",
}

# For backward compat (some code imports _NICKNAMES)
_NICKNAMES = NICKNAMES


def expand_nickname(name_lower: str) -> Optional[str]:
    """Return canonical full name if input is a known nickname, else None."""
    return NICKNAMES.get(name_lower.strip())


# ---------------------------------------------------------------------------
# Cache key helpers
# ---------------------------------------------------------------------------

def _today_str(target_date: Optional[date] = None) -> str:
    return (target_date or date.today()).isoformat()


def _cache_key(sport: str, date_str: str) -> str:
    return f"{sport.upper()}:{date_str}"


# ---------------------------------------------------------------------------
# Record builder (dual field names for compat)
# ---------------------------------------------------------------------------

def _make_record(player_id: str, name: str, team: str, position: str,
                 sport: str, source: str) -> dict:
    """
    Return a roster record with BOTH field-name conventions so both the
    incoming normalizer (name_norm / team_abbr) and this task's normalizer
    (name_normalized / team) work without translation.
    """
    norm = normalize_name(name)
    team_up = (team or "").upper()
    return {
        # Canonical fields (this task's normalizer)
        "player_id":      player_id,
        "name":           name,
        "name_normalized": norm,
        "team":           team_up,
        "position":       position,
        "sport":          sport.upper(),
        # Alias fields (incoming normalizer)
        "name_raw":  name,
        "name_norm": norm,
        "team_abbr": team_up,
        "source":    source,
    }


# ---------------------------------------------------------------------------
# NBA roster (ESPN primary, nba_api fallback)
# ---------------------------------------------------------------------------

def _fetch_nba_roster(sport: str = "NBA") -> list[dict]:
    """
    Fetch NBA/WNBA roster from ESPN (includes team abbreviations).
    Falls back to nba_api static list (which lacks team info — team left blank).
    """
    sport_up   = sport.upper()
    sport_path = "nba" if sport_up == "NBA" else "wnba"
    records: list[dict] = []

    try:
        teams_url = (
            f"https://site.api.espn.com/apis/site/v2/sports/"
            f"basketball/{sport_path}/teams"
        )
        r = requests.get(teams_url, timeout=_REQUEST_TIMEOUT)
        if r.status_code == 200:
            sports_data = r.json().get("sports", [])
            leagues = sports_data[0].get("leagues", []) if sports_data else []
            teams = leagues[0].get("teams", []) if leagues else []
            for team_entry in teams:
                team = team_entry.get("team", {})
                team_abbr = team.get("abbreviation", "")
                team_id   = team.get("id", "")
                if not team_id:
                    continue
                try:
                    roster_url = (
                        f"https://site.api.espn.com/apis/site/v2/sports/"
                        f"basketball/{sport_path}/teams/{team_id}/roster"
                    )
                    rr = requests.get(roster_url, timeout=_REQUEST_TIMEOUT)
                    if rr.status_code != 200:
                        continue
                    for group in rr.json().get("athletes", []):
                        items = group.get("items", [group]) if "items" in group else [group]
                        for athlete in items:
                            name = athlete.get("fullName") or athlete.get("displayName") or ""
                            if not name:
                                continue
                            pos = (athlete.get("position") or {}).get("abbreviation", "")
                            records.append(_make_record(
                                str(athlete.get("id", "")), name,
                                team_abbr, pos, sport_up, "espn",
                            ))
                except Exception:
                    continue
    except Exception as exc:
        logger.warning("roster_cache: ESPN %s roster failed: %s", sport_up, exc)

    if records:
        logger.info("roster_cache: loaded %d %s players (ESPN)", len(records), sport_up)
        return records

    # Fallback: nba_api static (no team info)
    try:
        from nba_api.stats.static import players as _pl  # noqa: PLC0415
        for p in _pl.get_active_players():
            raw  = p.get("full_name", "")
            team = p.get("team_abbreviation") or p.get("team", "") or ""
            records.append(_make_record(
                str(p["id"]), raw, team, p.get("position") or "", sport_up, "nba_api",
            ))
        logger.info("roster_cache: loaded %d %s players (nba_api)", len(records), sport_up)
    except Exception as exc:
        logger.warning("roster_cache: nba_api fallback failed: %s", exc)

    return records


# ---------------------------------------------------------------------------
# WNBA roster (BallDontLie → ESPN)
# ---------------------------------------------------------------------------

def _fetch_wnba_roster() -> list[dict]:
    import os
    bdl_key = os.environ.get("balldontlie") or os.environ.get("BALLDONTLIE_API_KEY", "")
    if bdl_key:
        try:
            r = requests.get(
                "https://api.balldontlie.io/wnba/v1/players",
                headers={"Authorization": bdl_key},
                params={"per_page": 100},
                timeout=10,
            )
            if r.status_code == 200:
                players = r.json().get("data", [])
                records = []
                for p in players:
                    raw  = f"{p.get('first_name','')} {p.get('last_name','')}".strip()
                    team = (p.get("team") or {}).get("abbreviation") or ""
                    records.append(_make_record(
                        str(p["id"]), raw, team, p.get("position") or "", "WNBA", "balldontlie",
                    ))
                if records:
                    logger.info("roster_cache: loaded %d WNBA players (BallDontLie)", len(records))
                    return records
        except Exception as exc:
            logger.warning("roster_cache: BallDontLie WNBA failed: %s", exc)

    # Fallback: ESPN
    return _fetch_nba_roster("WNBA")


# ---------------------------------------------------------------------------
# MLB roster via statsapi.mlb.com
# ---------------------------------------------------------------------------

_MLB_API = "https://statsapi.mlb.com/api/v1"

_MLB_TEAM_ID_TO_ABBR: dict[int, str] = {
    108: "LAA", 109: "ARI", 110: "BAL", 111: "BOS", 112: "CHC",
    113: "CIN", 114: "CLE", 115: "COL", 116: "DET", 117: "HOU",
    118: "KC",  119: "LAD", 120: "WSH", 121: "NYM", 133: "OAK",
    134: "PIT", 135: "SD",  136: "SEA", 137: "SF",  138: "STL",
    139: "TB",  140: "TEX", 141: "TOR", 142: "MIN", 143: "PHI",
    144: "ATL", 145: "CWS", 146: "MIA", 147: "NYY", 158: "MIL",
}


def _fetch_mlb_roster() -> list[dict]:
    try:
        r = requests.get(
            f"{_MLB_API}/sports/1/players",
            params={"season": date.today().year, "gameType": "R"},
            timeout=_REQUEST_TIMEOUT,
            headers={"User-Agent": "WOW/1.0"},
        )
        if r.status_code != 200:
            return []
        people = r.json().get("people", [])
        records = []
        for p in people:
            raw     = p.get("fullName", "")
            team_id = (p.get("currentTeam") or {}).get("id", 0)
            team    = _MLB_TEAM_ID_TO_ABBR.get(team_id, "")
            pos     = (p.get("primaryPosition") or {}).get("abbreviation", "")
            records.append(_make_record(str(p["id"]), raw, team, pos, "MLB", "mlb_stats_api"))
        logger.info("roster_cache: loaded %d MLB players", len(records))
        return records
    except Exception as exc:
        logger.warning("roster_cache: MLB Stats API failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# NFL / NHL roster via ESPN
# ---------------------------------------------------------------------------

_ESPN_SPORT_PATHS: dict[str, tuple[str, str]] = {
    "NFL": ("football",  "nfl"),
    "NHL": ("hockey",    "nhl"),
}


def _fetch_espn_roster(sport: str) -> list[dict]:
    sport_up = sport.upper()
    paths = _ESPN_SPORT_PATHS.get(sport_up)
    if not paths:
        return []
    s_path, l_path = paths
    records: list[dict] = []

    try:
        teams_url = (
            f"https://site.api.espn.com/apis/site/v2/sports/{s_path}/{l_path}/teams"
        )
        resp = requests.get(teams_url, timeout=_REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return []
        teams = resp.json().get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", [])
        for team_entry in teams[:32]:
            team_info = team_entry.get("team", {})
            team_abbr = team_info.get("abbreviation", "").upper()
            team_id   = team_info.get("id", "")
            if not team_id:
                continue
            try:
                roster_url = (
                    f"https://site.api.espn.com/apis/site/v2/sports/"
                    f"{s_path}/{l_path}/teams/{team_id}/roster"
                )
                rr = requests.get(roster_url, timeout=_REQUEST_TIMEOUT)
                if rr.status_code != 200:
                    continue
                athletes = rr.json().get("athletes", [])
                # NFL returns position groups — flatten
                if athletes and isinstance(athletes[0], dict) and "items" in athletes[0]:
                    flat: list = []
                    for grp in athletes:
                        flat.extend(grp.get("items", []))
                    athletes = flat
                for a in athletes:
                    raw = a.get("fullName") or a.get("displayName") or ""
                    if not raw:
                        continue
                    pos = (a.get("position") or {}).get("abbreviation", "")
                    records.append(_make_record(
                        str(a.get("id", "")), raw, team_abbr, pos, sport_up, "espn",
                    ))
            except Exception:
                continue
    except Exception as exc:
        logger.warning("roster_cache: ESPN %s/%s failed: %s", s_path, l_path, exc)

    logger.info("roster_cache: loaded %d %s players (ESPN)", len(records), sport_up)
    return records


# ---------------------------------------------------------------------------
# Schedule fetchers
# ---------------------------------------------------------------------------

_ESPN_SCHEDULE_PATHS: dict[str, tuple[str, str]] = {
    "NBA":  ("basketball", "nba"),
    "WNBA": ("basketball", "wnba"),
    "NFL":  ("football",   "nfl"),
    "NHL":  ("hockey",     "nhl"),
}


def _fetch_espn_schedule(sport: str, target_date: Optional[date] = None) -> list[dict]:
    sport_up = sport.upper()
    paths = _ESPN_SCHEDULE_PATHS.get(sport_up)
    if not paths:
        return []
    s_path, l_path = paths
    date_str = (target_date or date.today()).strftime("%Y%m%d")

    try:
        url = f"https://site.api.espn.com/apis/site/v2/sports/{s_path}/{l_path}/scoreboard"
        r = requests.get(url, params={"dates": date_str}, timeout=_REQUEST_TIMEOUT)
        if r.status_code != 200:
            return []
        events = r.json().get("events", [])
        games = []
        for ev in events:
            comp = (ev.get("competitions") or [{}])[0]
            competitors = comp.get("competitors", [])
            home = next((c for c in competitors if c.get("homeAway") == "home"), None)
            away = next((c for c in competitors if c.get("homeAway") == "away"), None)
            home_abbr = (home or {}).get("team", {}).get("abbreviation", "")
            away_abbr = (away or {}).get("team", {}).get("abbreviation", "")
            games.append({
                "game_id":   ev.get("id", ""),
                "home_team": home_abbr,
                "away_team": away_abbr,
                "game_time": ev.get("date", ""),
                "sport":     sport_up,
            })
        return games
    except Exception as exc:
        logger.debug("schedule_espn %s: %s", sport_up, exc)
        return []


def _fetch_mlb_schedule(target_date: Optional[date] = None) -> list[dict]:
    date_str = (target_date or date.today()).isoformat()
    try:
        r = requests.get(
            f"{_MLB_API}/schedule",
            params={"sportId": 1, "date": date_str, "gameType": "R,F,D,L,W"},
            timeout=_REQUEST_TIMEOUT,
            headers={"User-Agent": "WOW/1.0"},
        )
        if r.status_code != 200:
            return []
        games = []
        for day in r.json().get("dates", []):
            for g in day.get("games", []):
                home_id = (g.get("teams", {}).get("home", {}).get("team") or {}).get("id", 0)
                away_id = (g.get("teams", {}).get("away", {}).get("team") or {}).get("id", 0)
                games.append({
                    "game_id":   str(g.get("gamePk", "")),
                    "home_team": _MLB_TEAM_ID_TO_ABBR.get(home_id, ""),
                    "away_team": _MLB_TEAM_ID_TO_ABBR.get(away_id, ""),
                    "game_time": g.get("gameDate", ""),
                    "sport":     "MLB",
                })
        return games
    except Exception as exc:
        logger.debug("schedule_mlb: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Public cache API
# ---------------------------------------------------------------------------

def get_roster(sport: str, target_date: Optional[date] = None) -> list[dict]:
    """Return cached roster for sport on target_date (default: today). Never raises."""
    sport = sport.upper()
    date_str = _today_str(target_date)
    key = _cache_key(sport, date_str)

    with _cache_lock:
        if key in _roster_cache:
            return _roster_cache[key]

    players = _fetch_roster_for_sport(sport)

    with _cache_lock:
        _roster_cache[key] = players

    return players


def get_schedule(sport: str, target_date: Optional[date] = None) -> list[dict]:
    """Return today's games for sport on target_date (default: today). Never raises."""
    sport = sport.upper()
    date_str = _today_str(target_date)
    key = _cache_key(sport, date_str)

    with _cache_lock:
        if key in _schedule_cache:
            return _schedule_cache[key]

    games = _fetch_schedule_for_sport(sport, target_date)

    with _cache_lock:
        _schedule_cache[key] = games

    return games


def invalidate_cache(sport: Optional[str] = None) -> None:
    """Clear roster and schedule caches (for testing or forced refresh)."""
    with _cache_lock:
        if sport:
            to_del = [k for k in list(_roster_cache) + list(_schedule_cache)
                      if k.startswith(sport.upper() + ":")]
            for k in to_del:
                _roster_cache.pop(k, None)
                _schedule_cache.pop(k, None)
        else:
            _roster_cache.clear()
            _schedule_cache.clear()


# Alias for incoming code that calls bust_cache
bust_cache = invalidate_cache


def _fetch_roster_for_sport(sport: str) -> list[dict]:
    sport = sport.upper()
    if sport == "NBA":
        return _fetch_nba_roster("NBA")
    elif sport == "WNBA":
        return _fetch_wnba_roster()
    elif sport == "MLB":
        return _fetch_mlb_roster()
    elif sport in ("NFL", "NHL"):
        return _fetch_espn_roster(sport)
    else:
        return []


def _fetch_schedule_for_sport(sport: str, target_date: Optional[date] = None) -> list[dict]:
    sport = sport.upper()
    if sport in ("NBA", "WNBA", "NFL", "NHL"):
        return _fetch_espn_schedule(sport, target_date)
    elif sport == "MLB":
        return _fetch_mlb_schedule(target_date)
    else:
        return []


def teams_playing_today(sport: str, target_date: Optional[date] = None) -> set[str]:
    """Return set of team abbreviations with a game on target_date."""
    games = get_schedule(sport, target_date)
    playing: set[str] = set()
    for g in games:
        if g.get("home_team"):
            playing.add(g["home_team"].upper())
        if g.get("away_team"):
            playing.add(g["away_team"].upper())
    return playing


def game_for_team(sport: str, team_abbr: str,
                  target_date: Optional[date] = None) -> Optional[dict]:
    """Return the game dict for a team's game today, or None if no game."""
    games = get_schedule(sport, target_date)
    team_up = team_abbr.upper()
    for g in games:
        if (g.get("home_team", "").upper() == team_up
                or g.get("away_team", "").upper() == team_up):
            return g
    return None
