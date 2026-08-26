"""
gate_engine/mlb/savant_1ip_ledger.py

Baseball Savant first-inning pitch ledger builder.

Primary data source for WOW 1IP (First Inning Pitches Thrown) historical ledgers.

Fetch strategy (tested 2026-08-04 against Tarik Skubal MLBAM 669373):
  1. Direct Baseball Savant Statcast CSV URL — pre-filters to inning=1 server-side
     so only the rows needed for the ledger are transferred.
  2. Fallback: pybaseball.statcast_pitcher — fetches all pitches then filters
     locally to inning=1.  Slower but works if the direct URL is unavailable.

Source hierarchy (WOW 1IP acquisition spec):
  1. Baseball Savant — controlling ledger source (this module)
  2. FanGraphs       — role/starter validation (Claude web access; blocked server-side)
  3. Brooks Baseball — pitch-sequence QA    (Claude web access; blocked server-side)

FanGraphs and Brooks Baseball are not called from this module.

can_execute = False unconditional.
"""
from __future__ import annotations

can_execute = False

import importlib
import io
import logging
import statistics
import urllib.request
import urllib.parse
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Baseball Savant Statcast CSV endpoint
# ---------------------------------------------------------------------------

_SAVANT_CSV_URL = (
    "https://baseballsavant.mlb.com/statcast_search/csv"
    "?player_type=pitcher"
    "&pitchers_lookup[]={pitcher_id}"
    "&game_date_gt={start_dt}"
    "&game_date_lt={end_dt}"
    "&game_type=R"
    "&inning=1"
    "&type=details"
    "&hfSit="
    "&hfOuts="
)

_SAVANT_REQUEST_TIMEOUT = 30   # seconds

# ---------------------------------------------------------------------------
# Statcast events that contribute to first-inning counting stats
# ---------------------------------------------------------------------------

_HIT_EVENTS   = {"single", "double", "triple", "home_run"}
_WALK_EVENTS  = {"walk", "intent_walk"}        # Statcast uses "intent_walk"
_HBP_EVENTS   = {"hit_by_pitch"}
_ERROR_EVENTS = {"field_error", "fielders_choice_error"}

# ---------------------------------------------------------------------------
# Lazy pybaseball / pandas import (mirrors app.py pattern)
# ---------------------------------------------------------------------------

_pb   = None
_pd   = None
_PB_OK = False


def _ensure_pybaseball() -> bool:
    global _pb, _pd, _PB_OK
    if _PB_OK:
        return True
    try:
        _pb = importlib.import_module("pybaseball")
        _pd = importlib.import_module("pandas")
        _PB_OK = True
        return True
    except ImportError:
        return False


def _ensure_pandas() -> bool:
    """Ensure pandas is available even when pybaseball is not used."""
    global _pd
    if _pd is not None:
        return True
    try:
        _pd = importlib.import_module("pandas")
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_1ip_ledger(
    pitcher_id: int,
    season: str,
    board_date: str,
    *,
    line: float | None = None,
    side: str = "LESS",
    max_starts: int = 10,
) -> dict[str, Any]:
    """
    Build the Baseball Savant first-inning pitch ledger for one pitcher.

    Parameters
    ----------
    pitcher_id  : MLBAM pitcher ID.
    season      : Four-digit year string, e.g. "2026".
    board_date  : ISO date string (YYYY-MM-DD).  Data strictly before this
                  date — board_date itself is never included so postgame
                  rows cannot contaminate a pregame ledger.
    line        : Board line (pitches). When provided, each row gets a
                  "hit" field ("HIT"/"MISS") and summary hit-rate fields
                  are populated.
    side        : "MORE" or "LESS". Used only when line is provided.
    max_starts  : Maximum starts to include (most recent first). Default 10.

    Returns
    -------
    dict with:
        ledger_rows     : list[dict] — one row per verified start
        l5_hit_rate     : float | None
        l10_hit_rate    : float | None
        l5_pitch_mean   : float | None
        l10_pitch_mean  : float | None
        l5_pitch_median : float | None
        l10_pitch_median: float | None
        l5_pitch_std    : float | None
        l10_pitch_std   : float | None
        bf_distribution : dict — P(BF=3), P(BF=4), P(BF>=5)
        fetch_method    : "savant_csv_direct" | "pybaseball_fallback"
        source          : str
        pitcher_id      : int
        season          : str
        board_date      : str
        data_coverage   : int
        gaps            : list[str]
        error           : str | None
        can_execute     : False
    """
    if not _ensure_pandas():
        return _error_result(pitcher_id, season, board_date,
                             "pandas not available")

    start_dt = f"{season}-01-01"
    gaps: list[str] = []

    # ── Try 1: direct Savant CSV (inning=1 pre-filtered server-side) ────────
    raw_df, fetch_method, fetch_err = _fetch_savant_csv_direct(
        pitcher_id, start_dt, board_date
    )

    # ── Try 2: pybaseball fallback ───────────────────────────────────────────
    if raw_df is None:
        gaps.append(f"Direct Savant CSV unavailable ({fetch_err}); using pybaseball fallback")
        raw_df, fetch_method, fetch_err2 = _fetch_pybaseball_fallback(
            pitcher_id, start_dt, board_date
        )
        if raw_df is None:
            return _error_result(pitcher_id, season, board_date,
                                 f"Both fetch methods failed. "
                                 f"Direct: {fetch_err}. Pybaseball: {fetch_err2}")

    if raw_df.empty:
        return _error_result(pitcher_id, season, board_date,
                             "No first-inning pitch rows found in Statcast data")

    # ── Group by game_pk and build ledger rows ───────────────────────────────
    ledger_rows, row_gaps = _build_ledger_rows(raw_df, line, side)
    gaps.extend(row_gaps)

    # ── Sort most-recent-first and cap ───────────────────────────────────────
    ledger_rows.sort(key=lambda r: r["game_date"], reverse=True)
    ledger_rows = ledger_rows[:max_starts]

    if len(ledger_rows) < 5:
        gaps.append(f"Only {len(ledger_rows)} verified start(s) found (need 5 for L5)")
    elif len(ledger_rows) < 10:
        gaps.append(f"Only {len(ledger_rows)} verified start(s) found (need 10 for full L10)")

    # ── Summary statistics ───────────────────────────────────────────────────
    l5  = ledger_rows[:5]
    l10 = ledger_rows[:10]

    pitches_l5  = [r["first_inning_pitches"] for r in l5]
    pitches_l10 = [r["first_inning_pitches"] for r in l10]
    bf_all      = [r["first_inning_batters_faced"] for r in ledger_rows
                   if r["first_inning_batters_faced"] is not None]

    return {
        "ledger_rows":      ledger_rows,
        # Hit rates (require line)
        "l5_hit_rate":      _hit_rate(l5,  line, side) if line is not None else None,
        "l10_hit_rate":     _hit_rate(l10, line, side) if line is not None else None,
        # Pitch count summaries
        "l5_pitch_mean":    _mean(pitches_l5),
        "l10_pitch_mean":   _mean(pitches_l10),
        "l5_pitch_median":  _median(pitches_l5),
        "l10_pitch_median": _median(pitches_l10),
        "l5_pitch_std":     _std(pitches_l5),
        "l10_pitch_std":    _std(pitches_l10),
        # BF distribution
        "bf_distribution":  _bf_distribution(bf_all),
        # Provenance
        "fetch_method":     fetch_method,
        "source":           "Baseball Savant (Statcast pitch-level data)",
        "pitcher_id":       pitcher_id,
        "season":           season,
        "board_date":       board_date,
        "data_coverage":    len(ledger_rows),
        "gaps":             gaps,
        "error":            None,
        "can_execute":      False,
    }


# ---------------------------------------------------------------------------
# Fetch method 1: direct Savant CSV URL (inning=1 pre-filtered)
# ---------------------------------------------------------------------------

def _fetch_savant_csv_direct(
    pitcher_id: int,
    start_dt: str,
    end_dt: str,   # board_date (exclusive)
) -> tuple[Any, str, str | None]:
    """
    Fetch pitch-level CSV from Baseball Savant with inning=1 server-side filter.

    Returns (DataFrame | None, fetch_method_label, error_message | None).
    DataFrame is already filtered to inning=1 (server does it).
    """
    url = _SAVANT_CSV_URL.format(
        pitcher_id=pitcher_id,
        start_dt=start_dt,
        end_dt=end_dt,
    )
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "WOW-1IP-Ledger/1.0 (research; can_execute=false)"},
        )
        with urllib.request.urlopen(req, timeout=_SAVANT_REQUEST_TIMEOUT) as resp:
            content = resp.read().decode("utf-8")

        if not content.strip() or content.startswith("<!"):
            return None, "savant_csv_direct", "Savant returned HTML instead of CSV"

        if not _ensure_pandas():
            return None, "savant_csv_direct", "pandas not available for CSV parse"

        df = _pd.read_csv(io.StringIO(content), low_memory=False)
        if df.empty:
            return None, "savant_csv_direct", "Savant CSV empty for this query"

        # Savant applies inning=1 filter server-side; verify it held
        if "inning" in df.columns:
            df = df[df["inning"] == 1]

        return df, "savant_csv_direct", None

    except Exception as exc:
        return None, "savant_csv_direct", f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Fetch method 2: pybaseball fallback
# ---------------------------------------------------------------------------

def _fetch_pybaseball_fallback(
    pitcher_id: int,
    start_dt: str,
    end_dt: str,
) -> tuple[Any, str, str | None]:
    """
    Fetch via pybaseball.statcast_pitcher and filter locally to inning=1.

    Returns (DataFrame | None, fetch_method_label, error_message | None).
    """
    if not _ensure_pybaseball():
        return None, "pybaseball_fallback", "pybaseball not installed"
    try:
        raw = _pb.statcast_pitcher(start_dt, end_dt, player_id=pitcher_id)
        if raw is None or (hasattr(raw, "empty") and raw.empty):
            return None, "pybaseball_fallback", "statcast_pitcher returned empty"

        raw["game_date"] = _pd.to_datetime(raw["game_date"])
        cutoff = _pd.Timestamp(end_dt)
        raw = raw[raw["game_date"] < cutoff]

        if "inning" in raw.columns:
            raw = raw[raw["inning"] == 1]

        if raw.empty:
            return None, "pybaseball_fallback", "No inning=1 rows after filtering"

        return raw, "pybaseball_fallback", None

    except Exception as exc:
        return None, "pybaseball_fallback", f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Ledger row construction
# ---------------------------------------------------------------------------

def _build_ledger_rows(
    df: Any,
    line: float | None,
    side: str,
) -> tuple[list[dict], list[str]]:
    """
    Group by game_pk and produce one ledger row per start.
    Returns (ledger_rows, gaps).
    """
    gaps: list[str] = []

    if "game_date" in df.columns:
        df["game_date"] = _pd.to_datetime(df["game_date"])

    has_teams  = "home_team" in df.columns and "away_team" in df.columns
    has_topbot = "inning_topbot" in df.columns
    has_ab_num = "at_bat_number" in df.columns
    has_events = "events" in df.columns
    has_c      = "fielder_2" in df.columns   # catcher MLBAM ID
    has_inning = "inning" in df.columns

    # For starter inference we need all-inning data, but here we only have
    # inning=1 rows. We infer "starter_likely" from a simpler heuristic:
    # if bf >= 3, the pitcher completed a normal first inning start.
    # Caller should cross-check with FanGraphs for UNLIKELY cases.

    ledger_rows: list[dict] = []
    group_col = "game_pk" if "game_pk" in df.columns else "game_date"

    for game_pk, grp in df.groupby(group_col):
        grp = grp.copy()
        if "at_bat_number" in grp.columns:
            grp = grp.sort_values("at_bat_number")

        game_date_str = (
            grp["game_date"].iloc[0].strftime("%Y-%m-%d")
            if "game_date" in grp.columns
            else "UNKNOWN"
        )

        # ── Pitch count (primary metric) ──────────────────────────────────
        pitch_count = len(grp)

        # ── Batters faced ─────────────────────────────────────────────────
        if has_ab_num:
            bf: int | None = int(grp["at_bat_number"].nunique())
        else:
            bf = None
            gaps.append(f"{game_date_str}: at_bat_number missing — BF not computed")

        # ── Opponent ──────────────────────────────────────────────────────
        opponent = _resolve_opponent(grp, has_teams, has_topbot)

        # ── Event counts (PA-ending rows only) ────────────────────────────
        if has_events:
            ev = grp["events"].dropna()
            h_count   = int(ev.isin(_HIT_EVENTS).sum())
            bb_count  = int(ev.isin(_WALK_EVENTS).sum())
            hbp_count = int(ev.isin(_HBP_EVENTS).sum())
            err_count = int(ev.isin(_ERROR_EVENTS).sum())
        else:
            h_count = bb_count = hbp_count = err_count = 0
            gaps.append(f"{game_date_str}: events column missing — H/BB/HBP not computed")

        # ── Catcher (fielder_2 = catcher MLBAM ID in Statcast) ───────────
        if has_c:
            catcher_ids = grp["fielder_2"].dropna().unique()
            catcher_id = int(catcher_ids[0]) if len(catcher_ids) > 0 else None
        else:
            catcher_id = None

        # ── Starter inference ─────────────────────────────────────────────
        # With only inning=1 data available, LIKELY = bf >= 3 (completed inning)
        if bf is not None:
            starter_likely = bf >= 3
            starter_note   = f"inferred_from_bf={bf} (cross-check FanGraphs)"
        else:
            starter_likely = None
            starter_note   = "bf_unavailable"

        row: dict[str, Any] = {
            "game_date":                  game_date_str,
            "game_pk":                    str(game_pk),
            "opponent":                   opponent,
            "starter_confirmed":          ("LIKELY"   if starter_likely is True  else
                                           "UNLIKELY" if starter_likely is False else
                                           "UNKNOWN"),
            "starter_note":               starter_note,
            # Core ledger fields (from Savant spec)
            "first_inning_pitches":       pitch_count,
            "first_inning_batters_faced": bf,
            "first_inning_hits":          h_count,
            "first_inning_walks":         bb_count,
            "first_inning_hbp":           hbp_count,
            "first_inning_errors":        err_count,
            # Catcher pairing
            "catcher_mlbam_id":           catcher_id,
            # Source
            "source":                     "Baseball Savant (statcast_pitcher)",
            "source_game_id":             str(game_pk),
        }

        # Hit/miss at line
        if line is not None:
            row["line"] = line
            row["side"] = side
            row["hit"]  = _line_hit(pitch_count, line, side)

        ledger_rows.append(row)

    return ledger_rows, gaps


# ---------------------------------------------------------------------------
# Opponent resolution
# ---------------------------------------------------------------------------

def _resolve_opponent(grp: Any, has_teams: bool, has_topbot: bool) -> str:
    """
    Determine opposing team from Statcast row metadata.

    Statcast inning_topbot:
      "Top" → visiting team batting → pitcher is on home team → opponent = away_team
      "Bot" → home team batting     → pitcher is on away team → opponent = home_team
    """
    if not (has_teams and has_topbot):
        return "OPPONENT_UNKNOWN"

    topbot = str(grp["inning_topbot"].iloc[0])
    home   = str(grp["home_team"].iloc[0])
    away   = str(grp["away_team"].iloc[0])

    if topbot == "Top":
        return away if away not in ("", "nan", "None") else "OPPONENT_UNKNOWN"
    if topbot == "Bot":
        return home if home not in ("", "nan", "None") else "OPPONENT_UNKNOWN"
    return "OPPONENT_UNKNOWN"


# ---------------------------------------------------------------------------
# BF distribution
# ---------------------------------------------------------------------------

def _bf_distribution(bf_list: list[int]) -> dict[str, Any]:
    """P(BF=3), P(BF=4), P(BF>=5) from ledger rows.

    Returns both 'p_bf_5plus' (legacy key) and 'p_bf_gte5' (alias expected
    by ip1_event_tree.simulate_1ip) so both consumers work without an adapter.
    """
    valid = [b for b in bf_list if b is not None]
    n = len(valid)
    if n == 0:
        return {"n": 0, "p_bf_3": None, "p_bf_4": None,
                "p_bf_5plus": None, "p_bf_gte5": None,
                "note": "BF data unavailable"}
    p5plus = round(sum(1 for b in valid if b >= 5) / n, 4)
    return {
        "n":          n,
        "p_bf_3":     round(sum(1 for b in valid if b == 3) / n, 4),
        "p_bf_4":     round(sum(1 for b in valid if b == 4) / n, 4),
        "p_bf_5plus": p5plus,
        "p_bf_gte5":  p5plus,   # alias expected by ip1_event_tree.simulate_1ip()
        "note":       f"Based on {n} starts with verified BF data",
    }


def compute_pitches_per_batter_dist(ledger_rows: list[dict]) -> dict[str, Any]:
    """
    Derive pitches-per-batter distribution from ledger rows.

    For each start with both pitch count and BF present, compute
    pitches_per_batter = first_inning_pitches / first_inning_batters_faced.
    Returns {'mean': float, 'std': float, 'n': int} for ip1_event_tree.

    Falls back to genre-calibrated defaults (mean=4.2, std=1.1) when fewer
    than 3 valid starts exist — never fabricates individual pitch counts.
    """
    _DEFAULT_MEAN = 4.2
    _DEFAULT_STD  = 1.1

    ratios: list[float] = []
    for r in (ledger_rows or []):
        pitches = r.get("first_inning_pitches")
        bf      = r.get("first_inning_batters_faced")
        if (pitches is not None and bf is not None
                and isinstance(pitches, (int, float))
                and isinstance(bf, (int, float))
                and bf > 0):
            ratios.append(float(pitches) / float(bf))

    if len(ratios) < 3:
        return {
            "mean": _DEFAULT_MEAN,
            "std":  _DEFAULT_STD,
            "n":    len(ratios),
            "note": (
                f"Insufficient starts ({len(ratios)}) for pitcher-specific "
                f"pitches-per-batter; using genre defaults "
                f"(mean={_DEFAULT_MEAN}, std={_DEFAULT_STD})"
            ),
        }

    mean_val = sum(ratios) / len(ratios)
    variance = sum((r - mean_val) ** 2 for r in ratios) / (len(ratios) - 1)
    std_val  = variance ** 0.5

    return {
        "mean": round(mean_val, 3),
        "std":  round(std_val, 3),
        "n":    len(ratios),
        "note": f"Derived from {len(ratios)} verified starts",
    }


# ---------------------------------------------------------------------------
# Summary stat helpers
# ---------------------------------------------------------------------------

def _line_hit(pitch_count: int, line: float, side: str) -> str:
    if side.upper() == "MORE":
        return "HIT" if pitch_count > line else "MISS"
    return "HIT" if pitch_count < line else "MISS"


def _hit_rate(rows: list[dict], line: float | None, side: str) -> float | None:
    if not rows or line is None:
        return None
    hits = sum(1 for r in rows if r.get("hit") == "HIT")
    return round(hits / len(rows), 4)


def _mean(values: list[int]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _median(values: list[int]) -> float | None:
    return round(statistics.median(values), 2) if values else None


def _std(values: list[int]) -> float | None:
    return round(statistics.stdev(values), 2) if len(values) >= 2 else None


def _error_result(pitcher_id: int, season: str, board_date: str,
                  error: str) -> dict[str, Any]:
    logger.warning("savant_1ip_ledger error: %s", error)
    return {
        "ledger_rows":      [],
        "l5_hit_rate":      None,  "l10_hit_rate":     None,
        "l5_pitch_mean":    None,  "l10_pitch_mean":   None,
        "l5_pitch_median":  None,  "l10_pitch_median": None,
        "l5_pitch_std":     None,  "l10_pitch_std":    None,
        "bf_distribution":  {"n": 0, "p_bf_3": None, "p_bf_4": None,
                             "p_bf_5plus": None, "note": error},
        "fetch_method":     "none",
        "source":           "Baseball Savant (statcast_pitcher)",
        "pitcher_id":       pitcher_id,
        "season":           season,
        "board_date":       board_date,
        "data_coverage":    0,
        "gaps":             [error],
        "error":            error,
        "can_execute":      False,
    }


# ---------------------------------------------------------------------------
# Convenience: resolve MLBAM ID from pitcher name
# ---------------------------------------------------------------------------

def resolve_mlbam_id(first: str, last: str) -> int | None:
    """
    Look up MLBAM pitcher ID by first/last name via pybaseball's lookup table.
    Returns None if not found or pybaseball unavailable.
    """
    if not _ensure_pybaseball():
        return None
    try:
        df = _pb.playerid_lookup(last, first)
        if df is None or (hasattr(df, "empty") and df.empty):
            return None
        return int(df.iloc[0]["key_mlbam"])
    except Exception:
        return None
