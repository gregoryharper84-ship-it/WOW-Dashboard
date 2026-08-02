"""
prop_persistence.py  —  Multi-Window Prop Persistence & Distribution Audit
WOW-PATCH-2026-08-01-MULTI-WINDOW-PROP-PERSISTENCE-AND-DISTRIBUTION-AUDIT

Computes three orthogonal discovery signals from historical hit-rate windows:
  1. Persistence score — weighted agreement across up to 6 windows.
  2. Window agreement classification — FULL_ALIGNMENT → CONFLICTING_WINDOWS.
  3. Threshold cushion metrics — mean/median/std/25th-pct of (stat − line).

IMPORTANT design rule (from Linemaker analysis):
  All outputs affect **research_priority** only.
  None of them may override, replace, or be published as model probability.
  Final ranking always uses calibrated_lower_bound, not historical hit rate.

can_execute=False unconditional.
"""
from __future__ import annotations

import math
import statistics
from typing import Any

# ---------------------------------------------------------------------------
# Window weight spec (Linemaker analysis recommendation, §"Prop Persistence Score")
# ---------------------------------------------------------------------------

WINDOW_WEIGHTS: dict[str, float] = {
    "role_matched": 0.35,
    "season":       0.25,
    "l10":          0.15,
    "l20":          0.15,
    "l5":           0.10,
}

# ---------------------------------------------------------------------------
# Window agreement thresholds
# ---------------------------------------------------------------------------

_FULL_ALIGNMENT_SPREAD    = 0.10   # all windows within 10pp
_PARTIAL_ALIGNMENT_SPREAD = 0.20   # most within 20pp
_CONFLICTING_SPREAD       = 0.30   # spread > 30pp → CONFLICTING_WINDOWS
_DIVERGENCE_THRESHOLD     = 0.20   # |L10 − season| ≥ 20pp → RECENT_FORM_DIVERGENCE


# ---------------------------------------------------------------------------
# Window Agreement Classification
# ---------------------------------------------------------------------------

WindowAgreement = str  # typed alias for clarity

FULL_ALIGNMENT      = "FULL_ALIGNMENT"
PARTIAL_ALIGNMENT   = "PARTIAL_ALIGNMENT"
RECENT_ONLY         = "RECENT_ONLY"
SEASON_ONLY         = "SEASON_ONLY"
CONFLICTING_WINDOWS = "CONFLICTING_WINDOWS"
INSUFFICIENT_DATA   = "INSUFFICIENT_DATA"


def classify_window_agreement(
    windows: dict[str, float | None],
) -> dict[str, Any]:
    """
    Classify agreement across up to 6 hit-rate windows.

    Parameters
    ----------
    windows : dict
        Keys from {"l5", "l10", "l15", "l20", "season", "role_matched"}.
        Values are hit rates 0–1, or None if unavailable.

    Returns
    -------
    {
        "agreement":            str,    # window agreement label
        "recent_form_divergence": bool, # |L10 − season| ≥ 20pp
        "divergence_detail":    str | None,
        "available_windows":    list[str],
        "window_spread":        float | None,  # max − min across available
    }
    """
    available = {k: v for k, v in windows.items() if v is not None}

    if len(available) < 2:
        single = next(iter(available.keys()), None)
        if single in ("l5", "l10", "l15", "l20"):
            return {
                "agreement":              RECENT_ONLY,
                "recent_form_divergence": False,
                "divergence_detail":      "Only recent window available; season baseline missing.",
                "available_windows":      list(available.keys()),
                "window_spread":          None,
            }
        if single == "season":
            return {
                "agreement":              SEASON_ONLY,
                "recent_form_divergence": False,
                "divergence_detail":      "Only season baseline available; recent form unknown.",
                "available_windows":      list(available.keys()),
                "window_spread":          None,
            }
        return {
            "agreement":              INSUFFICIENT_DATA,
            "recent_form_divergence": False,
            "divergence_detail":      "Fewer than 2 windows available.",
            "available_windows":      list(available.keys()),
            "window_spread":          None,
        }

    vals    = list(available.values())
    spread  = round(max(vals) - min(vals), 4)

    # RECENT_ONLY — no season or role-matched window present
    has_season    = available.get("season") is not None
    has_recent    = any(available.get(k) is not None for k in ("l5", "l10", "l15", "l20"))
    if not has_season:
        base_agreement = RECENT_ONLY
    elif not has_recent:
        base_agreement = SEASON_ONLY
    elif spread <= _FULL_ALIGNMENT_SPREAD:
        base_agreement = FULL_ALIGNMENT
    elif spread <= _PARTIAL_ALIGNMENT_SPREAD:
        base_agreement = PARTIAL_ALIGNMENT
    elif spread > _CONFLICTING_SPREAD:
        base_agreement = CONFLICTING_WINDOWS
    else:
        base_agreement = PARTIAL_ALIGNMENT

    # RECENT_FORM_DIVERGENCE — L10 vs season divergence check
    l10     = available.get("l10")
    season  = available.get("season")
    divergence = False
    divergence_detail: str | None = None
    if l10 is not None and season is not None:
        gap = abs(l10 - season)
        if gap >= _DIVERGENCE_THRESHOLD:
            divergence = True
            direction  = "above" if l10 > season else "below"
            divergence_detail = (
                f"RECENT_FORM_DIVERGENCE: L10={l10:.1%} is {gap:.0%} {direction} "
                f"season={season:.1%}. Triggers OUTLIER_OR_ROLE_AUDIT_REQUIRED. "
                f"Possible causes: role change, schedule strength shift, outlier games, "
                f"unsustainable shooting, or small sample."
            )
            # Override agreement if we haven't already called it conflicting
            if base_agreement == FULL_ALIGNMENT:
                base_agreement = PARTIAL_ALIGNMENT

    return {
        "agreement":              base_agreement,
        "recent_form_divergence": divergence,
        "divergence_detail":      divergence_detail,
        "available_windows":      sorted(available.keys()),
        "window_spread":          spread,
    }


# ---------------------------------------------------------------------------
# Persistence Score
# ---------------------------------------------------------------------------

def compute_persistence_score(
    windows: dict[str, float | None],
) -> dict[str, Any]:
    """
    Weighted average hit rate across available windows.

    If a window is missing, its weight is redistributed proportionally
    to the remaining windows.

    Result affects research_priority only — never overrides calibrated probability.

    Returns
    -------
    {
        "persistence_score":     float | None,
        "windows_used":          list[str],
        "weight_applied":        dict[str, float],
        "raw_weights":           dict[str, float],   # requested weights
        "interpretation":        str,
        "discovery_only":        True,               # always True
    }
    """
    available = {k: float(v) for k, v in windows.items() if v is not None}
    if not available:
        return {
            "persistence_score": None,
            "windows_used":      [],
            "weight_applied":    {},
            "raw_weights":       dict(WINDOW_WEIGHTS),
            "interpretation":    "No hit-rate windows available.",
            "discovery_only":    True,
        }

    # Redistribute missing window weights
    available_keys = set(available.keys())
    applicable     = {k: w for k, w in WINDOW_WEIGHTS.items() if k in available_keys}
    total_w        = sum(applicable.values())

    if total_w == 0:
        # Window keys don't match canonical names — simple average
        score = round(statistics.mean(available.values()), 4)
        return {
            "persistence_score": score,
            "windows_used":      list(available.keys()),
            "weight_applied":    {k: round(1 / len(available), 4) for k in available},
            "raw_weights":       dict(WINDOW_WEIGHTS),
            "interpretation":    _interpret(score),
            "discovery_only":    True,
        }

    normalized = {k: round(w / total_w, 5) for k, w in applicable.items()}
    score       = sum(normalized[k] * available[k] for k in normalized)
    score       = round(score, 4)

    return {
        "persistence_score": score,
        "windows_used":      sorted(normalized.keys()),
        "weight_applied":    normalized,
        "raw_weights":       dict(WINDOW_WEIGHTS),
        "interpretation":    _interpret(score),
        "discovery_only":    True,
    }


def _interpret(score: float) -> str:
    if score >= 0.75:
        return "HIGH — strong multi-window agreement; elevates research priority."
    if score >= 0.60:
        return "MODERATE — reasonable agreement; warrants deeper model review."
    if score >= 0.45:
        return "LOW-MODERATE — mixed historical signal; model verification required."
    return "LOW — weak multi-window agreement; discovery signal insufficient alone."


# ---------------------------------------------------------------------------
# Threshold Cushion
# ---------------------------------------------------------------------------

def compute_threshold_cushion(
    stat_values: list[float],
    line:        float,
    direction:   str = "MORE",
) -> dict[str, Any]:
    """
    Compute the distribution of (stat − line) over historical game log values.

    direction="MORE" → positive cushion is good (Over).
    direction="LESS" → negate cushion (Under).

    Returns
    -------
    {
        "mean_cushion":          float | None,
        "median_cushion":        float | None,
        "std_cushion":           float | None,
        "p25_cushion":           float | None,   # 25th percentile — more conservative
        "p75_cushion":           float | None,
        "n_games":               int,
        "hit_rate":              float | None,   # fraction that cleared the line
        "line":                  float,
        "direction":             str,
        "note":                  str,
    }
    """
    if not stat_values:
        return {
            "mean_cushion": None, "median_cushion": None,
            "std_cushion": None, "p25_cushion": None, "p75_cushion": None,
            "n_games": 0, "hit_rate": None,
            "line": line, "direction": direction,
            "note": "No stat values provided.",
        }

    sign = 1.0 if direction.upper() in ("MORE", "OVER", ">") else -1.0
    cushions = [sign * (float(v) - float(line)) for v in stat_values]
    n = len(cushions)

    # Hit rate (fraction cleared the line)
    over = direction.upper() in ("MORE", "OVER", ">")
    cleared = sum(
        1 for v in stat_values
        if (float(v) > float(line) if over else float(v) < float(line))
    )
    hit_rate   = round(cleared / n, 4) if n else None

    mean_c   = round(statistics.mean(cushions), 4)
    med_c    = round(statistics.median(cushions), 4)
    std_c    = round(statistics.stdev(cushions), 4) if n > 1 else None
    sorted_c = sorted(cushions)
    p25_c    = round(_percentile(sorted_c, 25), 4)
    p75_c    = round(_percentile(sorted_c, 75), 4)

    note = (
        "25th percentile cushion is the most conservative screening metric. "
        "A positive 25th-pct means the line is cleared in at least 75% of games. "
        "Large mean-to-line gap is NOT sufficient alone — verify role, minutes, opponent."
    )

    return {
        "mean_cushion":   mean_c,
        "median_cushion": med_c,
        "std_cushion":    std_c,
        "p25_cushion":    p25_c,
        "p75_cushion":    p75_c,
        "n_games":        n,
        "hit_rate":       hit_rate,
        "line":           float(line),
        "direction":      direction.upper(),
        "note":           note,
    }


def _percentile(sorted_data: list[float], p: float) -> float:
    """Linear interpolation percentile on sorted list."""
    n = len(sorted_data)
    if n == 0:
        return 0.0
    if n == 1:
        return sorted_data[0]
    index  = (p / 100) * (n - 1)
    lower  = int(index)
    upper  = min(lower + 1, n - 1)
    frac   = index - lower
    return sorted_data[lower] + frac * (sorted_data[upper] - sorted_data[lower])


# ---------------------------------------------------------------------------
# Hit-Rate Inflation Audit flags
# ---------------------------------------------------------------------------

_INFLATION_CHECKS: list[dict[str, Any]] = [
    {
        "id":          "role_change",
        "description": "Role or usage change since the tracked sample",
        "field":       "role_change_detected",
    },
    {
        "id":          "schedule_strength",
        "description": "Soft schedule period inflating recent numbers",
        "field":       "schedule_strength_flag",
    },
    {
        "id":          "outlier_games",
        "description": "One or more outlier games distorting mean",
        "field":       "outlier_games_detected",
    },
    {
        "id":          "teammate_absences",
        "description": "Key teammate absences inflating usage",
        "field":       "teammate_absence_period",
    },
    {
        "id":          "unsustainable_efficiency",
        "description": "Shooting efficiency above sustainable range",
        "field":       "unsustainable_efficiency_flag",
    },
    {
        "id":          "small_sample",
        "description": "Sample size < 8 games reduces reliability",
        "field":       None,  # derived from n_games
    },
    {
        "id":          "stale_season_totals",
        "description": "Season totals include a prior role the player no longer holds",
        "field":       "season_totals_stale_flag",
    },
    {
        "id":          "opponent_quality",
        "description": "Recent games vs below-average opponents not flagged",
        "field":       "opponent_quality_flag",
    },
]


def run_inflation_audit(
    row:     dict[str, Any],
    n_games: int | None = None,
) -> dict[str, Any]:
    """
    Check row metadata for hit-rate inflation signals.

    Checks a row's enrichment fields for flags already set by upstream
    modules. Adds a `hit_rate_inflation_risk` label when ≥ 2 signals fire.

    Returns
    -------
    {
        "inflation_flags":    list[str],     # check IDs that fired
        "inflation_risk":     "HIGH"|"MODERATE"|"LOW",
        "outlier_or_role_audit_required": bool,
        "details":            list[str],
    }
    """
    flags:   list[str] = []
    details: list[str] = []

    for check in _INFLATION_CHECKS:
        if check["id"] == "small_sample":
            n = n_games or row.get("n_games") or row.get("games_played")
            if n is not None and int(n) < 8:
                flags.append("small_sample")
                details.append(f"Sample size {n} < 8 games — hit rates less reliable.")
        elif check["field"] and row.get(check["field"]):
            flags.append(check["id"])
            details.append(check["description"] + ".")

    n_flags = len(flags)
    if n_flags >= 3:
        risk = "HIGH"
    elif n_flags >= 1:
        risk = "MODERATE"
    else:
        risk = "LOW"

    return {
        "inflation_flags":               flags,
        "inflation_risk":                risk,
        "outlier_or_role_audit_required": n_flags >= 2,
        "details":                       details,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_prop_persistence(
    windows:     dict[str, float | None],
    stat_values: list[float] | None = None,
    line:        float | None       = None,
    direction:   str                = "MORE",
    row:         dict[str, Any]     | None = None,
    n_games:     int | None         = None,
) -> dict[str, Any]:
    """
    Full prop persistence analysis.

    Parameters
    ----------
    windows : dict
        Hit-rate windows: {"l5", "l10", "l15", "l20", "season", "role_matched"}
    stat_values : list[float] | None
        Raw per-game stat values for threshold cushion computation.
    line : float | None
        Prop line for cushion computation.
    direction : str
        "MORE" or "LESS".
    row : dict | None
        Full row dict for inflation audit (uses enrichment flags).
    n_games : int | None
        Override for game count in inflation audit.

    Returns
    -------
    {
        "persistence_score":    dict,
        "window_agreement":     dict,
        "threshold_cushion":    dict | None,
        "inflation_audit":      dict | None,
        "research_priority_boost": "HIGH" | "MODERATE" | "NONE",
        "discovery_notes":      list[str],
        "can_execute":          False,   # always
    }
    """
    persistence   = compute_persistence_score(windows)
    agreement     = classify_window_agreement(windows)
    cushion: dict[str, Any] | None = None
    inflation: dict[str, Any] | None = None

    if stat_values is not None and line is not None:
        cushion = compute_threshold_cushion(stat_values, line, direction)

    if row is not None:
        inflation = run_inflation_audit(row, n_games=n_games)

    # Research priority boost
    notes: list[str] = []
    ps = persistence.get("persistence_score")
    boost: str = "NONE"

    if agreement["recent_form_divergence"]:
        notes.append(
            "RECENT_FORM_DIVERGENCE: L10-season gap ≥ 20pp. "
            "OUTLIER_OR_ROLE_AUDIT_REQUIRED before using hit rate as research signal."
        )

    if ps is not None:
        if ps >= 0.70 and agreement["agreement"] in (FULL_ALIGNMENT, PARTIAL_ALIGNMENT):
            boost = "HIGH"
            notes.append(
                f"Persistence score {ps:.1%} across "
                f"{len(persistence['windows_used'])} windows → HIGH research priority boost."
            )
        elif ps >= 0.55:
            boost = "MODERATE"
            notes.append(
                f"Persistence score {ps:.1%} → MODERATE research priority boost."
            )
        else:
            notes.append(
                f"Persistence score {ps:.1%} — insufficient for discovery signal."
            )

    if boost != "NONE" and agreement["agreement"] == CONFLICTING_WINDOWS:
        boost = "NONE"
        notes.append(
            "Research priority boost cancelled — CONFLICTING_WINDOWS agreement. "
            "Model verification required before elevating priority."
        )

    notes.append(
        "REMINDER: Persistence score affects research_priority only. "
        "Historical hit rate is NOT a model probability. "
        "Final ranking must use calibrated_lower_bound."
    )

    return {
        "persistence_score":         persistence,
        "window_agreement":          agreement,
        "threshold_cushion":         cushion,
        "inflation_audit":           inflation,
        "research_priority_boost":   boost,
        "discovery_notes":           notes,
        "can_execute":               False,
    }
