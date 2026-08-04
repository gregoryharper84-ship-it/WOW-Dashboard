"""
gate_engine/fantasy_score.py

Pure Fantasy Score derivation for PrizePicks props.

Each sport has a per-game row derivation function that maps raw box-score
fields to a single FS float.  No external API calls are made here — the
callers in auto_game_log.py fetch the rows, then call derive_* here.

=============================================================================
⚠ VERIFICATION REQUIREMENTS — read before shipping at any real confidence tier
=============================================================================

All formulas are sourced from PrizePicks' own playbook articles and
cross-referenced calculator sites.  They are high-confidence starting
points but MUST be verified against:
  1. Official PrizePicks playbook pages for each sport
  2. A sample of settled results via the postmortem ledger / clv-grader

Known open questions flagged below:
  - NFL RECEPTION_WEIGHT: sources disagree full-PPR (1.0) vs half-PPR (0.5).
    Currently set to 0.5.  Confirm from the PrizePicks NFL playbook page.
  - WNBA WEIGHTS: assumed same as NBA.  Confirm WNBA has its own weights.

If a wrong weight ships, it silently produces a plausible-looking but
incorrect L5/L10 hit-rate history.  This is the highest-risk part of Task #84.
=============================================================================
"""

from __future__ import annotations
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Formula constants — sourced from PrizePicks playbook + cross-referenced
# ---------------------------------------------------------------------------

# NBA / WNBA
# ⚠ WNBA: assumed same as NBA — confirm before trusting at any confidence tier
NBA_WNBA_WEIGHTS: dict[str, float] = {
    "pts":  1.0,
    "reb":  1.2,
    "ast":  1.5,
    "stl":  3.0,
    "blk":  3.0,
    "tov": -1.0,   # turnover — negative weight
}

# NFL (full formula regardless of position)
# ⚠ RECEPTION_WEIGHT: set to 0.5 (half-PPR) — UNCONFIRMED.
#   Full-PPR (1.0) materially changes projections for WRs/TEs.
#   Confirm from PrizePicks NFL playbook before shipping.
NFL_WEIGHTS: dict[str, float] = {
    "pass_yds_per_pt":  25.0,   # PassYds / 25 = points
    "pass_td":          4.0,
    "int_penalty":     -2.0,
    "rush_yds_per_pt":  10.0,   # RushYds / 10 = points
    "rush_td":          6.0,
    "rec_yds_per_pt":   10.0,   # RecYds / 10 = points
    "rec_td":           6.0,
    "reception":        0.5,    # ⚠ UNCONFIRMED — half-PPR assumption
    "fumbles_lost":    -2.0,
}

NFL_RECEPTION_WEIGHT_NOTE = (
    "⚠ NFL reception weight is 0.5 (half-PPR) — this is UNCONFIRMED. "
    "Sources disagree with full-PPR (1.0). Verify against the PrizePicks "
    "NFL playbook page before trusting Fantasy Score projections for WRs/TEs."
)

# MLB Hitter
MLB_HITTER_WEIGHTS: dict[str, float] = {
    "singles":      3.0,
    "doubles":      5.0,
    "triples":      8.0,
    "home_runs":   10.0,
    "runs":         2.0,
    "rbi":          2.0,
    "walks":        2.0,
    "hbp":          2.0,   # hit by pitch
    "stolen_bases": 5.0,
}

# MLB Pitcher
# Quality Start is a derived flag (6+ IP, ≤3 ER) — compute from raw game row.
MLB_PITCHER_WEIGHTS: dict[str, float] = {
    "wins":           6.0,
    "quality_starts": 4.0,    # derived flag — see is_quality_start()
    "strikeouts":     3.0,
    "outs":           1.0,    # outs recorded = floor(IP) * 3 + fraction_outs
    "earned_runs":   -3.0,
}


# ---------------------------------------------------------------------------
# Helper: Quality Start flag
# ---------------------------------------------------------------------------

def is_quality_start(ip: float, er: float) -> bool:
    """
    True if this game is a Quality Start: ≥ 6.0 IP and ≤ 3 earned runs.

    `ip` may be formatted as a decimal where .1 = 1 out, .2 = 2 outs
    (MLB Stats API convention: "6.2" means 6 innings + 2 outs, not 6.2 IP).
    We treat the fractional part as thirds: 6.2 → 6 + 2/3 ≈ 6.667.
    For the ≥6 check we accept IP ≥ 6.0 after this conversion.
    """
    whole = int(ip)
    frac  = round(ip - whole, 1)
    # frac can be .0, .1, .2 — treat each tenth as one out / 3
    adjusted_ip = whole + (round(frac * 10) / 3)
    return adjusted_ip >= 6.0 and er <= 3.0


def _parse_ip(ip_raw) -> float:
    """Parse inningsPitched (MLB Stats API) which may be '6.2' or 6.2."""
    try:
        return float(ip_raw)
    except (TypeError, ValueError):
        return 0.0


def _ip_to_outs(ip: float) -> float:
    """Convert MLB IP float (6.2 = 6 inn + 2 outs) to total outs recorded."""
    whole = int(ip)
    frac  = round(ip - whole, 1)
    extra_outs = round(frac * 10)
    return whole * 3 + extra_outs


# ---------------------------------------------------------------------------
# Per-row derivation functions
# ---------------------------------------------------------------------------

def derive_nba_wnba_row(row: dict) -> float:
    """
    Apply NBA / WNBA Fantasy Score formula to one box-score row.

    Expected keys (case-insensitive): pts, reb, ast, stl, blk, tov.
    Keys may be uppercase (NBA nba_api) or lowercase (BallDontLie WNBA).
    BallDontLie WNBA uses 'turnover' not 'tov' — both are checked.
    """
    def g(key: str) -> float:
        return float(row.get(key) or row.get(key.upper()) or 0)

    # BallDontLie WNBA calls turnovers "turnover" not "tov"
    def g_tov() -> float:
        return float(
            row.get("tov") or row.get("TOV") or row.get("turnover") or 0
        )

    pts = g("pts");  reb = g("reb");  ast = g("ast")
    stl = g("stl");  blk = g("blk");  tov = g_tov()

    fs = (
        pts * NBA_WNBA_WEIGHTS["pts"]
        + reb * NBA_WNBA_WEIGHTS["reb"]
        + ast * NBA_WNBA_WEIGHTS["ast"]
        + stl * NBA_WNBA_WEIGHTS["stl"]
        + blk * NBA_WNBA_WEIGHTS["blk"]
        + tov * NBA_WNBA_WEIGHTS["tov"]   # tov weight is -1.0
    )
    return round(fs, 2)


def derive_nfl_row(row: dict) -> float:
    """
    Apply NFL Fantasy Score formula to one weekly stats row.

    Expected keys: pass_yds, rush_yds, rec_yds, receptions,
    pass_td, rush_td, rec_td, interceptions, fumbles_lost.
    Missing keys default to 0.
    """
    def g(key: str) -> float:
        return float(row.get(key) or row.get(key.upper()) or 0)

    w = NFL_WEIGHTS
    pass_yds = g("pass_yds") or g("passing_yards")
    rush_yds = g("rush_yds") or g("rushing_yards")
    rec_yds  = g("rec_yds")  or g("receiving_yards")
    rec      = g("rec")      or g("receptions")
    pass_td  = g("pass_td")  or g("passing_tds")
    rush_td  = g("rush_td")  or g("rushing_tds")
    rec_td   = g("rec_td")   or g("receiving_tds")
    ints     = g("int")      or g("interceptions")
    fum_lost = g("fumbles_lost")

    fs = (
        pass_yds / w["pass_yds_per_pt"]
        + pass_td  * w["pass_td"]
        + ints     * w["int_penalty"]
        + rush_yds / w["rush_yds_per_pt"]
        + rush_td  * w["rush_td"]
        + rec_yds  / w["rec_yds_per_pt"]
        + rec_td   * w["rec_td"]
        + rec      * w["reception"]
        + fum_lost * w["fumbles_lost"]
    )
    return round(fs, 2)


def derive_mlb_hitter_row(row: dict) -> float:
    """
    Apply MLB Hitter Fantasy Score formula to one game row.

    Expected keys (from MLB Stats API stat object):
      hits, doubles, triples, homeRuns, runs, rbi, baseOnBalls,
      hitByPitch, stolenBases.
    Singles are derived: hits - doubles - triples - homeRuns.
    """
    def g(key: str) -> float:
        return float(row.get(key) or 0)

    hits    = g("hits")
    doubles = g("doubles")
    triples = g("triples")
    hrs     = g("homeRuns")
    singles = max(0.0, hits - doubles - triples - hrs)

    runs = g("runs");    rbi = g("rbi");       bb  = g("baseOnBalls")
    hbp  = g("hitByPitch");  sb = g("stolenBases")

    w = MLB_HITTER_WEIGHTS
    fs = (
        singles * w["singles"]
        + doubles * w["doubles"]
        + triples * w["triples"]
        + hrs     * w["home_runs"]
        + runs    * w["runs"]
        + rbi     * w["rbi"]
        + bb      * w["walks"]
        + hbp     * w["hbp"]
        + sb      * w["stolen_bases"]
    )
    return round(fs, 2)


def derive_mlb_pitcher_row(row: dict) -> float:
    """
    Apply MLB Pitcher Fantasy Score formula to one game row.

    Expected keys (from MLB Stats API pitching split stat object):
      wins, strikeOuts, inningsPitched, earnedRuns.
    Quality Start (QS) is a derived flag: IP ≥ 6.0 AND ER ≤ 3.
    Outs = total outs recorded = _ip_to_outs(IP).
    """
    def g(key: str) -> float:
        return float(row.get(key) or 0)

    wins  = g("wins")
    k     = g("strikeOuts")
    ip    = _parse_ip(row.get("inningsPitched") or 0)
    er    = g("earnedRuns")
    outs  = _ip_to_outs(ip)
    qs    = 1.0 if is_quality_start(ip, er) else 0.0

    w = MLB_PITCHER_WEIGHTS
    fs = (
        wins  * w["wins"]
        + qs  * w["quality_starts"]
        + k   * w["strikeouts"]
        + outs * w["outs"]
        + er  * w["earned_runs"]   # negative weight
    )
    return round(fs, 2)


# ---------------------------------------------------------------------------
# Series derivation
# ---------------------------------------------------------------------------

def derive_series(
    sport:    str,
    rows:     list[dict],
    position: str = "hitter",   # "hitter" | "pitcher" — only used for MLB
) -> list[float]:
    """
    Apply the per-row formula to each row in `rows` and return a list of FS floats.

    `rows` are raw box-score dicts as returned by the game-log fetchers.
    `position` is only consulted for MLB — ignored for all other sports.
    """
    sport_upper = sport.upper()
    results: list[float] = []

    for row in rows:
        try:
            if sport_upper in ("NBA", "WNBA"):
                fs = derive_nba_wnba_row(row)
            elif sport_upper == "NFL":
                fs = derive_nfl_row(row)
            elif sport_upper == "MLB":
                if position.lower() == "pitcher":
                    fs = derive_mlb_pitcher_row(row)
                else:
                    fs = derive_mlb_hitter_row(row)
            else:
                logger.warning("fantasy_score.derive_series: unsupported sport=%s", sport)
                continue
        except Exception as exc:
            logger.debug("fantasy_score.derive_series: row skipped — %s", exc)
            continue

        results.append(fs)

    return results


# ---------------------------------------------------------------------------
# Unvalidated formula flag — surface on every FS output
# ---------------------------------------------------------------------------

FORMULA_FLAGS = {
    "NBA":    [],  # no open questions
    "WNBA":   ["WNBA_WEIGHTS_ASSUMED_SAME_AS_NBA"],
    "NFL":    ["NFL_RECEPTION_WEIGHT_UNCONFIRMED"],
    "MLB_HIT": [],
    "MLB_PIT": [],
}

FS_GLOBAL_FLAG = "FANTASY_SCORE_FORMULA_UNVALIDATED"
"""
Applies to every Fantasy Score output until validated against settled results.
The formula is a best-effort reconstruction from PrizePicks playbook pages
and third-party calculators — it has not yet been back-tested against a
sufficient sample of settled results.  Do NOT use for MONEY_GRADE decisions.
"""
