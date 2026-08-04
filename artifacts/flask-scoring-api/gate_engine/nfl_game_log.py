"""
gate_engine/nfl_game_log.py

Fetch per-game NFL player stats using nfl_data_py (backed by nflfastR / GitHub).

Source:    nfl_data_py.import_weekly_data()
Coverage:  2000–present; all regular-season and playoff weeks
Auth:      None required; downloads CSV from GitHub on first call per season
Cache:     Module-level DataFrame cache (one DataFrame per season, TTL 15 min)

Stat keys supported
-------------------
Counting (Poisson-appropriate):
  PASS_YDS   passing_yards
  RUSH_YDS   rushing_yards
  REC_YDS    receiving_yards
  REC        receptions
  TARGETS    targets
  PASS_ATT   attempts
  PASS_CMP   completions
  SACK       sacks
  FPTS       fantasy_points        (standard scoring)
  FPTS_PPR   fantasy_points_ppr

Near-binary / Bernoulli (line ≤ 1.5):
  PASS_TD    passing_tds
  RUSH_TD    rushing_tds
  REC_TD     receiving_tds
  TD         rushing_tds + receiving_tds + passing_tds   (any touchdown)
  INT        interceptions

Player lookup
-------------
`player_id` is treated first as an nfl_data_py player_id (e.g. "00-0039163").
If no exact match, falls back to case-insensitive full-name search against
`player_display_name`.  Partial names ("Mahomes", "P.Mahomes") are supported.
"""
from __future__ import annotations

import datetime
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Season-level DataFrame cache
# ---------------------------------------------------------------------------

_DF_CACHE: dict[int, dict] = {}   # season → {"ts": float, "df": DataFrame}
_DF_TTL = 900                      # 15 min


def _get_season_df(season: int):
    """Return (and cache) the weekly DataFrame for `season`."""
    entry = _DF_CACHE.get(season)
    if entry and (time.time() - entry["ts"]) < _DF_TTL:
        return entry["df"]

    try:
        import nfl_data_py as _nfl  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError("nfl-data-py package not installed") from exc

    try:
        df = _nfl.import_weekly_data([season])
    except Exception as exc:
        raise RuntimeError(f"nfl_data_py.import_weekly_data({season}) failed: {exc}") from exc

    _DF_CACHE[season] = {"ts": time.time(), "df": df}
    logger.debug("nfl_game_log: loaded season %d (%d rows)", season, len(df))
    return df


# ---------------------------------------------------------------------------
# Stat-key → column mapping
# ---------------------------------------------------------------------------

# Single-column stats
_STAT_COLS: dict[str, str] = {
    "PASS_YDS":  "passing_yards",
    "RUSH_YDS":  "rushing_yards",
    "REC_YDS":   "receiving_yards",
    "REC":       "receptions",
    "TARGETS":   "targets",
    "PASS_ATT":  "attempts",
    "PASS_CMP":  "completions",
    "SACK":      "sacks",
    "PASS_TD":   "passing_tds",
    "RUSH_TD":   "rushing_tds",
    "REC_TD":    "receiving_tds",
    "INT":       "interceptions",
    "FPTS":      "fantasy_points",
    "FPTS_PPR":  "fantasy_points_ppr",
}

# Combo stats: TD = any_td (sum of all TD columns)
_TD_COLS = ["passing_tds", "rushing_tds", "receiving_tds", "special_teams_tds"]


def _nfl_season_from_date(date: datetime.date) -> int:
    """
    NFL season year from a calendar date.

    NFL regular season: week 1 ≈ early September through Feb of the following
    calendar year (playoffs).  Season is labeled by the September start year.

    March–August = offseason; return prior season so we get the most recent
    game data rather than an empty future season.
    """
    if date.month >= 9:
        return date.year
    else:
        # Jan, Feb, Mar–Aug all belong to the previous season year
        return date.year - 1


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def fetch(
    player_id: str,
    stat_key: str,
    date_str: str,
    n_games: int = 10,
) -> tuple[list[float], str]:
    """
    Return (values, source_label) for the last `n_games` weeks for the player.

    `player_id` is an nfl_data_py player_id OR a display name (case-insensitive,
    partial match accepted).

    Raises RuntimeError / KeyError on unrecoverable errors so the caller
    (auto_game_log.py) can re-raise as GameLogUnavailable.
    """
    stat_key_upper = stat_key.upper().strip()
    is_combo_td = (stat_key_upper == "TD")

    if stat_key_upper not in _STAT_COLS and not is_combo_td:
        raise KeyError(f"stat_key '{stat_key}' not mapped for NFL")

    date = datetime.date.fromisoformat(date_str)
    season = _nfl_season_from_date(date)

    df = _get_season_df(season)

    # --- player lookup: ID first, then display name ---
    player_df = df[df["player_id"] == player_id]
    if player_df.empty:
        name_lower = player_id.lower()
        mask = df["player_display_name"].str.lower().str.contains(
            name_lower, regex=False, na=False
        )
        player_df = df[mask]

    if player_df.empty:
        # Try prior season (e.g., late January before season wraps)
        fallback_season = season - 1
        logger.debug("nfl_game_log: no data for season %d, trying %d", season, fallback_season)
        try:
            df2 = _get_season_df(fallback_season)
            player_df2 = df2[df2["player_id"] == player_id]
            if player_df2.empty:
                name_lower = player_id.lower()
                mask2 = df2["player_display_name"].str.lower().str.contains(
                    name_lower, regex=False, na=False
                )
                player_df2 = df2[mask2]
            if not player_df2.empty:
                player_df = player_df2
                df = df2
        except Exception:
            pass

    if player_df.empty:
        raise KeyError(
            f"No NFL weekly data found for player_id/name='{player_id}' "
            f"season={season}"
        )

    # Sort descending by (season, week) so most-recent game is first
    player_df = player_df.sort_values(
        ["season", "week"], ascending=[False, False]
    )

    # Filter to regular season + postseason (exclude preseason week 0)
    player_df = player_df[player_df["week"] >= 1]

    values: list[float] = []

    for _, row in player_df.iterrows():
        try:
            if is_combo_td:
                val = sum(
                    float(row.get(c) or 0)
                    for c in _TD_COLS
                    if c in row.index
                )
            else:
                col = _STAT_COLS[stat_key_upper]
                raw = row.get(col)
                if raw is None:
                    continue
                val = float(raw)
        except (TypeError, ValueError):
            continue

        values.append(round(val, 1))
        if len(values) >= n_games:
            break

    if not values:
        raise KeyError(
            f"NFL weekly log returned 0 qualifying rows for "
            f"player='{player_id}' season={season} stat={stat_key}"
        )

    player_display = player_df.iloc[0].get("player_display_name", player_id)
    source = f"nfl_data_py (nflfastR) — {player_display} season {season}"
    return values, source
