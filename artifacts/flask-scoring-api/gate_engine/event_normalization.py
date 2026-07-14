"""
event_normalization.py — WOW-PATCH-2026-07-10
Team alias map + cross-platform event normalization.

Exposes:
  normalize_team(name, sport=None) -> canonical_abbrev | None
  build_event_id(sport, league, date, away, home) -> str
  group_entries_by_event(rows) -> dict[event_id, list[row]]

League-aware lookup prevents collisions between shared nicknames
(e.g. "Rangers" = TEX in MLB vs NYR in NHL; "Kings" = SAC in NBA vs LAK in NHL).
"""
from __future__ import annotations

import re
from typing import Any


# ---------------------------------------------------------------------------
# League-specific alias maps  (alias_lower -> canonical_abbrev)
# ---------------------------------------------------------------------------

_MLB: dict[str, str] = {
    "yankees": "NYY", "new york yankees": "NYY", "nyy": "NYY",
    "red sox": "BOS", "boston": "BOS", "bos": "BOS",
    "blue jays": "TOR", "toronto": "TOR", "tor": "TOR",
    "orioles": "BAL", "baltimore": "BAL", "bal": "BAL",
    "rays": "TB", "tampa bay": "TB", "tb": "TB",
    "white sox": "CWS", "chicago white sox": "CWS", "cws": "CWS",
    "indians": "CLE", "guardians": "CLE", "cleveland": "CLE", "cle": "CLE",
    "tigers": "DET", "detroit": "DET", "det": "DET",
    "royals": "KC", "kansas city": "KC", "kc": "KC",
    "twins": "MIN", "minnesota": "MIN", "min": "MIN",
    "astros": "HOU", "houston": "HOU", "hou": "HOU",
    "angels": "LAA", "los angeles angels": "LAA", "laa": "LAA",
    "athletics": "OAK", "oakland": "OAK", "oak": "OAK",
    "mariners": "SEA", "seattle": "SEA", "sea": "SEA",
    "rangers": "TEX", "texas": "TEX", "tex": "TEX",
    "braves": "ATL", "atlanta": "ATL", "atl": "ATL",
    "marlins": "MIA", "miami": "MIA", "mia": "MIA",
    "mets": "NYM", "new york mets": "NYM", "nym": "NYM",
    "phillies": "PHI", "philadelphia": "PHI", "phi": "PHI",
    "nationals": "WSH", "washington": "WSH", "wsh": "WSH",
    "cubs": "CHC", "chicago cubs": "CHC", "chc": "CHC",
    "reds": "CIN", "cincinnati": "CIN", "cin": "CIN",
    "brewers": "MIL", "milwaukee": "MIL", "mil": "MIL",
    "pirates": "PIT", "pittsburgh": "PIT", "pit": "PIT",
    "cardinals": "STL", "st. louis": "STL", "stl": "STL",
    "diamondbacks": "ARI", "arizona": "ARI", "ari": "ARI",
    "rockies": "COL", "colorado": "COL", "col": "COL",
    "dodgers": "LAD", "los angeles dodgers": "LAD", "lad": "LAD",
    "padres": "SD", "san diego": "SD", "sd": "SD",
    "giants": "SF", "san francisco": "SF", "sf": "SF",
}

_NBA: dict[str, str] = {
    "celtics": "BOS", "boston celtics": "BOS",
    "nets": "BKN", "brooklyn": "BKN", "bkn": "BKN",
    "knicks": "NYK", "new york knicks": "NYK", "nyk": "NYK",
    "76ers": "PHI", "sixers": "PHI",
    "raptors": "TOR", "toronto raptors": "TOR",
    "bulls": "CHI", "chicago bulls": "CHI", "chi": "CHI",
    "cavaliers": "CLE", "cavs": "CLE",
    "pistons": "DET", "detroit pistons": "DET",
    "pacers": "IND", "indiana": "IND", "ind": "IND",
    "bucks": "MIL", "milwaukee bucks": "MIL",
    "hawks": "ATL", "atlanta hawks": "ATL",
    "hornets": "CHA", "charlotte": "CHA", "cha": "CHA",
    "heat": "MIA", "miami heat": "MIA",
    "magic": "ORL", "orlando": "ORL", "orl": "ORL",
    "wizards": "WSH", "washington wizards": "WSH",
    "nuggets": "DEN", "denver": "DEN", "den": "DEN",
    "timberwolves": "MIN", "wolves": "MIN",
    "thunder": "OKC", "oklahoma city": "OKC", "okc": "OKC",
    "trail blazers": "POR", "blazers": "POR", "por": "POR", "portland": "POR",
    "jazz": "UTA", "utah": "UTA", "uta": "UTA",
    "warriors": "GSW", "golden state": "GSW", "gsw": "GSW",
    "clippers": "LAC", "la clippers": "LAC", "lac": "LAC",
    "lakers": "LAL", "la lakers": "LAL", "lal": "LAL",
    "suns": "PHX", "phoenix": "PHX", "phx": "PHX",
    "kings": "SAC", "sacramento": "SAC", "sac": "SAC",
    "spurs": "SAS", "san antonio": "SAS", "sas": "SAS",
    "mavericks": "DAL", "dallas": "DAL", "dal": "DAL",
    "rockets": "HOU", "houston rockets": "HOU",
    "grizzlies": "MEM", "memphis": "MEM", "mem": "MEM",
    "pelicans": "NOP", "new orleans": "NOP", "nop": "NOP",
}

_WNBA: dict[str, str] = {
    "sky": "CHI", "chicago sky": "CHI",
    "sun": "CON", "connecticut": "CON", "con": "CON",
    "fever": "IND", "indiana fever": "IND",
    "sparks": "LA", "los angeles sparks": "LA",
    "lynx": "MIN", "minnesota lynx": "MIN",
    "liberty": "NY", "new york liberty": "NY",
    "mystics": "WSH", "washington mystics": "WSH",
    "storm": "SEA", "seattle storm": "SEA",
    "mercury": "PHX", "phoenix mercury": "PHX",
    "aces": "LV", "las vegas": "LV", "lv": "LV",
    "dream": "ATL", "atlanta dream": "ATL",
    "wings": "DAL", "dallas wings": "DAL",
}

_NHL: dict[str, str] = {
    "bruins": "BOS", "boston bruins": "BOS",
    "sabres": "BUF", "buffalo": "BUF", "buf": "BUF",
    "red wings": "DET", "detroit red wings": "DET",
    "panthers": "FLA", "florida": "FLA", "fla": "FLA",
    "canadiens": "MTL", "montreal": "MTL", "mtl": "MTL",
    "senators": "OTT", "ottawa": "OTT", "ott": "OTT",
    "lightning": "TB", "tampa bay lightning": "TB",
    "maple leafs": "TOR", "toronto maple leafs": "TOR",
    "hurricanes": "CAR", "carolina": "CAR", "car": "CAR",
    "blue jackets": "CBJ", "columbus": "CBJ", "cbj": "CBJ",
    "devils": "NJD", "new jersey": "NJD", "njd": "NJD",
    "islanders": "NYI", "new york islanders": "NYI", "nyi": "NYI",
    "rangers": "NYR", "new york rangers": "NYR", "nyr": "NYR",
    "flyers": "PHI", "philadelphia flyers": "PHI",
    "penguins": "PIT", "pittsburgh penguins": "PIT",
    "capitals": "WSH", "washington capitals": "WSH",
    "coyotes": "ARI", "arizona coyotes": "ARI",
    "blackhawks": "CHI", "chicago blackhawks": "CHI",
    "avalanche": "COL", "colorado avalanche": "COL",
    "stars": "DAL", "dallas stars": "DAL",
    "wild": "MIN", "minnesota wild": "MIN",
    "predators": "NSH", "nashville": "NSH", "nsh": "NSH",
    "blues": "STL", "st. louis blues": "STL",
    "jets": "WPG", "winnipeg": "WPG", "wpg": "WPG",
    "ducks": "ANA", "anaheim": "ANA", "ana": "ANA",
    "flames": "CGY", "calgary": "CGY", "cgy": "CGY",
    "oilers": "EDM", "edmonton": "EDM", "edm": "EDM",
    "kings": "LAK", "los angeles kings": "LAK", "lak": "LAK",
    "sharks": "SJS", "san jose": "SJS", "sjs": "SJS",
    "canucks": "VAN", "vancouver": "VAN", "van": "VAN",
    "golden knights": "VGK", "vegas": "VGK", "vgk": "VGK",
    "kraken": "SEA", "seattle kraken": "SEA",
}

# Sport string -> league alias map
_SPORT_MAP: dict[str, dict[str, str]] = {
    "mlb":  _MLB,
    "nba":  _NBA,
    "wnba": _WNBA,
    "nhl":  _NHL,
    "baseball": _MLB,
    "basketball": _NBA,
    "hockey": _NHL,
}


def _lookup_in_map(key: str, alias_map: dict[str, str]) -> str | None:
    """Direct lookup then longest-substring fallback in a specific alias map."""
    result = alias_map.get(key)
    if result:
        return result
    best_match: str | None = None
    best_len = 0
    for alias, abbrev in alias_map.items():
        if alias in key and len(alias) > best_len:
            best_match = abbrev
            best_len = len(alias)
    return best_match


def normalize_team(name: str | None, sport: str | None = None) -> str | None:
    """
    Return the canonical abbreviation for a team name.

    When `sport` is provided, the league-specific map is tried first,
    avoiding collisions between shared nicknames across leagues
    (e.g. "Rangers" = TEX/MLB vs NYR/NHL; "Kings" = SAC/NBA vs LAK/NHL).
    Falls back to all known maps in order if sport-specific lookup fails.

    Returns None if unrecognized.
    """
    if not name:
        return None
    key = re.sub(r"\s+", " ", name.strip().lower())

    # Try sport-specific map first
    if sport:
        sport_map = _SPORT_MAP.get(sport.lower().strip())
        if sport_map:
            result = _lookup_in_map(key, sport_map)
            if result:
                return result

    # Fall back to all maps in deterministic order
    for map_name, alias_map in [("mlb", _MLB), ("nba", _NBA), ("wnba", _WNBA), ("nhl", _NHL)]:
        result = _lookup_in_map(key, alias_map)
        if result:
            return result

    return None


def build_event_id(
    sport: str | None,
    league: str | None,
    date: str | None,
    away: str | None,
    home: str | None,
) -> str:
    """
    Build a canonical event ID from league, date, and normalized team names.
    Format: {LEAGUE}:{DATE}:{AWAY_NORM}@{HOME_NORM}
    Falls back to raw lowercased input when normalization fails.
    """
    _league = (league or sport or "UNK").upper().strip()
    _date   = (date or "NODATE").strip()
    _sport  = (sport or "").lower()
    _away   = normalize_team(away, _sport) or _slug(away)
    _home   = normalize_team(home, _sport) or _slug(home)
    return f"{_league}:{_date}:{_away}@{_home}"


def _slug(s: str | None) -> str:
    if not s:
        return "UNK"
    return re.sub(r"[^a-z0-9]", "", s.lower())[:10].upper() or "UNK"


def _extract_event_teams(row: dict[str, Any]) -> tuple[str | None, str | None]:
    """
    Best-effort extraction of away/home from a row dict.
    Tries: game field ("Away @ Home"), then team/opponent fields.
    """
    game = (row.get("game") or "").strip()
    if "@" in game:
        parts = game.split("@", 1)
        away = parts[0].strip()
        home = parts[1].strip()
        return away, home
    if "vs" in game.lower():
        parts = re.split(r"\s+vs\.?\s+", game, flags=re.IGNORECASE)
        if len(parts) == 2:
            return parts[0].strip(), parts[1].strip()
    # Fallback: use team (home) and opponent (away) fields
    team     = row.get("team")
    opponent = row.get("opponent")
    return opponent, team


_SIDE_MAP: dict[str, str] = {
    "OVER":  "OVER",
    "MORE":  "OVER",   # board_intake canonical alias for OVER props
    "YES":   "YES",
    "UNDER": "UNDER",
    "LESS":  "UNDER",  # board_intake canonical alias for UNDER props
    "NO":    "NO",
}


def _extract_side(row: dict[str, Any]) -> str:
    """
    Best-effort extraction of bet direction from a row.
    Returns a normalized side string: "OVER", "UNDER", "YES", "NO", or "UNKNOWN".
    Recognizes board_intake aliases: MORE → OVER, LESS → UNDER.
    """
    for field in ("direction", "over_under", "side", "bet_direction"):
        val = row.get(field)
        if val:
            v = str(val).upper().strip()
            mapped = _SIDE_MAP.get(v)
            if mapped:
                return mapped
    return "UNKNOWN"


def group_entries_by_event(
    rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """
    Group rows by canonical event_id.

    Each row gets `_event_id` annotation.
    Returns {event_id: [row, ...]} — groups with one row are still included.
    Used for financial-exposure aggregation (all entries for same game).
    """
    groups: dict[str, list[dict[str, Any]]] = {}

    for row in rows:
        away, home = _extract_event_teams(row)
        sport   = row.get("sport")
        league  = row.get("league") or sport
        date    = row.get("slate_date") or (row.get("start_time") or "")[:10]

        event_id = build_event_id(sport, league, date, away, home)
        row["_event_id"] = event_id
        groups.setdefault(event_id, []).append(row)

    return groups


def group_entries_by_event_and_side(
    rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """
    Group rows by canonical event_id + bet side (OVER/UNDER/YES/NO).

    Key format: "{event_id}:{side}"

    Each row gets `_event_side_key` annotation.

    Used for model deduplication: multiple entries on the SAME side of the
    same game collapse to model_observation_count=1, preventing duplicate
    entries from inflating model win count, accuracy, or calibration.
    Entries on OPPOSITE sides of the same game are separate model observations.
    """
    groups: dict[str, list[dict[str, Any]]] = {}

    for row in rows:
        event_id = row.get("_event_id")
        if not event_id:
            # Re-derive if not yet set (e.g. called before group_entries_by_event)
            away, home = _extract_event_teams(row)
            sport  = row.get("sport")
            league = row.get("league") or sport
            date   = row.get("slate_date") or (row.get("start_time") or "")[:10]
            event_id = build_event_id(sport, league, date, away, home)
            row["_event_id"] = event_id

        side = _extract_side(row)
        key  = f"{event_id}:{side}"
        row["_event_side_key"] = key
        groups.setdefault(key, []).append(row)

    return groups
