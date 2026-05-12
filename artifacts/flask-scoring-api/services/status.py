"""
Player status, injury, lineup, and starter service.
Primary: ESPN injury report + roster status.
MLB: probable pitchers via ESPN.
"""
import requests

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"

SPORT_ESPN = {
    "NBA":   ("basketball", "nba"),
    "WNBA":  ("basketball", "wnba"),
    "MLB":   ("baseball",   "mlb"),
    "NFL":   ("football",   "nfl"),
    "NHL":   ("hockey",     "nhl"),
    "NCAAB": ("basketball", "mens-college-basketball"),
    "NCAAF": ("football",   "college-football"),
}

INJURY_FLAG_MAP = {
    "active":       0,
    "day-to-day":   1,
    "questionable": 1,
    "doubtful":     1,
    "probable":     0,
    "out":          2,
    "injured reserve": 2,
    "ir":           2,
    "suspended":    2,
    "10-day il":    2,
    "15-day il":    2,
    "60-day il":    2,
}


def _get_espn(path, params=None):
    try:
        r = requests.get(f"{ESPN_BASE}{path}", params=params or {}, timeout=10)
        if r.status_code == 200:
            return r.json(), "AVAILABLE"
        return None, f"FAILED: HTTP {r.status_code}"
    except Exception as e:
        return None, f"FAILED: {e}"


def get_injuries(sport):
    """Return dict of {player_name_lower: injury_flag} for a sport."""
    league_info = SPORT_ESPN.get(sport)
    if not league_info:
        return {}, "NOT_CALLED: unsupported sport"
    sport_path, league = league_info
    data, status = _get_espn(f"/{sport_path}/{league}/injuries")
    if not data:
        return {}, status

    injuries = {}
    for team in (data.get("injuries") or []):
        for entry in (team.get("injuries") or []):
            athlete = entry.get("athlete", {})
            name = athlete.get("fullName", "").lower()
            raw_status = (entry.get("status") or "").lower()
            flag = INJURY_FLAG_MAP.get(raw_status, 0)
            if name:
                injuries[name] = {"flag": flag, "status_raw": raw_status}
    return injuries, status


def get_player_injury_flag(sport, player_name, injuries_cache=None):
    """
    Return (injury_flag, status_raw, source_status).
    0 = healthy/probable, 1 = questionable/day-to-day, 2 = out/IR.
    Uses cached injuries dict if provided (avoids repeat API call).
    """
    if injuries_cache is None:
        injuries_cache, source_status = get_injuries(sport)
    else:
        source_status = "AVAILABLE"

    key = player_name.lower()
    if key in injuries_cache:
        entry = injuries_cache[key]
        return entry["flag"], entry["status_raw"], source_status

    # Partial name match
    for cached_name, entry in injuries_cache.items():
        if key in cached_name or cached_name in key:
            return entry["flag"], entry["status_raw"], source_status

    return 0, "active", source_status


def get_mlb_probable_pitchers(game_date=None):
    """
    Return dict of {pitcher_name_lower: team} for today's probable pitchers.
    """
    data, status = _get_espn("/baseball/mlb/scoreboard", {"dates": game_date} if game_date else {})
    if not data:
        return {}, status
    pitchers = {}
    for event in (data.get("events") or []):
        for competitor in (event.get("competitions", [{}])[0].get("competitors") or []):
            probable = competitor.get("probables", [{}])
            if probable:
                name = probable[0].get("athlete", {}).get("fullName", "")
                team = competitor.get("team", {}).get("abbreviation", "")
                if name:
                    pitchers[name.lower()] = team
    return pitchers, status


def get_nba_starters(game_date=None):
    """Return set of confirmed starter names (lowercase) for today."""
    data, status = _get_espn("/basketball/nba/scoreboard", {"dates": game_date} if game_date else {})
    if not data:
        return set(), status
    starters = set()
    for event in (data.get("events") or []):
        for comp in (event.get("competitions", [{}])[0].get("competitors") or []):
            for lineup_entry in (comp.get("roster") or []):
                if lineup_entry.get("starter"):
                    name = lineup_entry.get("athlete", {}).get("fullName", "")
                    if name:
                        starters.add(name.lower())
    return starters, status
