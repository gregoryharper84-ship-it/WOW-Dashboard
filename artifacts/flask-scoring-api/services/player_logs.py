"""
Player game log service — fetches recent per-game logs via ESPN core API
and computes L5/L10 hit rates, medians, and averages for a given prop line.

Endpoints used:
  - Search:   https://site.api.espn.com/apis/search/v2?query=<name>&type=player
  - Eventlog: https://sports.core.api.espn.com/v2/sports/<sport>/leagues/<league>/athletes/<id>/eventlog
  - Stats:    follows $ref from each event entry
"""
import statistics
import requests

SEARCH_URL  = "https://site.api.espn.com/apis/search/v2"
CORE_BASE   = "https://sports.core.api.espn.com/v2/sports"

SPORT_LEAGUE = {
    "NBA":   ("basketball", "nba"),
    "WNBA":  ("basketball", "wnba"),
    "MLB":   ("baseball",   "mlb"),
    "NFL":   ("football",   "nfl"),
    "NHL":   ("hockey",     "nhl"),
    "NCAAB": ("basketball", "mens-college-basketball"),
    "NCAAF": ("football",   "college-football"),
    "Soccer": ("soccer",    "usa.1"),
    "Tennis": ("tennis",    "atp"),
}

# Map from Odds API market key → ESPN stat names (checked in order)
PROP_STAT_MAP = {
    "player_points":                  ["points"],
    "player_rebounds":                ["rebounds", "totalRebounds"],
    "player_assists":                  ["assists"],
    "player_threes":                   ["threePointFieldGoalsMade", "threePointersMade"],
    "player_steals":                   ["steals"],
    "player_blocks":                   ["blocks"],
    "player_points_rebounds_assists":  ["pointsReboundsAssists"],  # computed below
    "pitcher_strikeouts":              ["strikeouts", "strikeOuts"],
    "batter_hits":                     ["hits"],
    "batter_home_runs":                ["homeRuns"],
    "batter_rbis":                     ["RBI", "rbi", "runsBattedIn"],
    "batter_total_bases":              ["totalBases"],
    "batter_strikeouts":               ["strikeouts", "strikeOuts"],
    "player_pass_yds":                 ["passingYards"],
    "player_rush_yds":                 ["rushingYards"],
    "player_reception_yds":            ["receivingYards"],
    "player_receptions":               ["receptions", "receivingReceptions"],
    "player_pass_tds":                 ["passingTouchdowns"],
    "player_rush_attempts":            ["rushingAttempts"],
    "player_tackles_assists":          ["totalTackles", "tacklesAssists"],
    "player_goals":                    ["goals"],
    "player_shots_on_goal":            ["shotsOnGoal"],
    "player_goal_scorer_anytime":      ["goals"],
    "player_shots_on_target":          ["shotsOnTarget", "shotsOnGoal"],
}

# Composite stats that need summing
COMPOSITE_STATS = {
    "pointsReboundsAssists": ["points", "rebounds", "assists"],
}


def _search_athlete_id(player_name):
    """
    Search ESPN for an athlete.
    Returns (athlete_id_str, sport_path, league_path) or (None, None, None).
    """
    try:
        r = requests.get(SEARCH_URL, params={
            "query": player_name,
            "limit": 5,
            "type":  "player",
        }, timeout=10)
        if r.status_code != 200:
            return None, None, None, f"FAILED: search HTTP {r.status_code}"

        results = r.json().get("results", [])
        for rtype in results:
            if rtype.get("type") != "player":
                continue
            for item in rtype.get("contents", []):
                uid = item.get("uid", "")          # e.g. "s:40~l:46~a:4278073"
                display = item.get("displayName", "")
                description = item.get("description", "")  # "NBA", "MLB", etc.

                # Exact name match preferred
                if display.lower() != player_name.lower():
                    # try partial
                    if player_name.lower() not in display.lower():
                        continue

                if "~a:" not in uid:
                    continue
                athlete_id = uid.split("~a:")[-1]

                # Derive sport/league paths from description
                sport_label = description.strip().upper()
                league_info = SPORT_LEAGUE.get(sport_label)
                if not league_info:
                    # Try all leagues — use first match
                    for k, v in SPORT_LEAGUE.items():
                        if k in sport_label or sport_label in k:
                            league_info = v
                            break
                if not league_info:
                    # Fallback: try common defaults from uid league id
                    league_info = ("basketball", "nba")  # will fail gracefully

                return athlete_id, league_info[0], league_info[1], "AVAILABLE"

        return None, None, None, "MISSING: player not found in ESPN search"
    except Exception as e:
        return None, None, None, f"FAILED: {e}"


def _fetch_eventlog(sport_path, league_path, athlete_id):
    """Fetch per-game event log for an athlete."""
    url = f"{CORE_BASE}/{sport_path}/leagues/{league_path}/athletes/{athlete_id}/eventlog"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            items = r.json().get("events", {}).get("items", [])
            return items, "AVAILABLE"
        return [], f"FAILED: HTTP {r.status_code}"
    except Exception as e:
        return [], f"FAILED: {e}"


def _follow_stats_ref(ref_url):
    """Follow a $ref URL and return the splits/categories stats dict."""
    try:
        r = requests.get(ref_url, timeout=10)
        if r.status_code != 200:
            return {}
        return r.json().get("splits", {}).get("categories", [])
    except Exception:
        return []


def _extract_stat_value(categories, stat_names):
    """
    Extract a single numeric stat value from ESPN categories list.
    Handles composite stats (sum of parts).
    Returns float or None.
    """
    stat_lookup = {}
    for cat in categories:
        for s in cat.get("stats", []):
            stat_lookup[s["name"]] = s.get("value")

    for stat_name in stat_names:
        # Composite stat?
        if stat_name in COMPOSITE_STATS:
            parts = COMPOSITE_STATS[stat_name]
            total = 0.0
            found_all = True
            for p in parts:
                if p not in stat_lookup:
                    found_all = False
                    break
                v = stat_lookup[p]
                if v is None:
                    found_all = False
                    break
                total += float(v)
            if found_all:
                return total
        elif stat_name in stat_lookup and stat_lookup[stat_name] is not None:
            return float(stat_lookup[stat_name])

    return None


def _get_game_values(sport_path, league_path, athlete_id, stat_names, max_games=15):
    """
    Pull up to max_games per-game stat values by following eventlog $refs.
    Returns list of floats (most recent first).
    """
    items, log_status = _fetch_eventlog(sport_path, league_path, athlete_id)
    if not items:
        return [], log_status

    values = []
    # items are ordered most-recent-first typically
    for item in items[:max_games]:
        # statistics can be {"$ref": "..."} or [{"$ref": "..."}, ...]
        stats_field = item.get("statistics")
        if not stats_field:
            continue
        if isinstance(stats_field, dict):
            ref_url = stats_field.get("$ref")
        elif isinstance(stats_field, list) and stats_field:
            ref_url = stats_field[0].get("$ref") if isinstance(stats_field[0], dict) else None
        else:
            continue
        if not ref_url:
            continue
        categories = _follow_stats_ref(ref_url)
        val = _extract_stat_value(categories, stat_names)
        if val is not None:
            values.append(val)

    return values, log_status if values else f"PARTIAL: {log_status}, 0 values parsed"


def compute_hit_stats(values, line, side):
    """
    Compute L5/L10 hit rate, median, and average.
    Returns dict with l5_hit_rate, l10_hit_rate, l10_median, l10_avg, games_found.
    """
    if not values:
        return {
            "l5_hit_rate": None, "l10_hit_rate": None,
            "l10_median": None,  "l10_avg": None,
            "games_found": 0,
        }

    def hit(v):
        return (v > line) if side == "MORE" else (v < line)

    l5  = values[:5]
    l10 = values[:10]

    return {
        "l5_hit_rate":  round(sum(1 for v in l5  if hit(v)) / len(l5),  4) if l5  else None,
        "l10_hit_rate": round(sum(1 for v in l10 if hit(v)) / len(l10), 4) if l10 else None,
        "l10_median":   round(statistics.median(l10), 4) if l10 else None,
        "l10_avg":      round(sum(l10) / len(l10), 4) if l10 else None,
        "games_found":  len(values),
    }


def get_player_log_stats(sport, player_name, prop, line, side):
    """
    Full pipeline: search athlete → eventlog → per-game stats → hit stats.
    Returns (stats_dict, log_status_string).
    """
    stat_names = PROP_STAT_MAP.get(prop)
    if not stat_names:
        empty = {"l5_hit_rate": None, "l10_hit_rate": None,
                 "l10_median": None, "l10_avg": None, "games_found": 0}
        return empty, "NOT_CALLED: no stat mapping for prop"

    athlete_id, sport_path, league_path, search_status = _search_athlete_id(player_name)
    if not athlete_id:
        empty = {"l5_hit_rate": None, "l10_hit_rate": None,
                 "l10_median": None, "l10_avg": None, "games_found": 0}
        return empty, search_status

    # Override league from sport arg if search returned wrong league
    known = SPORT_LEAGUE.get(sport)
    if known:
        sport_path, league_path = known

    values, log_status = _get_game_values(sport_path, league_path, athlete_id, stat_names)
    stats = compute_hit_stats(values, float(line), side)
    return stats, log_status
