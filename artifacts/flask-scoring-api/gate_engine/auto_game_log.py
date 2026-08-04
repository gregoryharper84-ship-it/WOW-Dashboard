"""
gate_engine/auto_game_log.py

Automatically fetches the last N per-game stat values for a player
before calling run_pipeline(), so the L5/L10 ledger gate can score
without the caller supplying game_log manually.

Returns a plain list of numbers — the format l5_l10_ledger expects.

Supported sports / sources:
  NBA   → nba_api (stats.nba.com, free, no auth)
  WNBA  → BallDontLie WNBA endpoint (requires balldontlie secret)
  MLB   → MLB Stats API (statsapi.mlb.com, free, no auth)
  NFL, NHL → GameLogUnavailable (no reliable free source; caller uses Claude gap-fill)

Cache: in-memory LRU keyed by (player_id, stat_key, date_str), TTL 15 min.
One cache entry per sport/player/stat/date so a 4-leg slip hitting the same
player twice doesn't double-call the API.
"""
from __future__ import annotations

import datetime
import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_CACHE: dict[str, dict] = {}    # key → {"ts": float, "values": list, "source": str, "games_fetched": int}
_CACHE_TTL = 900                 # 15 minutes


def _cache_get(key: str) -> Optional[dict]:
    entry = _CACHE.get(key)
    if entry and (time.time() - entry["ts"]) < _CACHE_TTL:
        return entry
    return None


def _cache_set(key: str, values: list, source: str, games_fetched: int) -> dict:
    result = {
        "ts":           time.time(),
        "values":       values,
        "source":       source,
        "games_fetched": games_fetched,
    }
    _CACHE[key] = result
    return result


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------

class GameLogUnavailable(Exception):
    """Raised when a game log cannot be fetched for this player/sport/stat."""
    pass


# ---------------------------------------------------------------------------
# Stat column mappings
# ---------------------------------------------------------------------------

# NBA: stat_key (from normalizer) → nba_api DataFrame column name
# These are already the same because normalizer.py uses nba_api column names.
_NBA_STAT_COLS: dict[str, str | list] = {
    "PTS":     "PTS",
    "REB":     "REB",
    "AST":     "AST",
    "STL":     "STL",
    "BLK":     "BLK",
    "FG3M":    "FG3M",
    "FTM":     "FTM",
    "TOV":     "TOV",
    # combo
    "PTS+REB+AST": ["PTS", "REB", "AST"],
    "PTS+REB":     ["PTS", "REB"],
    "PTS+AST":     ["PTS", "AST"],
    "REB+AST":     ["REB", "AST"],
}

# MLB: stat_key → statsapi gameLog split stat field name
_MLB_STAT_FIELDS: dict[str, str] = {
    "H":          "hits",
    "H_allowed":  "hits",        # pitcher hits allowed — use pitching group
    "SO":         "strikeOuts",
    "TB":         "totalBases",
    "R":          "runs",
    "RBI":        "rbi",
    "BB":         "baseOnBalls",
    "ER":         "earnedRuns",
    # combo
    "H+R+RBI":    None,          # handled specially
}

# WNBA / BallDontLie: stat_key → BDL stat field
_WNBA_STAT_FIELDS: dict[str, str] = {
    "PTS": "pts",
    "REB": "reb",
    "AST": "ast",
    "STL": "stl",
    "BLK": "blk",
    "FG3M": "fg3m",
    # combo
    "PTS+REB+AST": None,         # handled specially
}

_MLB_API = "https://statsapi.mlb.com/api/v1"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def fetch_game_log(
    player_id: str,
    sport: str,
    stat_key: str,
    target_date: Optional[str] = None,
    n_games: int = 10,
) -> dict:
    """
    Fetch last `n_games` per-game stat values for player.

    Returns:
      {
        "values":       [float, ...],    # most recent first, plain numbers
        "source":       str,             # which API provided this
        "games_fetched": int,            # how many non-DNP rows returned
        "stat_key":     str,
        "sport":        str,
        "player_id":    str,
      }

    Raises:
      GameLogUnavailable — if the sport is unsupported, stat not mapped,
                           API is down, or player has no games this season.
    """
    date_str = target_date or datetime.date.today().isoformat()
    cache_key = f"{sport}:{player_id}:{stat_key}:{date_str}"

    cached = _cache_get(cache_key)
    if cached:
        logger.debug("auto_game_log: cache hit %s", cache_key)
        return {
            "values":        cached["values"],
            "source":        cached["source"],
            "games_fetched": cached["games_fetched"],
            "stat_key":      stat_key,
            "sport":         sport,
            "player_id":     player_id,
            "cached":        True,
        }

    sport_upper = sport.upper()
    if sport_upper == "NBA":
        values, source = _fetch_nba(player_id, stat_key, date_str, n_games)
    elif sport_upper == "WNBA":
        values, source = _fetch_wnba(player_id, stat_key, date_str, n_games)
    elif sport_upper == "MLB":
        values, source = _fetch_mlb(player_id, stat_key, date_str, n_games)
    else:
        raise GameLogUnavailable(
            f"Auto game log not supported for sport={sport_upper}. "
            f"NFL/NHL require manual supply or Claude gap-fill."
        )

    entry = _cache_set(cache_key, values, source, len(values))
    return {
        "values":        values,
        "source":        source,
        "games_fetched": len(values),
        "stat_key":      stat_key,
        "sport":         sport,
        "player_id":     player_id,
        "cached":        False,
    }


# ---------------------------------------------------------------------------
# NBA fetch
# ---------------------------------------------------------------------------

def _fetch_nba(player_id: str, stat_key: str, date_str: str, n: int) -> tuple[list, str]:
    try:
        from nba_api.stats.endpoints import playergamelog as _pgl
    except ImportError:
        raise GameLogUnavailable("nba_api package not installed")

    col_def = _NBA_STAT_COLS.get(stat_key)
    if col_def is None:
        raise GameLogUnavailable(f"stat_key '{stat_key}' not mapped for NBA")

    # Determine season from target date
    date = datetime.date.fromisoformat(date_str)
    season = f"{date.year}-{str(date.year + 1)[-2:]}" if date.month >= 10 \
             else f"{date.year - 1}-{str(date.year)[-2:]}"

    try:
        gl = _pgl.PlayerGameLog(
            player_id=int(player_id),
            season=season,
            season_type_all_star="Regular Season",
            timeout=15,
        )
        df = gl.get_data_frames()[0]
    except Exception as exc:
        raise GameLogUnavailable(f"nba_api fetch error: {exc}") from exc

    if df.empty:
        raise GameLogUnavailable(
            f"No NBA game log rows for player_id={player_id} season={season}"
        )

    values: list[float] = []
    for _, row in df.iterrows():
        # Skip DNPs (MIN = 0, "0:00", or None)
        min_val = str(row.get("MIN", "0")).split(":")[0]
        try:
            if float(min_val) < 1:
                continue
        except (ValueError, TypeError):
            continue

        if isinstance(col_def, list):
            val = sum(float(row.get(c, 0) or 0) for c in col_def)
        else:
            raw = row.get(col_def)
            if raw is None:
                continue
            try:
                val = float(raw)
            except (ValueError, TypeError):
                continue

        values.append(round(val, 1))
        if len(values) >= n:
            break

    if not values:
        raise GameLogUnavailable(
            f"NBA game log returned 0 qualifying rows for player_id={player_id}"
        )

    return values, "stats.nba.com (nba_api)"


# ---------------------------------------------------------------------------
# WNBA fetch (BallDontLie)
# ---------------------------------------------------------------------------

def _fetch_wnba(player_id: str, stat_key: str, date_str: str, n: int) -> tuple[list, str]:
    import os
    bdl_key = os.environ.get("balldontlie") or os.environ.get("BALLDONTLIE_API_KEY", "")
    if not bdl_key:
        raise GameLogUnavailable("balldontlie secret not set — WNBA game log unavailable")

    col = _WNBA_STAT_FIELDS.get(stat_key)
    is_combo = (col is None and "+" in stat_key)

    if col is None and not is_combo:
        raise GameLogUnavailable(f"stat_key '{stat_key}' not mapped for WNBA")

    date = datetime.date.fromisoformat(date_str)
    season = date.year

    try:
        resp = requests.get(
            "https://api.balldontlie.io/wnba/v1/stats",
            headers={"Authorization": bdl_key},
            params={
                "player_ids[]": player_id,
                "seasons[]":    season,
                "per_page":     25,
            },
            timeout=12,
        )
    except Exception as exc:
        raise GameLogUnavailable(f"BallDontLie request failed: {exc}") from exc

    if resp.status_code != 200:
        raise GameLogUnavailable(
            f"BallDontLie returned HTTP {resp.status_code} for player_id={player_id}"
        )

    game_stats = resp.json().get("data", [])
    # Sort most-recent first
    game_stats.sort(
        key=lambda g: (g.get("game") or {}).get("date") or "",
        reverse=True,
    )

    values: list[float] = []
    for gs in game_stats:
        # Skip DNPs (min = 0 or None)
        try:
            mins = float(gs.get("min") or 0)
            if mins < 1:
                continue
        except (ValueError, TypeError):
            continue

        if is_combo:
            # PTS+REB+AST only for now
            try:
                val = float(gs.get("pts") or 0) + \
                      float(gs.get("reb") or 0) + \
                      float(gs.get("ast") or 0)
            except (ValueError, TypeError):
                continue
        else:
            raw = gs.get(col)
            if raw is None:
                continue
            try:
                val = float(raw)
            except (ValueError, TypeError):
                continue

        values.append(round(val, 1))
        if len(values) >= n:
            break

    if not values:
        raise GameLogUnavailable(
            f"BallDontLie returned 0 qualifying rows for player_id={player_id} season={season}"
        )

    return values, "api.balldontlie.io (WNBA)"


# ---------------------------------------------------------------------------
# MLB fetch
# ---------------------------------------------------------------------------

def _fetch_mlb(player_id: str, stat_key: str, date_str: str, n: int) -> tuple[list, str]:
    date = datetime.date.fromisoformat(date_str)
    season = date.year

    # Determine stat group from stat_key
    pitcher_keys = {"H_allowed", "ER", "BB"}
    group = "pitching" if stat_key in pitcher_keys else "hitting"

    field = _MLB_STAT_FIELDS.get(stat_key)
    is_combo = (stat_key == "H+R+RBI")

    if field is None and not is_combo:
        raise GameLogUnavailable(f"stat_key '{stat_key}' not mapped for MLB")

    try:
        resp = requests.get(
            f"{_MLB_API}/people/{player_id}/stats",
            params={"stats": "gameLog", "group": group, "season": season},
            timeout=12,
            headers={"User-Agent": "WOW/1.0"},
        )
    except Exception as exc:
        raise GameLogUnavailable(f"MLB Stats API request failed: {exc}") from exc

    if resp.status_code != 200:
        raise GameLogUnavailable(
            f"MLB Stats API returned HTTP {resp.status_code} for player_id={player_id}"
        )

    splits = resp.json().get("stats", [{}])[0].get("splits", [])
    if not splits:
        raise GameLogUnavailable(
            f"No MLB game log splits for player_id={player_id} season={season}"
        )

    # Reverse so most-recent is first (MLB Stats API returns chronological)
    splits = list(reversed(splits))

    values: list[float] = []
    for split in splits:
        stat = split.get("stat", {})

        if is_combo:
            try:
                val = float(stat.get("hits") or 0) + \
                      float(stat.get("runs") or 0) + \
                      float(stat.get("rbi") or 0)
            except (ValueError, TypeError):
                continue
        else:
            raw = stat.get(field)
            if raw is None:
                continue
            try:
                val = float(raw)
            except (ValueError, TypeError):
                continue

        values.append(round(val, 1))
        if len(values) >= n:
            break

    if not values:
        raise GameLogUnavailable(
            f"MLB game log returned 0 qualifying rows for player_id={player_id}"
        )

    return values, "statsapi.mlb.com (MLB Stats API)"
