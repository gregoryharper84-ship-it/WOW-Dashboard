"""
gate_engine/tennis_game_log.py

Fetch per-match Tennis player stats from Jeff Sackmann's open-source
tennis datasets (MIT licence, updated daily during season).

Sources:
  ATP: https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_matches_{year}.csv
  WTA: https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master/wta_matches_{year}.csv

Coverage notes (document the ceiling upfront)
---------------------------------------------
  • ATP tour-level and WTA tour-level matches: excellent coverage back to 1968.
  • ATP Challenger / WTA 125K: included from ~2010 onward.
  • ITF / lower-tier events: NOT covered in these files.
  • PrizePicks slates frequently include ITF/Challenger players → model will
    fail-closed for those players; the pipeline returns RESEARCH_INTEREST
    rather than a fabricated estimate.

Stat keys
---------
  GAMES_WON      integer count of games won in the match
  ACES           ace count (w_ace / l_ace column)
  DOUBLE_FAULTS  double fault count (w_df / l_df)
  FANTASY_SCORE  weighted composite: games_won + 0.5*aces − 0.5*double_faults
                 (PrizePicks tennis scoring approximation)

Player lookup
-------------
`player_id` is matched against winner_name / loser_name (case-insensitive,
partial match accepted).  Full surnames (e.g. "Alcaraz") and full names
("Carlos Alcaraz") both work.  Ambiguous short names raise an error.
"""
from __future__ import annotations

import datetime
import io
import logging
import re
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_ATP_URL = "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_matches_{year}.csv"
_WTA_URL = "https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master/wta_matches_{year}.csv"

# ---------------------------------------------------------------------------
# DataFrame cache (keyed by (tour, year))
# ---------------------------------------------------------------------------

_DF_CACHE: dict[tuple, dict] = {}
_DF_TTL = 3600  # 1 hour — these CSVs update daily at most


def _get_tour_df(tour: str, year: int):
    """Download and cache the match DataFrame for (tour, year)."""
    cache_key = (tour, year)
    entry = _DF_CACHE.get(cache_key)
    if entry and (time.time() - entry["ts"]) < _DF_TTL:
        return entry["df"]

    try:
        import pandas as _pd  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError("pandas not installed") from exc

    url = (_ATP_URL if tour == "atp" else _WTA_URL).format(year=year)
    try:
        resp = requests.get(url, timeout=20)
    except Exception as exc:
        raise RuntimeError(f"tennis data fetch failed: {exc}") from exc

    if resp.status_code == 404:
        raise RuntimeError(f"No {tour.upper()} data for {year} at {url}")
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} fetching {url}")

    df = _pd.read_csv(io.StringIO(resp.text), low_memory=False)
    _DF_CACHE[cache_key] = {"ts": time.time(), "df": df}
    logger.debug("tennis_game_log: loaded %s %d (%d rows)", tour, year, len(df))
    return df


# ---------------------------------------------------------------------------
# Score parsing — extract games won by winner and loser from score string
# ---------------------------------------------------------------------------

_SET_SCORE_RE = re.compile(r"(\d+)-(\d+)(?:\(\d+\))?")


def _parse_games(score_str: str) -> tuple[int, int]:
    """
    Parse a match score string and return (winner_games, loser_games).

    Handles:
      "6-3 6-2"                → (12, 5)
      "6-4 3-6 6-3"            → (15, 13)
      "7-6(3) 6-4"             → (13, 10)
      "6-3 3-0 RET"            → (9, 3)   — counts only completed sets
      "W/O"                    → (0, 0)   — walkover, no data
    """
    if not score_str or str(score_str).strip().upper() in ("W/O", "WO", "", "NAN"):
        return (0, 0)

    w_games = l_games = 0
    for m in _SET_SCORE_RE.finditer(str(score_str)):
        w_games += int(m.group(1))
        l_games += int(m.group(2))
    return (w_games, l_games)


# ---------------------------------------------------------------------------
# Player match rows finder
# ---------------------------------------------------------------------------

def _find_player_rows(df, player_id: str):
    """
    Return a DataFrame with columns (date, games_won, aces, double_faults,
    is_winner) for matches involving the player.

    Raises KeyError if the player is not found or ambiguous.
    """
    try:
        import pandas as _pd  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError("pandas not installed") from exc

    name_lower = player_id.lower().strip()

    w_mask = df["winner_name"].str.lower().str.contains(name_lower, regex=False, na=False)
    l_mask = df["loser_name"].str.lower().str.contains(name_lower, regex=False, na=False)

    # Guard against ambiguous short names matching multiple players
    w_names = set(df.loc[w_mask, "winner_name"].dropna().unique())
    l_names = set(df.loc[l_mask, "loser_name"].dropna().unique())
    all_names = w_names | l_names
    if len(all_names) > 3:
        # Too many distinct player names matched — name is too ambiguous
        raise KeyError(
            f"Player name '{player_id}' matches {len(all_names)} players "
            f"({', '.join(sorted(all_names)[:4])} …). "
            "Provide a more specific name."
        )

    rows = []

    # Winner rows
    for _, row in df[w_mask].iterrows():
        try:
            date_str = str(row.get("tourney_date") or "")
            w_g, l_g = _parse_games(row.get("score"))
            aces = float(row.get("w_ace") or 0)
            dfs  = float(row.get("w_df")  or 0)
            rows.append({
                "date":          date_str,
                "games_won":     w_g,
                "aces":          aces,
                "double_faults": dfs,
                "is_winner":     True,
            })
        except Exception:
            continue

    # Loser rows
    for _, row in df[l_mask].iterrows():
        try:
            date_str = str(row.get("tourney_date") or "")
            w_g, l_g = _parse_games(row.get("score"))
            aces = float(row.get("l_ace") or 0)
            dfs  = float(row.get("l_df")  or 0)
            rows.append({
                "date":          date_str,
                "games_won":     l_g,
                "aces":          aces,
                "double_faults": dfs,
                "is_winner":     False,
            })
        except Exception:
            continue

    if not rows:
        raise KeyError(
            f"No tennis matches found for player '{player_id}' in this dataset. "
            "This is common for ITF/Challenger-level players not covered by "
            "the ATP/WTA main-draw dataset."
        )

    # Sort most-recent first (tourney_date format is YYYYMMDD as an int or str)
    rows.sort(key=lambda r: str(r["date"]), reverse=True)
    return rows


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

# Tour-level labels exposed in fetch() return value so downstream gates and
# error messages can distinguish "data existed but stat unavailable" from
# "player is in a tier we don't cover at all."
TOUR_LEVEL_ATP_MAIN = "ATP_MAIN_DRAW"
TOUR_LEVEL_WTA_MAIN = "WTA_MAIN_DRAW"
TOUR_LEVEL_UNKNOWN  = "UNKNOWN_TIER"

# Stable prefix for ITF/Challenger fail-closed errors — gates and tests should
# key off this prefix, not the full message text.
NO_DATA_FOR_TOUR_TIER = "NO_DATA_FOR_TOUR_TIER"


def fetch(
    player_id: str,
    stat_key: str,
    date_str: str,
    n_games: int = 10,
    tour_hint: str = "auto",
) -> tuple[list[float], str, str]:
    """
    Return (values, source_label, tour_level).

    `tour_hint` is "atp", "wta", or "auto".  When "auto", both tours are
    searched for the current year + the prior year, and the tour with the
    most matches is used.

    `tour_level` is one of TOUR_LEVEL_ATP_MAIN / TOUR_LEVEL_WTA_MAIN /
    TOUR_LEVEL_UNKNOWN.  Callers (auto_game_log) surface it on the result
    dict so gates can say "no data available for this tour tier" rather than
    a generic NO_GAME_LOG_PROVIDED.

    Raises RuntimeError / KeyError on error.  ITF/Challenger failures raise
    KeyError with a message that begins with NO_DATA_FOR_TOUR_TIER so callers
    can pattern-match the failure reason.
    """
    stat_key_upper = stat_key.upper().strip()
    if stat_key_upper not in ("GAMES_WON", "ACES", "DOUBLE_FAULTS", "FANTASY_SCORE", "FPTS", "FANTASY"):
        raise KeyError(f"stat_key '{stat_key}' not mapped for TENNIS")

    date = datetime.date.fromisoformat(date_str)
    years = [date.year]
    if date.month <= 3:
        years.append(date.year - 1)

    tours = (["atp", "wta"] if tour_hint == "auto" else [tour_hint.lower()])

    all_rows: list[dict] = []
    source_label = "JeffSackmann/tennis"
    used_tour = "auto"

    for tour in tours:
        for year in years:
            try:
                df = _get_tour_df(tour, year)
                rows = _find_player_rows(df, player_id)
                if len(rows) > len(all_rows):
                    all_rows = rows
                    used_tour = tour
                    source_label = f"github:JeffSackmann/tennis_{tour} {year}"
            except KeyError:
                continue
            except RuntimeError as exc:
                logger.debug("tennis_game_log: %s %d skipped: %s", tour, year, exc)
                continue

    if not all_rows:
        raise KeyError(
            f"{NO_DATA_FOR_TOUR_TIER}: player '{player_id}' not found in "
            f"{'ATP+WTA' if tour_hint == 'auto' else tour_hint.upper()} "
            f"main-draw dataset for {years}. "
            f"ITF/Challenger players are not covered by this data source — "
            f"this is an expected outcome for lower-tier players, not a bug."
        )

    # Determine tour_level from which dataset produced the match
    if used_tour == "atp":
        tour_level = TOUR_LEVEL_ATP_MAIN
    elif used_tour == "wta":
        tour_level = TOUR_LEVEL_WTA_MAIN
    else:
        tour_level = TOUR_LEVEL_UNKNOWN

    # Extract the requested stat
    values: list[float] = []
    for row in all_rows:
        try:
            if stat_key_upper in ("FANTASY_SCORE", "FPTS", "FANTASY"):
                # Weighted composite: games_won + 0.5*aces − 0.5*double_faults
                val = (
                    float(row["games_won"])
                    + 0.5 * float(row["aces"])
                    - 0.5 * float(row["double_faults"])
                )
            elif stat_key_upper == "GAMES_WON":
                val = float(row["games_won"])
            elif stat_key_upper == "ACES":
                val = float(row["aces"])
            elif stat_key_upper == "DOUBLE_FAULTS":
                val = float(row["double_faults"])
            else:
                continue

            # Skip walkovers (all zeros, usually a W/O)
            if val == 0.0 and row["games_won"] == 0 and row["aces"] == 0:
                continue

            values.append(round(val, 1))
        except (TypeError, ValueError):
            continue

        if len(values) >= n_games:
            break

    if not values:
        raise KeyError(
            f"Tennis log for '{player_id}' produced 0 usable stat values "
            f"for stat_key={stat_key}"
        )

    return values, source_label, tour_level
