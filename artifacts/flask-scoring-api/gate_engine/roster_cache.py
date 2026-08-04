"""
gate_engine/roster_cache.py

Fetches and daily-caches active rosters per sport so the normalizer can do
exact and fuzzy name lookups without hitting the sport APIs on every request.

Each record:
  {
    "player_id":   str,   # canonical ID (nba_api int→str, MLB str, ESPN str)
    "name_raw":    str,   # full name as returned by the source
    "name_norm":   str,   # lowercased, diacritics stripped, suffix removed
    "team_abbr":   str,   # e.g. "LAL", "NYY", "BOS"
    "sport":       str,   # "NBA" | "MLB" | "WNBA" | "NFL" | "NHL"
    "position":    str,   # e.g. "G", "SP", "WR"
    "source":      str,   # which API produced this
  }

Cache is in-memory, refreshed once per calendar day per sport.
"""

from __future__ import annotations

import re
import time
import unicodedata
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache store: sport → {fetched_at, records}
# ---------------------------------------------------------------------------
_ROSTER_CACHE: dict[str, dict] = {}
_ROSTER_TTL   = 86_400  # 24 hours


def _cache_valid(sport: str) -> bool:
    entry = _ROSTER_CACHE.get(sport)
    if not entry:
        return False
    return (time.time() - entry["fetched_at"]) < _ROSTER_TTL


def get_roster(sport: str) -> list[dict]:
    """Return cached roster for sport, refreshing if stale."""
    if not _cache_valid(sport):
        records = _fetch_roster(sport)
        _ROSTER_CACHE[sport] = {"fetched_at": time.time(), "records": records}
    return _ROSTER_CACHE[sport]["records"]


def bust_cache(sport: str | None = None) -> None:
    """Bust cache for one sport or all sports."""
    if sport:
        _ROSTER_CACHE.pop(sport, None)
    else:
        _ROSTER_CACHE.clear()


# ---------------------------------------------------------------------------
# Name normalisation helpers (shared with normalizer.py)
# ---------------------------------------------------------------------------

_TRAILING_PERIOD_RE = re.compile(r"\.+$")
_ROMAN_SUFFIX_RE    = re.compile(r"\b(iii|iv|vi|vii|viii|ix)\s*$", re.IGNORECASE)
_OCR_SUBS = str.maketrans({
    "0": "o", "1": "l", "!": "i",
})


def normalize_name(name: str) -> str:
    """
    Lowercase, strip diacritics, remove Roman-numeral-only suffixes (III, IV…),
    strip trailing periods from every token (so "Jr." → "jr"), collapse whitespace.
    Keeps generational words (jr, sr) because roster entries include them.
    Does NOT apply OCR substitutions (those are done only on the raw OCR token).
    """
    # Unicode NFKD → drop combining chars
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    lower = ascii_str.lower().strip()
    # Strip trailing periods from every token ("Jr." → "jr", "Jr" → "jr")
    lower = " ".join(_TRAILING_PERIOD_RE.sub("", tok) for tok in lower.split())
    # Remove pure Roman-numeral suffixes (III, IV …) — these aren't in roster names
    lower = _ROMAN_SUFFIX_RE.sub("", lower).strip()
    # Collapse internal whitespace
    return re.sub(r"\s+", " ", lower)


def normalize_ocr(name: str) -> str:
    """
    Like normalize_name but also applies common OCR substitutions
    (0→o, 1→l) before matching.
    """
    fixed = name.translate(_OCR_SUBS)
    return normalize_name(fixed)


# ---------------------------------------------------------------------------
# Nickname dictionary  (extended as new cases appear)
# ---------------------------------------------------------------------------

NICKNAMES: dict[str, str] = {
    # NBA
    "steph":         "stephen curry",
    "giannis":       "giannis antetokounmpo",
    "bron":          "lebron james",
    "cp3":           "chris paul",
    "kd":            "kevin durant",
    "pg13":          "paul george",
    "dame":          "damian lillard",
    "russ":          "russell westbrook",
    "melo":          "carmelo anthony",
    "kawhi":         "kawhi leonard",
    "joker":         "nikola jokic",
    "ant":           "anthony edwards",
    "shai":          "shai gilgeous-alexander",
    "dlo":           "d'angelo russell",
    "lamelo":        "lamelo ball",
    "trae":          "trae young",
    "tyrese":        "tyrese haliburton",
    # MLB
    "trout":         "mike trout",
    "ohtani":        "shohei ohtani",
    "acuna":         "ronald acuna jr",
    "tatis":         "fernando tatis jr",
    "vlad":          "vladimir guerrero jr",
    "vladdy":        "vladimir guerrero jr",
    "judge":         "aaron judge",
    # WNBA
    "caitlin":       "caitlin clark",
    "breanna":       "breanna stewart",
    "arike":         "arike ogunbowale",
    "aja":           "aja wilson",
    "a'ja":          "a'ja wilson",
}


# ---------------------------------------------------------------------------
# Sport-specific roster fetchers
# ---------------------------------------------------------------------------

_ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"
_MLB_API   = "https://statsapi.mlb.com/api/v1"


def _fetch_roster(sport: str) -> list[dict]:
    try:
        if sport == "NBA":
            return _roster_nba()
        if sport == "WNBA":
            return _roster_wnba()
        if sport == "MLB":
            return _roster_mlb()
        if sport == "NFL":
            return _roster_espn("football", "nfl")
        if sport == "NHL":
            return _roster_espn("hockey", "nhl")
    except Exception as exc:
        logger.warning("roster_cache: failed to fetch %s roster: %s", sport, exc)
    return []


# -- NBA via nba_api ----------------------------------------------------------

def _roster_nba() -> list[dict]:
    try:
        from nba_api.stats.static import players as _pl
    except ImportError:
        logger.warning("roster_cache: nba_api not installed")
        return []
    records = []
    for p in _pl.get_active_players():
        raw  = p.get("full_name", "")
        team = p.get("team_abbreviation") or p.get("team", "") or ""
        records.append({
            "player_id": str(p["id"]),
            "name_raw":  raw,
            "name_norm": normalize_name(raw),
            "team_abbr": team.upper(),
            "sport":     "NBA",
            "position":  p.get("position") or "",
            "source":    "nba_api",
        })
    logger.info("roster_cache: loaded %d NBA players", len(records))
    return records


# -- WNBA via BallDontLie or ESPN --------------------------------------------

def _roster_wnba() -> list[dict]:
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
                    raw = f"{p.get('first_name','')} {p.get('last_name','')}".strip()
                    team = (p.get("team") or {}).get("abbreviation") or ""
                    records.append({
                        "player_id": str(p["id"]),
                        "name_raw":  raw,
                        "name_norm": normalize_name(raw),
                        "team_abbr": team.upper(),
                        "sport":     "WNBA",
                        "position":  p.get("position") or "",
                        "source":    "balldontlie",
                    })
                logger.info("roster_cache: loaded %d WNBA players (BallDontLie)", len(records))
                return records
        except Exception as exc:
            logger.warning("roster_cache: BallDontLie WNBA failed: %s", exc)
    # Fallback: ESPN
    return _roster_espn("basketball", "wnba")


# -- MLB via MLB Stats API ---------------------------------------------------

def _roster_mlb() -> list[dict]:
    import datetime
    season = datetime.date.today().year
    # Fetch all active 40-man rosters via sports/1/players
    try:
        r = requests.get(
            f"{_MLB_API}/sports/1/players",
            params={"season": season, "gameType": "R"},
            timeout=12,
            headers={"User-Agent": "WOW/1.0"},
        )
        if r.status_code != 200:
            return []
        people = r.json().get("people", [])
        records = []
        for p in people:
            raw  = p.get("fullName", "")
            team = (p.get("currentTeam") or {}).get("abbreviation") or ""
            pos  = (p.get("primaryPosition") or {}).get("abbreviation") or ""
            records.append({
                "player_id": str(p["id"]),
                "name_raw":  raw,
                "name_norm": normalize_name(raw),
                "team_abbr": team.upper(),
                "sport":     "MLB",
                "position":  pos,
                "source":    "mlb_stats_api",
            })
        logger.info("roster_cache: loaded %d MLB players", len(records))
        return records
    except Exception as exc:
        logger.warning("roster_cache: MLB Stats API failed: %s", exc)
        return []


# -- ESPN generic (NFL, NHL, fallback WNBA) -----------------------------------

def _roster_espn(sport_path: str, league_path: str) -> list[dict]:
    """
    Fetch active athletes from ESPN's site API.
    Iterates team rosters since there's no single 'all active players' endpoint.
    """
    sport_upper = league_path.upper()
    records: list[dict] = []
    try:
        # Step 1: get team list
        teams_url = f"{_ESPN_BASE}/{sport_path}/{league_path}/teams"
        resp = requests.get(teams_url, timeout=10)
        if resp.status_code != 200:
            return []
        teams = resp.json().get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", [])
        for team_entry in teams:
            team_info = team_entry.get("team", {})
            team_abbr = team_info.get("abbreviation", "").upper()
            team_id   = team_info.get("id", "")
            if not team_id:
                continue
            try:
                roster_url = f"{_ESPN_BASE}/{sport_path}/{league_path}/teams/{team_id}/roster"
                rr = requests.get(roster_url, timeout=8)
                if rr.status_code != 200:
                    continue
                athletes = rr.json().get("athletes", [])
                # NFL returns position groups; flatten
                if isinstance(athletes, list) and athletes and isinstance(athletes[0], dict):
                    if "items" in athletes[0]:
                        flat: list = []
                        for grp in athletes:
                            flat.extend(grp.get("items", []))
                        athletes = flat
                for a in athletes:
                    raw = a.get("fullName") or a.get("displayName") or ""
                    if not raw:
                        continue
                    pos = (a.get("position") or {}).get("abbreviation") or ""
                    records.append({
                        "player_id": str(a.get("id", "")),
                        "name_raw":  raw,
                        "name_norm": normalize_name(raw),
                        "team_abbr": team_abbr,
                        "sport":     sport_upper,
                        "position":  pos,
                        "source":    "espn",
                    })
            except Exception:
                continue
    except Exception as exc:
        logger.warning("roster_cache: ESPN %s/%s failed: %s", sport_path, league_path, exc)
    logger.info("roster_cache: loaded %d %s players (ESPN)", len(records), sport_upper)
    return records
