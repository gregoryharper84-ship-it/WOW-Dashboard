"""
calibration_health.py
Layer 0.5: Calibration Health Gate — WOW v16 Clean Core

Runs BEFORE prop analysis. Reads historical calibration ledger entries and
evaluates failure patterns across six dimensions using blended CLV + result
signals. Does NOT use raw loss count alone — variance requires CLV confirmation.

Blended suppression rules (ChatGPT review 2026-06-27):
  3  same-tag failures (any CLV)          → WATCH ceiling (warning)
  5+ same-tag failures + negative CLV     → WATCH max (bucket downgrade)
  8+ failures across dimension OR neg EV  → SUPPRESS (auto-suppress / REJECT max)
  CLV positive + results negative         → VARIANCE_HOLD (no downgrade, note only)
  CLV negative + results negative         → strongest downgrade (SUPPRESS)

CLV 4-quadrant (Gap 2 from ChatGPT review):
  CLV+ / Result+ → GREEN   (promote — strong signal)
  CLV+ / Result- → WATCH   (variance hold — good process, bad luck)
  CLV- / Result+ → WATCH   (lucky run — do not promote)
  CLV- / Result- → SUPPRESS (worst signal — suppress)
  No CLV data    → DATA_GAP (approval cap, note only)

This module classifies and logs. It does NOT approve bets.
Final betting decisions remain with LLP/WOW.
"""
from __future__ import annotations

import json
import os
from enum import Enum
from typing import Any

from .llp_governance import (
    CALLEDGER_PATH, LLPLabel,
    _ok, _fail,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_SAMPLE = 5         # fewer records → DATA_GAP (no downgrade)
WATCH_TAG_THRESHOLD    = 3   # 3+ same-tag failures → WATCH warning
DOWNGRADE_TAG_THRESHOLD = 5  # 5+ same-tag failures + neg CLV → WATCH max
SUPPRESS_THRESHOLD     = 8   # 8+ failures across dimension → SUPPRESS

# Negative CLV cutoff (mean CLV below this = "negative CLV bucket")
NEGATIVE_CLV_CUTOFF = -0.005   # −0.5% implied-prob CLV

# Result values expected in calibration ledger
WIN  = "WIN"
LOSS = "LOSS"
PUSH = "PUSH"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class HealthGrade(str, Enum):
    GREEN       = "GREEN"        # clear — no historical concerns
    WATCH       = "WATCH"        # cap at LLP_WATCH
    SUPPRESS    = "SUPPRESS"     # cap at LLP_REJECT (auto-suppress)
    DATA_GAP    = "DATA_GAP"     # insufficient sample — no downgrade, note only


class CLVQuadrant(str, Enum):
    PROMOTE       = "CLV_POS_RESULT_POS"   # strong signal
    VARIANCE_HOLD = "CLV_POS_RESULT_NEG"   # good process, bad luck
    LUCKY         = "CLV_NEG_RESULT_POS"   # bad process, good luck
    SUPPRESS      = "CLV_NEG_RESULT_NEG"   # suppress
    NO_DATA       = "CLV_NO_DATA"          # no CLV available


# Map HealthGrade → LLP label ceiling
GRADE_CEILING = {
    HealthGrade.GREEN:    None,                    # no cap
    HealthGrade.WATCH:    LLPLabel.WATCH.value,
    HealthGrade.SUPPRESS: LLPLabel.REJECT.value,
    HealthGrade.DATA_GAP: LLPLabel.WATCH.value,    # soft cap while building history
}


# ---------------------------------------------------------------------------
# Ledger reader
# ---------------------------------------------------------------------------

def _load_ledger() -> list[dict[str, Any]]:
    """Load all calibration ledger entries."""
    try:
        with open(CALLEDGER_PATH) as f:
            return [json.loads(line) for line in f if line.strip()]
    except (OSError, json.JSONDecodeError):
        return []


def _parse_clv(entry: dict) -> float | None:
    """Return CLV value from a ledger entry (numeric or None)."""
    raw = entry.get("clv")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _parse_result(entry: dict) -> str | None:
    raw = (entry.get("result") or "").upper().strip()
    return raw if raw in (WIN, LOSS, PUSH) else None


# ---------------------------------------------------------------------------
# CLV 4-quadrant classifier
# ---------------------------------------------------------------------------

def _clv_quadrant(records: list[dict]) -> CLVQuadrant:
    """
    Classify a set of calibration records into one of the 4 CLV quadrants.

    CLV is computed as the mean CLV across records with data.
    Result is computed as the win rate across records with WIN/LOSS.

    CLV+ = mean_clv >= NEGATIVE_CLV_CUTOFF
    Result+ = win_rate >= 0.50
    """
    clv_values  = [v for r in records if (v := _parse_clv(r)) is not None]
    result_vals = [_parse_result(r) for r in records]
    wins   = sum(1 for v in result_vals if v == WIN)
    losses = sum(1 for v in result_vals if v == LOSS)
    graded = wins + losses

    if not clv_values:
        return CLVQuadrant.NO_DATA

    mean_clv  = sum(clv_values) / len(clv_values)
    clv_pos   = mean_clv >= NEGATIVE_CLV_CUTOFF
    result_pos = (wins / graded >= 0.50) if graded else True   # unknown = neutral

    if clv_pos and result_pos:
        return CLVQuadrant.PROMOTE
    if clv_pos and not result_pos:
        return CLVQuadrant.VARIANCE_HOLD
    if not clv_pos and result_pos:
        return CLVQuadrant.LUCKY
    return CLVQuadrant.SUPPRESS


# ---------------------------------------------------------------------------
# Dimension scorer
# ---------------------------------------------------------------------------

def _score_dimension(records: list[dict], dimension_name: str,
                     failure_tags: list[str] | None = None
                     ) -> dict[str, Any]:
    """
    Score a filtered set of calibration records for one dimension.

    Returns:
      {
        grade:      HealthGrade
        quadrant:   CLVQuadrant
        ceiling:    str | None
        code:       str
        detail:     str
        total:      int
        failures:   int
        tag_hits:   int      (matching failure_tags)
        mean_clv:   float | None
        win_rate:   float | None
      }
    """
    total = len(records)

    if total < MIN_SAMPLE:
        return {
            "grade":   HealthGrade.DATA_GAP,
            "quadrant": CLVQuadrant.NO_DATA,
            "ceiling": GRADE_CEILING[HealthGrade.DATA_GAP],
            "code":    "DATA_GAP",
            "detail":  f"{dimension_name}: only {total} records (< {MIN_SAMPLE} minimum)",
            "total":   total, "failures": 0, "tag_hits": 0,
            "mean_clv": None, "win_rate": None,
        }

    # Count failures (LOSS results)
    result_vals = [_parse_result(r) for r in records]
    losses  = sum(1 for v in result_vals if v == LOSS)
    wins    = sum(1 for v in result_vals if v == WIN)
    graded  = wins + losses
    win_rate = round(wins / graded, 3) if graded else None

    # Count tag matches
    tag_hits = 0
    if failure_tags:
        for r in records:
            r_tags = r.get("failure_tags") or []
            if isinstance(r_tags, str):
                r_tags = [r_tags]
            if any(t in r_tags for t in failure_tags):
                tag_hits += 1

    # Compute CLV health
    clv_values = [v for r in records if (v := _parse_clv(r)) is not None]
    mean_clv = round(sum(clv_values) / len(clv_values), 4) if clv_values else None
    quadrant = _clv_quadrant(records)

    # Apply blended rules (order matters).
    #
    # VARIANCE_HOLD principle (ChatGPT review): positive CLV + bad results =
    # good process, bad luck — do NOT auto-suppress. But tag warnings still fire
    # because repeated failure-tag patterns signal data/tooling issues independent
    # of whether the process otherwise beats closing lines.
    is_variance_hold = (quadrant == CLVQuadrant.VARIANCE_HOLD)

    grade  = HealthGrade.GREEN
    code   = "HEALTHY"
    detail = f"{dimension_name}: {total} records, {losses} losses, quadrant={quadrant.value}"

    # Rule 1: 8+ failures → auto-suppress (blocked when CLV is positive — variance hold)
    if losses >= SUPPRESS_THRESHOLD and not is_variance_hold:
        grade  = HealthGrade.SUPPRESS
        code   = "AUTO_SUPPRESS"
        detail = (f"{dimension_name}: {losses} losses >= {SUPPRESS_THRESHOLD} "
                  f"auto-suppress threshold | quadrant={quadrant.value}")

    # Rule 2: CLV negative + result negative → suppress (strongest signal; not blocked)
    elif quadrant == CLVQuadrant.SUPPRESS and losses >= 3:
        grade  = HealthGrade.SUPPRESS
        code   = "CLV_NEG_RESULT_NEG_SUPPRESS"
        detail = (f"{dimension_name}: CLV negative (mean={mean_clv}) + "
                  f"{losses} losses → suppress | quadrant={quadrant.value}")

    # Rule 3: 5+ tag failures + negative CLV → WATCH downgrade
    # Tag rules run regardless of variance-hold — tags signal data/tooling issues.
    elif failure_tags and tag_hits >= DOWNGRADE_TAG_THRESHOLD and \
            mean_clv is not None and mean_clv < NEGATIVE_CLV_CUTOFF:
        grade  = HealthGrade.WATCH
        code   = "TAG_NEG_CLV_DOWNGRADE"
        detail = (f"{dimension_name}: {tag_hits} same-tag failures "
                  f"(>={DOWNGRADE_TAG_THRESHOLD}) + negative CLV ({mean_clv}) "
                  f"→ WATCH ceiling | quadrant={quadrant.value}")

    # Rule 4: 3+ tag failures (any CLV) → WATCH warning
    elif failure_tags and tag_hits >= WATCH_TAG_THRESHOLD:
        grade  = HealthGrade.WATCH
        code   = "TAG_FAILURE_WARNING"
        detail = (f"{dimension_name}: {tag_hits} matching failure tags "
                  f"(>={WATCH_TAG_THRESHOLD}) → WATCH ceiling | quadrant={quadrant.value}")

    # Rule 5: CLV negative + results positive → lucky, soft WATCH
    elif quadrant == CLVQuadrant.LUCKY and losses >= 2:
        grade  = HealthGrade.WATCH
        code   = "CLV_NEG_RESULT_POS_LUCKY"
        detail = (f"{dimension_name}: negative CLV but positive results — "
                  f"likely variance, do not promote | quadrant={quadrant.value}")

    # Rule 6: CLV positive + results negative → variance hold, no downgrade
    # Fires only when no tag or CLV-suppress rules applied above.
    elif is_variance_hold:
        grade  = HealthGrade.GREEN
        code   = "VARIANCE_HOLD"
        detail = (f"{dimension_name}: positive CLV but negative results — "
                  f"variance hold, no downgrade | quadrant={quadrant.value}")

    return {
        "grade":   grade,
        "quadrant": quadrant,
        "ceiling": GRADE_CEILING[grade],
        "code":    code,
        "detail":  detail,
        "total":   total,
        "failures": losses,
        "tag_hits": tag_hits,
        "mean_clv": mean_clv,
        "win_rate": win_rate,
    }


# ---------------------------------------------------------------------------
# Main validator
# ---------------------------------------------------------------------------

def validate_calibration_health(candidate: dict[str, Any]) -> dict[str, Any]:
    """
    Layer 0.5: Calibration Health Gate.

    Reads historical calibration records and checks three dimensions:
      1. failure_tags   — same failure-tag pattern history
      2. sport+market   — bucket-level health (sport × market)
      3. player         — player-specific history (if player field present)

    Returns:
      {
        passed:             bool
        grade:              str   (most restrictive across all dimensions)
        ceiling:            str | None   (LLP label ceiling)
        code:               str
        detail:             str
        dimension_results:  {failure_tags, sport_market, player}
        can_approve_bets:   False
      }
    """
    all_records = _load_ledger()

    failure_tags = candidate.get("failure_tags") or []
    if isinstance(failure_tags, str):
        failure_tags = [failure_tags]

    sport     = (candidate.get("sport")     or "").lower().strip()
    market    = (candidate.get("market")    or "").lower().strip()
    prop_type = (candidate.get("prop_type") or "").lower().strip()
    player    = (candidate.get("player")    or "").lower().strip()

    # --- Dimension 1: failure_tags ---
    if failure_tags:
        tag_records = [
            r for r in all_records
            if _has_any_tag(r, failure_tags)
        ]
    else:
        tag_records = all_records   # no tags → score full pool

    tag_result = _score_dimension(tag_records, "failure_tags", failure_tags)

    # --- Dimension 2: sport + market (bucket) ---
    bucket_records = [
        r for r in all_records
        if (not sport  or (r.get("sport") or "").lower()  == sport)
        and (not market or (r.get("market") or "").lower() == market)
    ]
    bucket_result = _score_dimension(bucket_records, f"sport={sport or '*'} market={market or '*'}")

    # --- Dimension 3: player ---
    if player:
        player_records = [
            r for r in all_records
            if (r.get("player") or "").lower() == player
        ]
        player_result = _score_dimension(player_records, f"player={player}")
    else:
        player_result = {
            "grade": HealthGrade.DATA_GAP, "quadrant": CLVQuadrant.NO_DATA,
            "ceiling": None, "code": "NO_PLAYER_KEY",
            "detail": "player field not provided — player dimension skipped",
            "total": 0, "failures": 0, "tag_hits": 0,
            "mean_clv": None, "win_rate": None,
        }

    # --- Aggregate: take most restrictive grade ---
    dimension_results = {
        "failure_tags": tag_result,
        "sport_market": bucket_result,
        "player":       player_result,
    }

    all_grades = [
        tag_result["grade"],
        bucket_result["grade"],
        player_result["grade"],
    ]
    worst_grade = _most_restrictive_grade(all_grades)
    ceiling     = GRADE_CEILING[worst_grade]

    # Collect blockers (non-green, non-data-gap)
    blockers = [
        f"{dim.upper()}:{res['code']}"
        for dim, res in dimension_results.items()
        if res["grade"] in (HealthGrade.WATCH, HealthGrade.SUPPRESS)
    ]

    # Find the driving detail
    driving = max(
        dimension_results.items(),
        key=lambda kv: _grade_rank(kv[1]["grade"])
    )
    detail = driving[1]["detail"]

    passed = worst_grade not in (HealthGrade.SUPPRESS,)

    return {
        "passed":            passed,
        "grade":             worst_grade.value,
        "ceiling":           ceiling,
        "code":              _worst_code(dimension_results),
        "detail":            detail,
        "blockers":          blockers,
        "dimension_results": {
            k: {**v, "grade": v["grade"].value, "quadrant": v["quadrant"].value}
            for k, v in dimension_results.items()
        },
        "can_approve_bets":  False,
    }


# ---------------------------------------------------------------------------
# Health summary (for GET endpoint / dashboard)
# ---------------------------------------------------------------------------

def get_health_summary() -> dict[str, Any]:
    """
    Return a high-level health summary across key dimensions:
      - by sport
      - by market
      - by failure_tag (top 10 tags by frequency)
      - overall quadrant
      - total records in ledger

    Designed for the GET /gate-engine/calibration-health/summary endpoint.
    """
    all_records = _load_ledger()
    total = len(all_records)

    if total == 0:
        return {
            "total_records": 0,
            "overall_quadrant": CLVQuadrant.NO_DATA.value,
            "grade": HealthGrade.DATA_GAP.value,
            "by_sport": {},
            "by_market": {},
            "by_failure_tag": {},
            "can_approve_bets": False,
        }

    overall_quadrant = _clv_quadrant(all_records)

    # By sport
    sports: dict[str, list] = {}
    for r in all_records:
        s = (r.get("sport") or "UNKNOWN").upper()
        sports.setdefault(s, []).append(r)
    by_sport = {
        s: _summarize(recs) for s, recs in sports.items()
    }

    # By market
    markets: dict[str, list] = {}
    for r in all_records:
        m = (r.get("market") or "UNKNOWN").lower()
        markets.setdefault(m, []).append(r)
    by_market = {
        m: _summarize(recs) for m, recs in markets.items()
    }

    # By failure tag (top 10 by freq)
    tag_map: dict[str, list] = {}
    for r in all_records:
        tags = r.get("failure_tags") or []
        if isinstance(tags, str):
            tags = [tags]
        for t in tags:
            tag_map.setdefault(t, []).append(r)
    top_tags = sorted(tag_map.items(), key=lambda kv: len(kv[1]), reverse=True)[:10]
    by_failure_tag = {tag: _summarize(recs) for tag, recs in top_tags}

    return {
        "total_records":     total,
        "overall_quadrant":  overall_quadrant.value,
        "grade":             HealthGrade.GREEN.value if overall_quadrant in (
            CLVQuadrant.PROMOTE, CLVQuadrant.VARIANCE_HOLD) else HealthGrade.WATCH.value,
        "by_sport":          by_sport,
        "by_market":         by_market,
        "by_failure_tag":    by_failure_tag,
        "can_approve_bets":  False,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_any_tag(record: dict, tags: list[str]) -> bool:
    r_tags = record.get("failure_tags") or []
    if isinstance(r_tags, str):
        r_tags = [r_tags]
    return any(t in r_tags for t in tags)


_GRADE_ORDER = [
    HealthGrade.GREEN,
    HealthGrade.DATA_GAP,
    HealthGrade.WATCH,
    HealthGrade.SUPPRESS,
]


def _grade_rank(grade: HealthGrade) -> int:
    try:
        return _GRADE_ORDER.index(grade)
    except ValueError:
        return 0


def _most_restrictive_grade(grades: list[HealthGrade]) -> HealthGrade:
    return max(grades, key=_grade_rank)


def _worst_code(dimension_results: dict) -> str:
    worst = max(
        dimension_results.values(),
        key=lambda v: _grade_rank(v["grade"])
    )
    return worst["code"]


def _summarize(records: list[dict]) -> dict[str, Any]:
    """Compact summary for a record group."""
    result_vals = [_parse_result(r) for r in records]
    wins   = sum(1 for v in result_vals if v == WIN)
    losses = sum(1 for v in result_vals if v == LOSS)
    graded = wins + losses
    clv_values = [v for r in records if (v := _parse_clv(r)) is not None]
    return {
        "total":    len(records),
        "wins":     wins,
        "losses":   losses,
        "win_rate": round(wins / graded, 3) if graded else None,
        "mean_clv": round(sum(clv_values) / len(clv_values), 4) if clv_values else None,
        "quadrant": _clv_quadrant(records).value,
    }
