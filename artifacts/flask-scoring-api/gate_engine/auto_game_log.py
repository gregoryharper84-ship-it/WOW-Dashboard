"""
gate_engine/auto_game_log.py

Automatically fetches the last N per-game stat values for a player
before calling run_pipeline(), so the L5/L10 ledger gate can score
without the caller supplying game_log manually.

Returns a plain list of numbers — the format l5_l10_ledger expects.

Supported sports / sources:
  NBA    → nba_api (stats.nba.com, free, no auth)
  WNBA   → BallDontLie WNBA endpoint (requires balldontlie secret)
  MLB    → MLB Stats API (statsapi.mlb.com, free, no auth)
  NFL    → nfl_data_py (nflfastR / GitHub, free, no auth)
  TENNIS → Jeff Sackmann ATP/WTA CSVs (GitHub, free, no auth); ATP/WTA main-draw only
  NHL    → GameLogUnavailable (no reliable free source; caller uses Claude gap-fill)

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

# Fantasy Score stat-key set — triggers multi-column derivation path
_FS_STAT_KEYS = {"FANTASY_SCORE", "FANTASY_SCORE_HIT", "FANTASY_SCORE_PIT"}

_CACHE: dict[str, dict] = {}    # key → {"ts", "values", "source", "games_fetched", "tour_level"}
_CACHE_TTL = 900                 # 15 minutes


def _cache_get(key: str) -> Optional[dict]:
    entry = _CACHE.get(key)
    if entry and (time.time() - entry["ts"]) < _CACHE_TTL:
        return entry
    return None


def _cache_set(key: str, values: list, source: str, games_fetched: int,
               tour_level: Optional[str] = None,
               meta: Optional[dict] = None) -> dict:
    result: dict = {
        "ts":            time.time(),
        "values":        values,
        "source":        source,
        "games_fetched": games_fetched,
    }
    if tour_level is not None:
        result["tour_level"] = tour_level
    if meta:
        result["meta"] = meta   # game-level metadata (game_date, opponent) from MLB splits
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
#
# FIX-1: Added "K" → "strikeOuts".
# normalizer.py maps "pitcher strikeouts"/"strikeouts"/"k" → stat_key "K".
# "SO" was already present as an alias; both now point to the same MLB field.
# Prior to this fix, _MLB_STAT_FIELDS.get("K") returned None, which caused
# _fetch_mlb to raise GameLogUnavailable before any HTTP request was made.
_MLB_STAT_FIELDS: dict[str, str] = {
    "H":          "hits",
    "H_allowed":  "hits",        # pitcher hits allowed — use pitching split group
    "K":          "strikeOuts",  # normalizer.py primary key for pitcher strikeouts
    "SO":         "strikeOuts",  # legacy/alternate key; same MLB field
    # Pitching outs (recorded outs) — normalizer.py maps "pitching outs" → "OUTS".
    # MLB Stats API pitching split field: "outs" (integer; 4.1 IP → outs=13, 6.0 IP → outs=18).
    # NOT "recordedOuts" — that field is absent from gameLog splits.
    # "inningsPitched" is also available ("4.1") but "outs" is already the integer
    # count we need, removing the fractional-IP parsing step.
    "OUTS":       "outs",
    "TB":         "totalBases",
    "R":          "runs",
    "RBI":        "rbi",
    "BB":         "baseOnBalls",
    "ER":         "earnedRuns",
    # Plate appearances — batter stat; MLB Stats API hitting split field.
    # normalizer.py maps "plate appearances"/"pa"/"plate_appearances" → stat_key "PA".
    # "PLATE_APPEARANCES" alias registered for belt-and-suspenders lookups.
    "PA":               "plateAppearances",
    "PLATE_APPEARANCES": "plateAppearances",
    # combo
    "H+R+RBI":    None,          # handled specially in _fetch_mlb
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
        hit: dict = {
            "values":        cached["values"],
            "source":        cached["source"],
            "games_fetched": cached["games_fetched"],
            "stat_key":      stat_key,
            "sport":         sport,
            "player_id":     player_id,
            "cached":        True,
        }
        if "tour_level" in cached:
            hit["tour_level"] = cached["tour_level"]
        # Re-expose cached metadata (game_date, opponent) so build_auto_enrichment
        # can use them even on repeated calls within the cache TTL.
        if "meta" in cached:
            hit.update(cached["meta"])
        return hit

    sport_upper = sport.upper()
    stat_key_upper = stat_key.upper()
    tour_level: str | None = None  # populated for TENNIS only

    # ── Fantasy Score composite props — multi-column derivation ──────────────
    # FANTASY_SCORE / FANTASY_SCORE_HIT / FANTASY_SCORE_PIT require fetching
    # all component columns and applying the per-sport formula.  Routed BEFORE
    # the single-stat dispatch to avoid raising "stat_key not mapped" errors.
    # TENNIS is excluded: it handles FANTASY_SCORE through its own _fetch_tennis
    # path (Jeff Sackmann data + tennis-specific formula).
    if stat_key_upper in _FS_STAT_KEYS and sport_upper in ("NBA", "WNBA", "NFL", "MLB"):
        if sport_upper == "NBA":
            values, source = _fetch_nba_fantasy(player_id, date_str, n_games)
        elif sport_upper == "WNBA":
            values, source = _fetch_wnba_fantasy(player_id, date_str, n_games)
        elif sport_upper == "NFL":
            values, source = _fetch_nfl_fantasy(player_id, date_str, n_games)
        elif sport_upper == "MLB":
            if stat_key_upper == "FANTASY_SCORE_PIT":
                values, source = _fetch_mlb_pitcher_fantasy(player_id, date_str, n_games)
            else:  # FANTASY_SCORE or FANTASY_SCORE_HIT
                values, source = _fetch_mlb_hitter_fantasy(player_id, date_str, n_games)
        else:
            raise GameLogUnavailable(
                f"FANTASY_SCORE not supported for sport={sport_upper}. "
                f"Supported: NBA, WNBA, NFL, MLB."
            )
    elif sport_upper == "NBA":
        values, source = _fetch_nba(player_id, stat_key, date_str, n_games)
    elif sport_upper == "WNBA":
        values, source = _fetch_wnba(player_id, stat_key, date_str, n_games)
    elif sport_upper == "MLB":
        values, source, _game_meta = _fetch_mlb(player_id, stat_key, date_str, n_games)
    elif sport_upper == "NFL":
        values, source = _fetch_nfl(player_id, stat_key, date_str, n_games)
    elif sport_upper == "TENNIS":
        # 3-tuple: values, source, tour_level
        # tour_level distinguishes ATP_MAIN_DRAW / WTA_MAIN_DRAW from UNKNOWN_TIER
        # so downstream gates can emit "no data for this tour tier" rather than
        # a generic NO_GAME_LOG_PROVIDED when ITF/Challenger players fail closed.
        values, source, tour_level = _fetch_tennis(player_id, stat_key, date_str, n_games)
    else:
        raise GameLogUnavailable(
            f"Auto game log not supported for sport={sport_upper}. "
            f"NHL requires manual supply or Claude gap-fill."
        )

    # Collect game-level metadata returned only by MLB path (other sports return empty dict)
    _game_meta: dict = locals().get("_game_meta") or {}

    _cache_set(cache_key, values, source, len(values),
               tour_level=tour_level, meta=_game_meta or None)
    result = {
        "values":        values,
        "source":        source,
        "games_fetched": len(values),
        "stat_key":      stat_key,
        "sport":         sport,
        "player_id":     player_id,
        "cached":        False,
    }
    if tour_level is not None:
        result["tour_level"] = tour_level
    # Merge MLB split metadata (game_date, opponent) into the result so
    # build_auto_enrichment can populate those contract fields without a
    # second API call.
    if _game_meta:
        result.update(_game_meta)
    return result


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

def _fetch_mlb(player_id: str, stat_key: str, date_str: str, n: int) -> tuple[list, str, dict]:
    """
    Returns (values, source_label, game_metadata).

    game_metadata contains at most:
      {"game_date": "YYYY-MM-DD", "opponent": "Team Name"}
    extracted from the most-recent split.  Missing fields are omitted
    (not set to None) so callers can safely do `if meta.get("game_date")`.
    """
    date = datetime.date.fromisoformat(date_str)
    season = date.year

    # FIX-1: "K" and "SO" are pitcher strikeout stat keys → use the pitching
    # split group.  Prior code only listed H_allowed/ER/BB as pitcher keys,
    # so "K" / "SO" incorrectly queried the hitting split (which has no
    # strikeOuts field), producing 0 qualifying rows.
    # "OUTS" added: normalizer.py maps "pitching outs" → stat_key "OUTS";
    # recordedOuts lives in the pitching split group, not the hitting split.
    pitcher_keys = {"H_allowed", "ER", "BB", "K", "SO", "OUTS"}
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

    # Extract game-level metadata from the most-recent split (index 0 after
    # reversal).  These fields are used by build_auto_enrichment to populate
    # the `opponent` and `game_date` enrichment contract fields without needing
    # a second API call.  Keys are omitted (not set to None) when absent so
    # callers can safely do `if meta.get("game_date")`.
    _meta: dict = {}
    if splits:
        _recent_split = splits[0]
        _split_date = (_recent_split.get("date") or "").strip()
        if _split_date:
            _meta["game_date"] = _split_date
        _opp_name = (_recent_split.get("opponent") or {}).get("name", "").strip()
        if _opp_name:
            _meta["opponent"] = _opp_name

    return values, "statsapi.mlb.com (MLB Stats API)", _meta


# ---------------------------------------------------------------------------
# Fantasy Score fetchers — multi-column, apply formula per game row
# ---------------------------------------------------------------------------
# Each function fetches ALL component columns in one API call and applies the
# per-sport Fantasy Score formula from gate_engine.fantasy_score.
# The formula is PROVISIONAL/UNVALIDATED — see fantasy_score.py for details.
# ---------------------------------------------------------------------------

def _fetch_nba_fantasy(player_id: str, date_str: str, n: int) -> tuple[list, str]:
    """NBA Fantasy Score: PTS×1.0 + REB×1.2 + AST×1.5 + STL×3.0 + BLK×3.0 + TOV×−1.0"""
    try:
        from nba_api.stats.endpoints import playergamelog as _pgl
    except ImportError:
        raise GameLogUnavailable("nba_api package not installed")
    from gate_engine.fantasy_score import derive_nba_wnba_row

    date = datetime.date.fromisoformat(date_str)
    season = (f"{date.year}-{str(date.year + 1)[-2:]}" if date.month >= 10
              else f"{date.year - 1}-{str(date.year)[-2:]}")

    try:
        gl = _pgl.PlayerGameLog(
            player_id=int(player_id),
            season=season,
            season_type_all_star="Regular Season",
            timeout=15,
        )
        df = gl.get_data_frames()[0]
    except Exception as exc:
        raise GameLogUnavailable(f"nba_api (FS) fetch error: {exc}") from exc

    if df.empty:
        raise GameLogUnavailable(f"No NBA game log for player_id={player_id} season={season}")

    values: list[float] = []
    for _, row in df.iterrows():
        min_val = str(row.get("MIN", "0")).split(":")[0]
        try:
            if float(min_val) < 1:
                continue
        except (ValueError, TypeError):
            continue
        try:
            fs = derive_nba_wnba_row(row.to_dict())
        except Exception:
            continue
        values.append(round(fs, 2))
        if len(values) >= n:
            break

    if not values:
        raise GameLogUnavailable(f"NBA FS: 0 qualifying rows for player_id={player_id}")
    return values, "stats.nba.com/nba_api [FANTASY_SCORE]"


def _fetch_wnba_fantasy(player_id: str, date_str: str, n: int) -> tuple[list, str]:
    """WNBA Fantasy Score (same weights as NBA — WNBA_WEIGHTS_ASSUMED_SAME_AS_NBA)."""
    import os
    bdl_key = os.environ.get("balldontlie") or os.environ.get("BALLDONTLIE_API_KEY", "")
    if not bdl_key:
        raise GameLogUnavailable("balldontlie secret not set — WNBA FS unavailable")
    from gate_engine.fantasy_score import derive_nba_wnba_row

    date = datetime.date.fromisoformat(date_str)
    season = date.year

    try:
        resp = requests.get(
            "https://api.balldontlie.io/wnba/v1/stats",
            headers={"Authorization": bdl_key},
            params={"player_ids[]": player_id, "seasons[]": season, "per_page": 25},
            timeout=12,
        )
    except Exception as exc:
        raise GameLogUnavailable(f"BallDontLie (FS) request failed: {exc}") from exc

    if resp.status_code != 200:
        raise GameLogUnavailable(f"BallDontLie (FS) HTTP {resp.status_code} for player_id={player_id}")

    game_stats = resp.json().get("data", [])
    game_stats.sort(key=lambda g: (g.get("game") or {}).get("date") or "", reverse=True)

    values: list[float] = []
    for gs in game_stats:
        try:
            mins = float(gs.get("min") or 0)
            if mins < 1:
                continue
        except (ValueError, TypeError):
            continue
        try:
            fs = derive_nba_wnba_row(gs)
        except Exception:
            continue
        values.append(round(fs, 2))
        if len(values) >= n:
            break

    if not values:
        raise GameLogUnavailable(f"WNBA FS: 0 qualifying rows for player_id={player_id}")
    return values, "api.balldontlie.io/WNBA [FANTASY_SCORE]"


def _fetch_nfl_fantasy(player_id: str, date_str: str, n: int) -> tuple[list, str]:
    """NFL Fantasy Score — all component stats in one season load."""
    from gate_engine.fantasy_score import derive_nfl_row

    try:
        import nfl_data_py as nfl
    except ImportError:
        raise GameLogUnavailable("nfl_data_py package not installed")

    from gate_engine.nfl_game_log import _nfl_season_from_date, _get_season_df

    date = datetime.date.fromisoformat(date_str)
    season = _nfl_season_from_date(date)
    df = _get_season_df(season)

    # Player lookup (ID or display name)
    player_df = df[df["player_id"] == player_id]
    if player_df.empty:
        name_lower = player_id.lower()
        mask = df["player_display_name"].str.lower().str.contains(name_lower, regex=False, na=False)
        player_df = df[mask]
    if player_df.empty:
        fallback = season - 1
        df2 = _get_season_df(fallback)
        player_df = df2[df2["player_display_name"].str.lower().str.contains(
            player_id.lower(), regex=False, na=False
        )]
    if player_df.empty:
        raise GameLogUnavailable(f"NFL FS: player '{player_id}' not found in season {season}")

    # Sort most-recent week first
    if "week" in player_df.columns:
        player_df = player_df.sort_values("week", ascending=False)

    values: list[float] = []
    for _, row in player_df.iterrows():
        row_dict = {
            "pass_yds":    row.get("passing_yards", 0),
            "pass_td":     row.get("passing_tds", 0),
            "int":         row.get("interceptions", 0),
            "rush_yds":    row.get("rushing_yards", 0),
            "rush_td":     row.get("rushing_tds", 0),
            "rec_yds":     row.get("receiving_yards", 0),
            "rec_td":      row.get("receiving_tds", 0),
            "rec":         row.get("receptions", 0),
            "fumbles_lost": row.get("fumbles_lost", 0),
        }
        try:
            fs = derive_nfl_row(row_dict)
        except Exception:
            continue
        values.append(round(fs, 2))
        if len(values) >= n:
            break

    if not values:
        raise GameLogUnavailable(f"NFL FS: 0 qualifying rows for player '{player_id}'")
    return values, "nfl_data_py/nflfastR [FANTASY_SCORE]"


def _fetch_mlb_hitter_fantasy(player_id: str, date_str: str, n: int) -> tuple[list, str]:
    """MLB Hitter Fantasy Score: 1B×3 + 2B×5 + 3B×8 + HR×10 + R×2 + RBI×2 + BB×2 + HBP×2 + SB×5"""
    from gate_engine.fantasy_score import derive_mlb_hitter_row

    date = datetime.date.fromisoformat(date_str)
    season = date.year

    try:
        resp = requests.get(
            f"{_MLB_API}/people/{player_id}/stats",
            params={"stats": "gameLog", "group": "hitting", "season": season},
            timeout=12,
            headers={"User-Agent": "WOW/1.0"},
        )
    except Exception as exc:
        raise GameLogUnavailable(f"MLB Stats API (hitter FS) failed: {exc}") from exc

    if resp.status_code != 200:
        raise GameLogUnavailable(f"MLB Stats API HTTP {resp.status_code} for player_id={player_id}")

    splits = resp.json().get("stats", [{}])[0].get("splits", [])
    if not splits:
        raise GameLogUnavailable(f"No MLB hitter game log for player_id={player_id} season={season}")

    splits = list(reversed(splits))  # most-recent first
    values: list[float] = []
    for split in splits:
        try:
            fs = derive_mlb_hitter_row(split.get("stat", {}))
        except Exception:
            continue
        values.append(round(fs, 2))
        if len(values) >= n:
            break

    if not values:
        raise GameLogUnavailable(f"MLB hitter FS: 0 qualifying rows for player_id={player_id}")
    return values, "statsapi.mlb.com [FANTASY_SCORE_HIT]"


def _fetch_mlb_pitcher_fantasy(player_id: str, date_str: str, n: int) -> tuple[list, str]:
    """MLB Pitcher Fantasy Score: W×6 + QS×4 + K×3 + Outs×1 + ER×−3 (QS derived)."""
    from gate_engine.fantasy_score import derive_mlb_pitcher_row

    date = datetime.date.fromisoformat(date_str)
    season = date.year

    try:
        resp = requests.get(
            f"{_MLB_API}/people/{player_id}/stats",
            params={"stats": "gameLog", "group": "pitching", "season": season},
            timeout=12,
            headers={"User-Agent": "WOW/1.0"},
        )
    except Exception as exc:
        raise GameLogUnavailable(f"MLB Stats API (pitcher FS) failed: {exc}") from exc

    if resp.status_code != 200:
        raise GameLogUnavailable(f"MLB Stats API HTTP {resp.status_code} for player_id={player_id}")

    splits = resp.json().get("stats", [{}])[0].get("splits", [])
    if not splits:
        raise GameLogUnavailable(f"No MLB pitcher game log for player_id={player_id} season={season}")

    splits = list(reversed(splits))  # most-recent first
    values: list[float] = []
    for split in splits:
        try:
            fs = derive_mlb_pitcher_row(split.get("stat", {}))
        except Exception:
            continue
        values.append(round(fs, 2))
        if len(values) >= n:
            break

    if not values:
        raise GameLogUnavailable(f"MLB pitcher FS: 0 qualifying rows for player_id={player_id}")
    return values, "statsapi.mlb.com [FANTASY_SCORE_PIT]"


# ---------------------------------------------------------------------------
# NFL fetch (nfl_data_py)
# ---------------------------------------------------------------------------

def _fetch_nfl(player_id: str, stat_key: str, date_str: str, n: int) -> tuple[list, str]:
    try:
        from gate_engine.nfl_game_log import fetch as _nfl_fetch
    except ImportError as exc:
        raise GameLogUnavailable(f"nfl_game_log module unavailable: {exc}") from exc

    try:
        return _nfl_fetch(player_id, stat_key, date_str, n)
    except (RuntimeError, KeyError) as exc:
        raise GameLogUnavailable(f"NFL game log: {exc}") from exc
    except Exception as exc:
        raise GameLogUnavailable(f"NFL game log unexpected error: {exc}") from exc


# ---------------------------------------------------------------------------
# Tennis fetch (Jeff Sackmann ATP/WTA CSVs)
# ---------------------------------------------------------------------------

def _fetch_tennis(player_id: str, stat_key: str, date_str: str, n: int) -> tuple[list, str, str]:
    """
    Returns (values, source_label, tour_level).

    tour_level is one of the TOUR_LEVEL_* constants from tennis_game_log.
    It is surfaced on the fetch_game_log result dict so gates can distinguish
    "no data for this tour tier" from a generic unavailable error.

    ITF/Challenger failures raise GameLogUnavailable with a message that
    begins with the NO_DATA_FOR_TOUR_TIER prefix from tennis_game_log.
    """
    try:
        from gate_engine.tennis_game_log import fetch as _tennis_fetch, TOUR_LEVEL_UNKNOWN
    except ImportError as exc:
        raise GameLogUnavailable(f"tennis_game_log module unavailable: {exc}") from exc

    try:
        values, source, tour_level = _tennis_fetch(player_id, stat_key, date_str, n)
        return values, source, tour_level
    except (RuntimeError, KeyError) as exc:
        raise GameLogUnavailable(f"Tennis game log: {exc}") from exc
    except Exception as exc:
        raise GameLogUnavailable(f"Tennis game log unexpected error: {exc}") from exc


# ---------------------------------------------------------------------------
# BallDontLie rich player package — TRUSTED_STRUCTURED_STATS (A- grade)
# ---------------------------------------------------------------------------

def fetch_bdl_player_package(
    player_id:   str,
    sport:       str,
    stat_key:    str,
    season:      int | None = None,
    n_games:     int        = 10,
    target_date: str | None = None,
) -> dict:
    """
    Fetch a comprehensive BallDontLie player package (TRUSTED_STRUCTURED_STATS).

    Returns a dict containing:
      "game_log"      : list[float]          — WOW canonical, most recent first
      "box_score_log" : list[dict]           — WOW canonical, most recent first
      "minutes_stats" : dict                 — mean/variance/cv/role_stability
      "acquisition_status": str              — BDLStatus constant
      "provenance"    : dict                 — full BDLProvenance
      "notes"         : list[str]
      "source"        : "api.balldontlie.io"
      "source_grade"  : "A-"
      "source_type"   : "balldontlie_api"

    Raises GameLogUnavailable when credentials are absent or the sport
    is unsupported. Never raises on HTTP failures — returns acquisition_status.

    Source grade A- (TRUSTED_STRUCTURED_STATS): direct API with timestamp,
    below official league feeds, above B-grade stat-site reconstruction.

    can_execute=False unconditional in the BDL layer.
    """
    sport_upper = sport.upper().strip()

    if sport_upper in ("NBA", "WNBA"):
        try:
            from gate_engine.balldontlie.nba_wnba import fetch_player_package
        except ImportError as exc:
            raise GameLogUnavailable(
                f"BDL NBA/WNBA module unavailable: {exc}"
            ) from exc

        package = fetch_player_package(
            player_id   = player_id,
            sport       = sport_upper,
            season      = season,
            n_games     = n_games,
            target_date = target_date,
        )

    elif sport_upper == "MLB":
        # Route MLB to pitcher vs batter based on stat_key
        _PITCHER_KEYS = {"IP", "OUTS", "K", "BB", "BF", "PC", "PITCHER_OUTS",
                         "FANTASY_SCORE_PIT"}
        try:
            if stat_key.upper() in _PITCHER_KEYS:
                from gate_engine.balldontlie.mlb import fetch_pitcher_package
                package = fetch_pitcher_package(
                    player_id   = player_id,
                    season      = season,
                    n_games     = n_games,
                    target_date = target_date,
                )
            else:
                from gate_engine.balldontlie.mlb import fetch_batter_package
                package = fetch_batter_package(
                    player_id   = player_id,
                    season      = season,
                    n_games     = n_games,
                    target_date = target_date,
                )
        except ImportError as exc:
            raise GameLogUnavailable(
                f"BDL MLB module unavailable: {exc}"
            ) from exc

    else:
        raise GameLogUnavailable(
            f"BDL package not supported for sport={sport_upper}. "
            f"Supported: NBA, WNBA, MLB."
        )

    return {
        "game_log":         package.wow_game_log(stat_key, n_games),
        "box_score_log":    package.wow_box_score_log(n_games),
        "minutes_stats":    package.minutes_stats(),
        "acquisition_status": package.acquisition_status,
        "provenance":       package.provenance.to_dict(),
        "notes":            package.notes,
        "source":           "api.balldontlie.io",
        "source_grade":     "A-",
        "source_type":      "balldontlie_api",
    }
