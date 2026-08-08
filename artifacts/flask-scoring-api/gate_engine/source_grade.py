"""
source_grade.py  —  Module H: Source Timestamp Grading + Reconciliation Doctrine
WOW v16 / Section 4.3

Every source used in prop analysis must carry a grade based on source type
and freshness. Grade controls the approval cap.

SOURCE GRADES:
  A   — Direct API / stat feed with timestamp            Full trust — T0
  A-  — Official box score / gamelog                     High trust — T1
  B   — Trusted stat site (StatMuse, BBRef, Her Hoop…)  Reliable — T1
  C   — Article / preview / blurb / web search           Context only — T3
  D   — Screenshot / social report / consumer weather    Cannot verify
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

─────────────────────────────────────────────────────────────────────────────
SOURCE ARCHITECTURE (complete precedence order):

  1. Official APIs and direct feeds          ← preferred whenever available
  2. Public / free research websites         ← automatic backup/reconstruction
  3. Web search and news verification        ← context and corroboration only
  4. User screenshots and board captures     ← lowest priority; never authoritative

  → source reconciliation (this module)
  → structured PostgreSQL ledger
  → specialist models and gates

─────────────────────────────────────────────────────────────────────────────
RECONCILIATION RULES (five hard doctrines):

  1. STATMUSE / B-GRADE STAT SITES — EXACT STAT OR DATE ROLES
     StatMuse may be excellent for quick historical queries, but results must
     be reconciled against official logs when the exact stat or date matters.
     Trigger: source_type in STAT_RECONCILIATION_REQUIRED_SOURCES
              AND role in {"l5_l10", "exact_stat", "game_log"}
              AND corroborated=False
     Blocker: RECONCILIATION_REQUIRED:<source_type>:needs_official_log

  2. ESPN BLURBS — STALE SEASON AVERAGES
     ESPN blurbs may contain stale season averages; live game-log data takes
     priority. A row whose only L5/L10 source is an ESPN blurb is capped and
     flagged for replacement by a live feed.
     Trigger: source_type == "espn_blurb" AND role in {"l5_l10", "game_log"}
     Blocker: ESPN_BLURB_STALE_AVERAGES:live_game_log_required

  3. ODDS AGGREGATORS — EXACT-LINE AUDIT REQUIRED
     Odds aggregators may show delayed or mismatched lines; an exact-line
     audit must still confirm the same line, period, rules, and both sides
     before the row qualifies for any money label.
     Trigger: source_type in ODDS_AGGREGATOR_SOURCES AND role == "line_price"
     Blocker: EXACT_LINE_AUDIT_REQUIRED:<source_type>

  4. CONSUMER WEATHER SITES — CANNOT REPLACE KALSHI SETTLEMENT STATION
     Consumer weather sites (weather.com, Weather Underground, etc.) cannot
     replace the official NWS CLI settlement station for Kalshi markets.
     Only official_weather_station / nws_cli sources are valid for the
     kalshi_weather role.
     Trigger: source_type in CONSUMER_WEATHER_SOURCES AND role == "kalshi_weather"
     Blocker: WEATHER_SOURCE_INVALID_FOR_SETTLEMENT:<source_type>

  5. PRIZEPICKS / ANY SCREENSHOT — LINE ACTIVE STATUS UNCONFIRMED
     A public PrizePicks screenshot (or any screenshot) does not prove that
     the line is still active. A live board query must confirm the line
     before it can be treated as current.
     Trigger: source_type in SCREENSHOT_SOURCES AND role == "line_price"
     Blocker: LINE_ACTIVE_UNCONFIRMED:<source_type>
─────────────────────────────────────────────────────────────────────────────
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
    # ── Tier A: official APIs and direct feeds ──────────────────────────────
    "api_feed":                "A",
    "stat_feed":               "A",
    "odds_api":                "A",
    "sportsbook_api":          "A",
    "official_feed":           "A",
    "nws_cli":                 "A",   # NWS CLI — official Kalshi settlement source
    "official_weather_station": "A",  # alias for nws_cli
    "espn_api":                "A-",  # ESPN public API (event identity only, not odds)

    # ── Tier A-: official records + TRUSTED_STRUCTURED_STATS direct APIs ────
    "box_score":               "A-",
    "official_gamelog":        "A-",
    # BallDontLie: TRUSTED_STRUCTURED_STATS — direct timestamped API,
    # below official league feeds, above B-grade stat-site reconstruction.
    # Grade A- because: direct API with retrieval timestamp, structured verified
    # game records, machine-readable IDs (game/player/team), real-time capable.
    # Does NOT carry official league authority → not Tier A.
    # SOURCE_CONFLICT with any A-grade source still blocks money labels.
    "balldontlie_api":         "A-",
    "balldontlie":             "A-",  # alias used in existing auto_game_log.py

    # ── Tier B: trusted stat and research sites ──────────────────────────────
    "statmuse":                "B",
    "basketball_reference":    "B",
    "bbref":                   "B",
    "her_hoop_stats":          "B",
    "across_the_timeline":     "B",
    "bettingpros":             "B",
    "establish_the_run":       "B",
    "daily_fantasy_fuel":      "B",
    "rotowire":                "B",
    # Odds aggregators: grade B but trigger EXACT_LINE_AUDIT_REQUIRED on line_price
    "odds_aggregator":         "B",
    "action_network":          "B",
    "donbest":                 "B",
    "covers":                  "B",
    "vegasinsider":            "B",
    "thelines":                "B",

    # ── Tier C: articles, blurbs, web search, news ──────────────────────────
    "article":                 "C",
    "preview":                 "C",
    "blurb":                   "C",
    "espn_blurb":              "C",   # ESPN article/blurb — may have stale averages
    "espn_article":            "C",
    "web_search":              "C",
    "news_article":            "C",
    "beat_reporter":           "C",
    # Consumer weather: grade C normally, but D (and hard blocker) for kalshi_weather role
    "consumer_weather_site":   "C",
    "weather_underground":     "C",
    "weather_dot_com":         "C",
    "wunderground":            "C",

    # ── Tier D: screenshots, social, unverified ──────────────────────────────
    "tweet":                   "D",
    "social_report":           "D",
    "screenshot":              "D",
    "pikkit":                  "D",
    "prizepicks_screenshot":   "D",   # PrizePicks board screenshot
    "board_capture":           "D",   # any manual board screenshot/capture
    "user_supplied":           "D",
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
# Reconciliation rule sets (Doctrine rules 1–5)
# ---------------------------------------------------------------------------

# Rule 1 — B-grade stat sites that need official-log reconciliation when
#           used for exact stat / exact date queries
STAT_RECONCILIATION_REQUIRED_SOURCES: frozenset[str] = frozenset({
    "statmuse",
    "basketball_reference",
    "bbref",
    "her_hoop_stats",
    "across_the_timeline",
})

# Rule 1 — roles that trigger reconciliation for the above sources
STAT_RECONCILIATION_ROLES: frozenset[str] = frozenset({
    "l5_l10",
    "exact_stat",
    "game_log",
})

# Rule 3 — odds aggregator source types (trigger EXACT_LINE_AUDIT on line_price)
ODDS_AGGREGATOR_SOURCES: frozenset[str] = frozenset({
    "odds_aggregator",
    "action_network",
    "donbest",
    "covers",
    "vegasinsider",
    "thelines",
})

# Rule 4 — consumer weather sources (invalid for kalshi_weather role)
CONSUMER_WEATHER_SOURCES: frozenset[str] = frozenset({
    "consumer_weather_site",
    "weather_underground",
    "weather_dot_com",
    "wunderground",
})

# Official weather sources valid for Kalshi settlement
OFFICIAL_WEATHER_SOURCES: frozenset[str] = frozenset({
    "nws_cli",
    "official_weather_station",
})

# Rule 5 — screenshot source types (trigger LINE_ACTIVE_UNCONFIRMED on line_price)
SCREENSHOT_SOURCES: frozenset[str] = frozenset({
    "screenshot",
    "prizepicks_screenshot",
    "board_capture",
    "user_supplied",
    "pikkit",
})


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


def _check_reconciliation_rules(
    stype: str,
    role: str,
    corroborated: bool,
) -> list[str]:
    """
    Return a list of blocker strings triggered by the five reconciliation
    doctrines for a single source+role combination.
    """
    blockers: list[str] = []
    stype_norm = stype.lower().replace(" ", "_")

    # Rule 1: B-grade stat site + exact-stat role + uncorroborated
    if (stype_norm in STAT_RECONCILIATION_REQUIRED_SOURCES
            and role in STAT_RECONCILIATION_ROLES
            and not corroborated):
        blockers.append(
            f"RECONCILIATION_REQUIRED:{stype_norm}:needs_official_log"
        )

    # Rule 2: ESPN blurb used for game-log / L5/L10 role
    if stype_norm in {"espn_blurb", "espn_article"} and role in {"l5_l10", "game_log"}:
        blockers.append("ESPN_BLURB_STALE_AVERAGES:live_game_log_required")

    # Rule 3: Odds aggregator used for line_price
    if stype_norm in ODDS_AGGREGATOR_SOURCES and role == "line_price":
        blockers.append(f"EXACT_LINE_AUDIT_REQUIRED:{stype_norm}")

    # Rule 4: Consumer weather used for Kalshi settlement
    if stype_norm in CONSUMER_WEATHER_SOURCES and role == "kalshi_weather":
        blockers.append(
            f"WEATHER_SOURCE_INVALID_FOR_SETTLEMENT:{stype_norm}"
        )

    # Rule 5: Screenshot used for line_price (doesn't prove line is still active)
    if stype_norm in SCREENSHOT_SOURCES and role == "line_price":
        blockers.append(f"LINE_ACTIVE_UNCONFIRMED:{stype_norm}")

    return blockers


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
          name:           str   (e.g. "StatMuse")
          source_type:    str   (key into SOURCE_TYPE_GRADES)
          role:           str   ("line_price"|"status_role"|"l5_l10"|
                                 "market_consensus"|"kalshi_weather"|
                                 "exact_stat"|"game_log"|other)
          has_timestamp:  bool  (default True)
          corroborated:   bool  (default False)  — for grade-B sources
          grade_override: str | None  (optional explicit grade)
          source_conflict: bool (default False)
        }

    Returns:
        {
          passed:                   bool
          source_grades:            list[dict]
          critical_grades:          list[str]
          worst_critical:           str
          ceiling:                  str | None
          source_conflict:          bool
          reconciliation_blockers:  list[str]   ← doctrine rule violations
          code:                     str
          detail:                   str
        }
    """
    enr = enrichment or {}
    raw_sources: list[dict[str, Any]] = sources or enr.get("sources") or []

    graded: list[dict[str, Any]] = []
    critical_grades: list[str] = []
    conflict = False
    reconciliation_blockers: list[str] = []

    for src in raw_sources:
        stype        = (src.get("source_type") or "").lower().replace(" ", "_")
        role         = src.get("role", "other")
        has_ts       = src.get("has_timestamp", True)
        corroborated = src.get("corroborated", False)
        override     = src.get("grade_override")

        if override:
            grade = override
        else:
            grade = grade_source(stype, has_ts)

        # Consumer weather used for Kalshi settlement → downgrade to D regardless
        if stype in CONSUMER_WEATHER_SOURCES and role == "kalshi_weather":
            grade = "D"

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

        # Check all five reconciliation doctrine rules for this source
        rec_blockers = _check_reconciliation_rules(stype, role, corroborated)
        reconciliation_blockers.extend(rec_blockers)

        graded.append({
            "name":                  src.get("name", stype),
            "source_type":           stype,
            "role":                  role,
            "grade":                 grade,
            "effective_grade":       effective_grade,
            "has_timestamp":         has_ts,
            "corroborated":          corroborated,
            "is_critical":           is_critical,
            "reconciliation_flags":  rec_blockers,
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

    # Reconciliation blockers add an additional RESEARCH_INTEREST ceiling
    # (they do not block worse than RESEARCH_INTEREST but surface explicitly)
    rec_ceiling: str | None = None
    if reconciliation_blockers:
        rec_ceiling = PropLabel.RESEARCH_INTEREST.value
        if ceiling is None:
            ceiling = rec_ceiling
            if code == "SOURCE_GRADE_OK":
                code = "RECONCILIATION_REQUIRED"

    passed = ceiling is None and not conflict

    result: dict[str, Any] = {
        "passed":                  passed,
        "source_grades":           graded,
        "critical_grades":         critical_grades,
        "worst_critical":          worst,
        "ceiling":                 ceiling,
        "source_conflict":         conflict,
        "reconciliation_blockers": reconciliation_blockers,
        "code":                    code,
        "detail":                  _build_detail(worst, ceiling, conflict,
                                                  len(critical_grades),
                                                  reconciliation_blockers),
    }

    row.setdefault("gates", {})["source_grade"] = result

    # Stamp grade-level blockers
    if not passed:
        row["blockers"].append(f"SOURCE_GRADE:{code}:ceiling={ceiling}")
        if not row.get("terminal_label") and conflict:
            row["terminal_label"] = PropLabel.SOURCE_CONFLICT.value
        elif ceiling and not row.get("terminal_label"):
            row["label_ceiling"] = ceiling

    # Stamp reconciliation blockers separately (surfaced even when grade passes)
    for rb in reconciliation_blockers:
        if rb not in row.get("blockers", []):
            row.setdefault("blockers", []).append(rb)

    return result


def _build_detail(
    worst: str,
    ceiling: str | None,
    conflict: bool,
    n_critical: int,
    reconciliation_blockers: list[str] | None = None,
) -> str:
    parts: list[str] = []
    if conflict:
        parts.append("SOURCE_CONFLICT detected — money labels blocked until resolved")
    elif ceiling:
        parts.append(
            f"Worst critical-path source grade: {worst} — "
            f"approval capped at {ceiling} (n_critical={n_critical})"
        )
    else:
        parts.append(
            f"All critical-path sources grade A/A- "
            f"— no source-grade cap (n_critical={n_critical})"
        )
    if reconciliation_blockers:
        parts.append(
            f"Reconciliation doctrine flags: {', '.join(reconciliation_blockers)}"
        )
    return " | ".join(parts)
