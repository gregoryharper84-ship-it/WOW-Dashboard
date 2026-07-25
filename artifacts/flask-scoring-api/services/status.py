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


def get_mlb_scoreboard_trimmed(game_date=None):
    """
    Fetch ESPN's MLB scoreboard and strip it to only the fields the scoring
    engine needs: event_id, start_time, home/away team abbreviations, game
    status, and home/away probable pitchers.

    The raw ESPN response is ~300–400 KB for a full slate; this trimmed
    version is ~2–4 KB regardless of game count, making it safe to return
    from a Custom GPT Action (which has a ~100 KB response ceiling).

    Args:
        game_date: YYYYMMDD string (e.g. "20260725"). Defaults to today.

    Returns:
        (games: list[dict], status: str)
        Each game dict:
          {
            "event_id":             str,      # ESPN event id
            "start_time":           str,      # ISO-8601 UTC
            "home_team":            str,      # team abbreviation, e.g. "LAD"
            "away_team":            str,      # team abbreviation, e.g. "NYM"
            "status":               str,      # ESPN status name, e.g. "STATUS_SCHEDULED"
            "status_detail":        str,      # e.g. "Scheduled" or "Final"
            "status_short":         str,      # e.g. "7/25 - 1:10 PM EDT"
            "home_probable_pitcher": str|None,
            "away_probable_pitcher": str|None,
          }
    """
    params = {"limit": 30}
    if game_date:
        params["dates"] = game_date
    data, status = _get_espn("/baseball/mlb/scoreboard", params)
    if not data:
        return [], status

    games = []
    for event in (data.get("events") or []):
        comp = (event.get("competitions") or [{}])[0]
        competitors = comp.get("competitors") or []

        home = next((c for c in competitors if c.get("homeAway") == "home"), {})
        away = next((c for c in competitors if c.get("homeAway") == "away"), {})

        def _abbr(c):
            return (c.get("team") or {}).get("abbreviation") or ""

        def _probable(c):
            prob = (c.get("probables") or [{}])[0]
            if not prob:
                return None
            return (prob.get("athlete") or {}).get("fullName") or None

        stype = (comp.get("status") or {}).get("type") or {}

        games.append({
            "event_id":              event.get("id", ""),
            "start_time":            event.get("date", ""),
            "home_team":             _abbr(home),
            "away_team":             _abbr(away),
            "status":                stype.get("name", ""),
            "status_detail":         stype.get("detail", ""),
            "status_short":          stype.get("shortDetail", ""),
            "home_probable_pitcher": _probable(home),
            "away_probable_pitcher": _probable(away),
        })

    return games, status


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
