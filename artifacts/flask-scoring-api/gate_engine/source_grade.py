"""
source_grade.py  —  Module H: Source Timestamp Grading
WOW v16 / Section 4.3

Every source used in prop analysis must carry a grade based on source type
and freshness. Grade controls the approval cap.

SOURCE GRADES:
  A   — Direct API / stat feed with timestamp            Full trust — T0
  A-  — Official box score / gamelog                     High trust — T1
  B   — Trusted stat site (StatMuse, BBRef, Her Hoop…)  Reliable — T1
  C   — Article / preview / blurb                        Context only — T3
  D   — Screenshot only / Social report unconfirmed      Cannot verify
  N/T — No timestamp present                             Caps at Watch

CAPS BY SOURCE GRADE (critical-path sources):
  All A or A-                       → no cap from source grade
  Any B + corroborated/reconstructed → no cap from source grade
  Any B (uncorroborated)            → cap at MODEL_QUALIFIED_HOLD
  Any C or D                        → cap at WATCH
  Missing timestamp on critical src  → cap at WATCH regardless of grade
  SOURCE_CONFLICT (any grade)        → block money labels until resolved

Critical-path sources: line/price, status/role, L5/L10 values, market consensus.
Non-critical sources: noted but do not by themselves cap approval.
"""
from __future__ import annotations

from typing import Any

from .labels import PropLabel

# ---------------------------------------------------------------------------
# Grade definitions and rank
# ---------------------------------------------------------------------------

GRADE_RANK: dict[str, int] = {
    "A":  5,
    "A-": 4,
    "B":  3,
    "C":  2,
    "D":  1,
    "N/T": 0,   # No timestamp → worst
}

# Source-type → default grade mapping (can be overridden per call)
SOURCE_TYPE_GRADES: dict[str, str] = {
    "api_feed":           "A",
    "stat_feed":          "A",
    "odds_api":           "A",
    "sportsbook_api":     "A",
    "box_score":          "A-",
    "official_gamelog":   "A-",
    "statmuse":           "B",
    "basketball_reference": "B",
    "bbref":              "B",
    "her_hoop_stats":     "B",
    "across_the_timeline": "B",
    "bettingpros":        "B",
    "establish_the_run":  "B",
    "daily_fantasy_fuel": "B",
    "rotowire":           "B",
    "article":            "C",
    "preview":            "C",
    "blurb":              "C",
    "tweet":              "D",
    "social_report":      "D",
    "screenshot":         "D",
    "pikkit":             "D",
}

# Critical-path sources that control the approval cap
CRITICAL_PATH_SOURCES = {
    "line_price",
    "status_role",
    "l5_l10",
    "market_consensus",
}

# Grade → approval ceiling (worst grade among critical-path sources wins)
GRADE_CEILING: dict[str, str | None] = {
    "A":   None,                                   # no cap
    "A-":  None,                                   # no cap
    "B":   PropLabel.MODEL_QUALIFIED_HOLD.value,   # cap unless corroborated
    "C":   PropLabel.RESEARCH_INTEREST.value,      # Watch equiv
    "D":   PropLabel.RESEARCH_INTEREST.value,      # Watch equiv
    "N/T": PropLabel.RESEARCH_INTEREST.value,      # Watch equiv
}


# ---------------------------------------------------------------------------
# Grading helpers
# ---------------------------------------------------------------------------

def grade_source(source_type: str, has_timestamp: bool = True) -> str:
    """Return the grade for a given source type + timestamp availability."""
    base = SOURCE_TYPE_GRADES.get(source_type.lower().replace(" ", "_"), "C")
    if not has_timestamp:
        return "N/T"
    return base


def worst_grade(grades: list[str]) -> str:
    """Return the lowest-ranked grade from a list."""
    if not grades:
        return "A"
    return min(grades, key=lambda g: GRADE_RANK.get(g, 0))


# ---------------------------------------------------------------------------
# Main validator
# ---------------------------------------------------------------------------

def run(
    row: dict[str, Any],
    sources: list[dict[str, Any]] | None = None,
    enrichment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Grade all critical-path sources and determine if any cap applies.

    sources — list of dicts, each with:
        {
          name:          str   (e.g. "StatMuse")
          source_type:   str   (key into SOURCE_TYPE_GRADES)
          role:          str   ("line_price"|"status_role"|"l5_l10"|"market_consensus"|other)
          has_timestamp: bool  (default True)
          corroborated:  bool  (default False)  — for grade-B sources
          grade_override: str | None  (optional explicit grade)
        }

    Returns:
        {
          passed:            bool
          source_grades:     list[dict]   — grade per source
          critical_grades:   list[str]    — grades for critical-path sources only
          worst_critical:    str          — worst grade among critical sources
          ceiling:           str | None   — label cap from source grades
          source_conflict:   bool
          code:              str
          detail:            str
        }
    """
    enr = enrichment or {}
    raw_sources: list[dict[str, Any]] = sources or enr.get("sources") or []

    graded: list[dict[str, Any]] = []
    critical_grades: list[str] = []
    conflict = False

    for src in raw_sources:
        stype      = src.get("source_type", "")
        role       = src.get("role", "other")
        has_ts     = src.get("has_timestamp", True)
        corroborated = src.get("corroborated", False)
        override   = src.get("grade_override")

        if override:
            grade = override
        else:
            grade = grade_source(stype, has_ts)

        # Upgrade grade-B if corroborated/reconstructed (removes auto-cap)
        effective_grade = grade
        if grade == "B" and corroborated:
            effective_grade = "B+"   # treated as no-cap for ceiling purposes
            GRADE_RANK["B+"] = GRADE_RANK["A-"] - 1  # between B and A-

        is_critical = role in CRITICAL_PATH_SOURCES
        if is_critical:
            critical_grades.append(effective_grade)

        if src.get("source_conflict"):
            conflict = True

        graded.append({
            "name":           src.get("name", stype),
            "source_type":    stype,
            "role":           role,
            "grade":          grade,
            "effective_grade": effective_grade,
            "has_timestamp":  has_ts,
            "corroborated":   corroborated,
            "is_critical":    is_critical,
        })

    # Determine worst critical-path grade
    if not critical_grades:
        worst = "A"   # no critical sources provided → no cap from this module
    else:
        worst = worst_grade([g for g in critical_grades if g != "B+"])
        # If all grades are B+ (corroborated), treat as no-cap
        if not [g for g in critical_grades if g != "B+"]:
            worst = "A-"

    ceiling: str | None = None
    if conflict:
        # SOURCE_CONFLICT blocks money labels — cap below MONEY_QUALIFIED
        ceiling = PropLabel.MODEL_QUALIFIED_HOLD.value
        code    = "SOURCE_CONFLICT"
    elif worst in ("C", "D", "N/T"):
        ceiling = GRADE_CEILING[worst]
        code    = f"SOURCE_GRADE_DEGRADED:{worst}"
    elif worst == "B":
        ceiling = GRADE_CEILING["B"]
        code    = "SOURCE_GRADE_B_UNCORROBORATED"
    else:
        code = "SOURCE_GRADE_OK"

    passed = ceiling is None and not conflict

    result: dict[str, Any] = {
        "passed":          passed,
        "source_grades":   graded,
        "critical_grades": critical_grades,
        "worst_critical":  worst,
        "ceiling":         ceiling,
        "source_conflict": conflict,
        "code":            code,
        "detail":          _build_detail(worst, ceiling, conflict, len(critical_grades)),
    }

    row.setdefault("gates", {})["source_grade"] = result

    if not passed:
        row["blockers"].append(f"SOURCE_GRADE:{code}:ceiling={ceiling}")
        if not row.get("terminal_label") and conflict:
            row["terminal_label"] = PropLabel.SOURCE_CONFLICT.value
        elif ceiling and not row.get("terminal_label"):
            row["label_ceiling"] = ceiling

    return result


def _build_detail(worst: str, ceiling: str | None,
                  conflict: bool, n_critical: int) -> str:
    if conflict:
        return f"SOURCE_CONFLICT detected — money labels blocked until resolved"
    if ceiling:
        return (f"Worst critical-path source grade: {worst} — "
                f"approval capped at {ceiling} (n_critical={n_critical})")
    return (f"All critical-path sources grade A/A- "
            f"— no source-grade cap (n_critical={n_critical})")
