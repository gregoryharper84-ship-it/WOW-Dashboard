"""
TheRundown API service — backup odds source and event data.
Docs: https://rapidapi.com/therundown/api/therundown
"""
import os
import requests
from datetime import datetime, timezone

RUNDOWN_API_KEY = os.environ.get("RUNDOWN_API_KEY", "")
BASE_URL = "https://therundown-therundown-v1.p.rapidapi.com"

SPORT_IDS = {
    "NBA":    4,
    "MLB":    3,
    "NFL":    2,
    "NHL":    6,
    "NCAAB":  5,
    "NCAAF":  1,
    "Soccer": 7,
    "Tennis": 9,
}

HEADERS = {
    "x-rapidapi-host": "therundown-therundown-v1.p.rapidapi.com",
}


def _get(path, params=None):
    if not RUNDOWN_API_KEY:
        return None, "NOT_CALLED: RUNDOWN_API_KEY not set"
    headers = {**HEADERS, "x-rapidapi-key": RUNDOWN_API_KEY}
    try:
        r = requests.get(f"{BASE_URL}{path}", headers=headers, params=params or {}, timeout=15)
        if r.status_code == 200:
            return r.json(), "AVAILABLE"
        elif r.status_code == 401 or r.status_code == 403:
            return None, "FAILED: invalid RUNDOWN_API_KEY"
        elif r.status_code == 429:
            return None, "FAILED: rate limit exceeded"
        else:
            return None, f"FAILED: HTTP {r.status_code}"
    except requests.exceptions.Timeout:
        return None, "FAILED: timeout"
    except Exception as e:
        return None, f"FAILED: {e}"


def get_events_for_sport(sport, date_str=None):
    """Get events for a sport on a given date (YYYY-MM-DD, defaults to today)."""
    sport_id = SPORT_IDS.get(sport)
    if not sport_id:
        return [], "NOT_CALLED: unknown sport"
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data, status = _get(f"/sports/{sport_id}/events/{date_str}")
    events = (data or {}).get("events", [])
    return events, status


def get_line_movements(event_id):
    """Get line movement history for an event."""
    data, status = _get(f"/events/{event_id}/lines")
    return data, status


def extract_props_from_events(events, sport):
    """
    Parse TheRundown events into flat prop dicts where player prop lines exist.
    Returns list of {player, prop, side, line, sport, game_date, home_team, away_team}
    """
    props = []
    for event in (events or []):
        game_date = (event.get("event_date") or "")[:10]
        home = event.get("teams_normalized", [{}])[0].get("name", "")
        away = event.get("teams_normalized", [{}])[-1].get("name", "") if len(event.get("teams_normalized", [])) > 1 else ""
        # TheRundown primarily carries game lines; player props may be in lines_periods
        for line_source in (event.get("lines", {}) or {}).values():
            for period_key, period in (line_source.get("line_periods", {}) or {}).items():
                if not period:
                    continue
                for market_key in ["player_props"]:
                    market_data = period.get(market_key, {}) or {}
                    for player_name, player_lines in market_data.items():
                        for prop_name, prop_data in (player_lines or {}).items():
                            over = prop_data.get("over")
                            under = prop_data.get("under")
                            line_val = prop_data.get("line") or prop_data.get("total")
                            if line_val is None:
                                continue
                            if over is not None:
                                props.append({
                                    "player": player_name,
                                    "prop": prop_name,
                                    "side": "MORE",
                                    "line": float(line_val),
                                    "source": "rundown",
                                    "sport": sport,
                                    "game_date": game_date,
                                    "home_team": home,
                                    "away_team": away,
                                })
                            if under is not None:
                                props.append({
                                    "player": player_name,
                                    "prop": prop_name,
                                    "side": "LESS",
                                    "line": float(line_val),
                                    "source": "rundown",
                                    "sport": sport,
                                    "game_date": game_date,
                                    "home_team": home,
                                    "away_team": away,
                                })
    return props


def fetch_backup_props(sport):
    """
    Backup prop pull from TheRundown.
    Returns (props_list, status_string)
    """
    events, status = get_events_for_sport(sport)
    if not events:
        return [], status
    props = extract_props_from_events(events, sport)
    return props, status
