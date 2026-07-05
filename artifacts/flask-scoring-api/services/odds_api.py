"""
The Odds API service — pulls events, markets, and player props.
Docs: https://the-odds-api.com/lts-odds-api/
"""
import os
import requests
from datetime import datetime, timezone

ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
BASE_URL = "https://api.the-odds-api.com/v4"

SPORT_KEYS = {
    "NBA":   "basketball_nba",
    "WNBA":  "basketball_wnba",
    "MLB":   "baseball_mlb",
    "NFL":   "americanfootball_nfl",
    "NHL":   "icehockey_nhl",
    "NCAAB": "basketball_ncaab",
    "NCAAF": "americanfootball_ncaaf",
    "Soccer": "soccer_epl",
    "Tennis": "tennis_atp_french_open",
}

PLAYER_PROP_MARKETS = {
    "NBA":  [
        "player_points", "player_rebounds", "player_assists",
        "player_threes", "player_steals", "player_blocks",
        "player_points_rebounds_assists",
    ],
    "WNBA": [
        "player_points", "player_rebounds", "player_assists",
        "player_threes", "player_steals", "player_blocks",
        "player_points_rebounds_assists",
    ],
    "MLB":  [
        "batter_hits", "batter_home_runs", "batter_rbis",
        "batter_strikeouts", "batter_total_bases",
        "pitcher_strikeouts", "pitcher_hits_allowed",
        "pitcher_walks", "pitcher_earned_runs",
    ],
    "NFL":  [
        "player_pass_tds", "player_pass_yds", "player_rush_yds",
        "player_reception_yds", "player_receptions",
        "player_rush_attempts", "player_tackles_assists",
    ],
    "NHL":  [
        "player_points", "player_goals", "player_assists",
        "player_shots_on_goal",
    ],
    "NCAAB": ["player_points", "player_rebounds", "player_assists"],
    "NCAAF": ["player_pass_yds", "player_rush_yds", "player_reception_yds"],
    "Soccer": ["player_goal_scorer_anytime", "player_shots_on_target"],
    "Tennis": ["player_set_winner"],
}


def _get(path, params=None):
    if not ODDS_API_KEY:
        return None, "NOT_CALLED: ODDS_API_KEY not set"
    params = params or {}
    params["apiKey"] = ODDS_API_KEY
    try:
        r = requests.get(f"{BASE_URL}{path}", params=params, timeout=15)
        remaining = r.headers.get("x-requests-remaining", "?")
        if r.status_code == 200:
            return r.json(), f"AVAILABLE (remaining={remaining})"
        elif r.status_code == 401:
            return None, "FAILED: invalid ODDS_API_KEY"
        elif r.status_code == 422:
            return None, f"FAILED: unprocessable ({r.text[:120]})"
        elif r.status_code == 429:
            return None, "FAILED: quota exhausted"
        else:
            return None, f"FAILED: HTTP {r.status_code}"
    except requests.exceptions.Timeout:
        return None, "FAILED: timeout"
    except Exception as e:
        return None, f"FAILED: {e}"


def get_sports():
    data, status = _get("/sports")
    return data or [], status


def get_events(sport_key):
    """Return today's events for a sport key."""
    data, status = _get(f"/sports/{sport_key}/events", {
        "dateFormat": "iso",
        "commenceTimeFrom": datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z"),
        "commenceTimeTo":   datetime.now(timezone.utc).strftime("%Y-%m-%dT23:59:59Z"),
    })
    return data or [], status


def get_h2h_odds(sport_key):
    """
    Return today's moneyline (h2h) odds across all events for a sport key
    in one call — used by kalshi_engine.llp_bridge.consensus_odds for the
    no-vig sportsbook consensus gate. Read-only; no order placement.
    Returns (events_list, status_string).
    """
    data, status = _get(f"/sports/{sport_key}/odds", {
        "regions":    "us",
        "markets":    "h2h",
        "oddsFormat": "american",
        "dateFormat": "iso",
    })
    return data or [], status


def get_player_props(sport_key, event_id, markets):
    """Return player prop odds for a specific event + market list."""
    data, status = _get(f"/sports/{sport_key}/events/{event_id}/odds", {
        "regions":   "us",
        "markets":   ",".join(markets),
        "oddsFormat": "american",
        "dateFormat": "iso",
    })
    return data, status


def extract_props_from_event(event_data, sport):
    """
    Parse bookmaker outcomes into flat prop dicts.
    Returns list of:
      {player, prop, side, line, bookmaker, price, sport, game_date, home_team, away_team}
    """
    if not event_data:
        return []
    props = []
    game_date = (event_data.get("commence_time", "") or "")[:10]
    home = event_data.get("home_team", "")
    away = event_data.get("away_team", "")
    for bm in (event_data.get("bookmakers") or []):
        for market in (bm.get("markets") or []):
            mkey = market.get("key", "")
            for outcome in (market.get("outcomes") or []):
                name = outcome.get("description") or outcome.get("name", "")
                side_raw = outcome.get("name", "")
                side = "MORE" if side_raw.upper() in ("OVER", "MORE") else (
                    "LESS" if side_raw.upper() in ("UNDER", "LESS") else side_raw.upper()
                )
                point = outcome.get("point")
                if point is None:
                    continue
                props.append({
                    "player": name,
                    "prop": mkey,
                    "side": side,
                    "line": float(point),
                    "bookmaker": bm.get("key", ""),
                    "price": outcome.get("price"),
                    "sport": sport,
                    "game_date": game_date,
                    "home_team": home,
                    "away_team": away,
                })
    return props


def fetch_all_props(sport):
    """
    Full pipeline for one sport:
    1. Get today's events
    2. For each event, fetch player prop markets
    3. Parse into flat prop list
    Returns (props_list, status_dict)
    """
    sport_key = SPORT_KEYS.get(sport)
    if not sport_key:
        return [], {"events": "NOT_CALLED: unknown sport", "props": "NOT_CALLED"}

    events, ev_status = get_events(sport_key)
    if not events:
        return [], {"events": ev_status, "props": "NOT_CALLED: no events"}

    markets = PLAYER_PROP_MARKETS.get(sport, [])
    if not markets:
        return [], {"events": ev_status, "props": "NOT_CALLED: no markets defined"}

    all_props = []
    prop_status = "NOT_RETRIEVED"
    for event in events[:10]:
        event_id = event.get("id")
        if not event_id:
            continue
        data, p_status = get_player_props(sport_key, event_id, markets)
        prop_status = p_status
        if data:
            all_props.extend(extract_props_from_event(data, sport))

    return all_props, {"events": ev_status, "props": prop_status}
