"""
portfolio_governor.py  —  Recovery Mode caps + ranking + final pool
WOW v16.5 Category-Router / Singles-Governor Layer

Enforces Recovery Mode caps on every gate-passing candidate, then ranks
survivors and returns the final pool (max 2, max 1 per event/position).

Recovery Mode caps (applied per candidate, in order):
  1. Singles only — any multi-leg structure → immediate reject
  2. Max 2 research-eligible contracts/day (weather + sports combined)
  3. Max 1 contract per event (event_id deduplication)
  4. Max 1 weather contract per city+date combination
  5. No narrow-bracket YES (weather bracket covering <=1°F span)
  6. No same-city multiple bracket contracts
  7. No multi-city combos (already blocked at category_router, redundant guard here)

Ranking of survivors (hard gates already passed):
  1. net_edge_lower_bound descending    (primary — stronger edge first)
  2. calibration_strength descending    (higher calibration history score first)
  3. model_uncertainty ascending        (lower uncertainty first)
  4. price_age_minutes ascending        (freshest orderbook first)
  5. calibrated_prob_lower_bound desc   (stronger probability signal first)
  6. settlement_clarity_grade score desc (A=5 > B=4 > C=3 > D=2 > F=1)
  7. spread_cents ascending             (lowest friction first)
  8. exposure_overlap ascending         (no existing exposure first: False < True)

Final pool: top 2 survivors, additionally enforcing max 1 per event/position.
Zero or one is valid and expected — never an error.
"""
from __future__ import annotations

from typing import Any

# Final pool hard cap
_MAX_POOL_SIZE     = 2
_MAX_PER_EVENT     = 1

# Narrow bracket threshold (°F span)
_NARROW_BRACKET_MAX_SPAN = 1.0

_GRADE_RANK = {"A": 5, "B": 4, "C": 3, "D": 2, "F": 1}


def check_single(candidate: dict[str, Any], day_pool: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Check whether one candidate passes Recovery Mode caps given what is
    already in `day_pool` (candidates already accepted for today).

    Used by the weather_gate and sports_gate pipelines (gate 12 / pre-gate).

    Parameters
    ----------
    candidate — gate-enriched candidate dict with:
      category            "weather" | "sports_winner"
      event_id            str | None
      city                str | None   (weather only)
      scan_date           str | None   YYYY-MM-DD
      bracket_span_f      float | None (weather only — °F span of the bracket)
      is_multi_leg        bool
    day_pool — candidates already accepted today

    Returns
    -------
    dict with: passed bool, rejection_reason str | None
    """
    # Cap 1: singles only
    if candidate.get("is_multi_leg", False):
        return {"passed": False, "rejection_reason": "RECOVERY_CAP_SINGLES_ONLY"}

    research_today = [c for c in day_pool if c.get("research_eligible", True)]
    # Cap 2: max 2 research-eligible/day
    if len(research_today) >= 2:
        return {"passed": False, "rejection_reason": "RECOVERY_CAP_MAX_2_PER_DAY"}

    # Cap 3: max 1 per event
    event_id = candidate.get("event_id")
    if event_id:
        same_event = [c for c in day_pool if c.get("event_id") == event_id]
        if same_event:
            return {"passed": False, "rejection_reason": "RECOVERY_CAP_MAX_1_PER_EVENT"}

    if candidate.get("category") == "weather":
        city      = (candidate.get("city") or "").upper()
        scan_date = candidate.get("scan_date") or ""

        # Cap 4: max 1 weather per city+date
        same_city_date = [
            c for c in day_pool
            if c.get("category") == "weather"
            and (c.get("city") or "").upper() == city
            and c.get("scan_date") == scan_date
        ]
        if same_city_date:
            return {"passed": False, "rejection_reason": "RECOVERY_CAP_MAX_1_WEATHER_PER_CITY_DATE"}

        # Cap 5: no narrow-bracket YES
        bracket_span = candidate.get("bracket_span_f")
        if bracket_span is not None and bracket_span <= _NARROW_BRACKET_MAX_SPAN:
            return {"passed": False, "rejection_reason": "RECOVERY_CAP_NO_NARROW_BRACKET_YES"}

        # Cap 6: no same-city multi-bracket
        same_city = [
            c for c in day_pool
            if c.get("category") == "weather"
            and (c.get("city") or "").upper() == city
        ]
        if same_city:
            return {"passed": False, "rejection_reason": "RECOVERY_CAP_NO_SAME_CITY_MULTI_BRACKET"}

    # Cap 7: no multi-city combos (already blocked upstream, redundant guard)
    # (a combo never reaches here — category_router gates it at KALSHI_REJECT_COMBO_DISABLED)

    return {"passed": True, "rejection_reason": None}


def run(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Apply all Recovery Mode caps sequentially, then rank survivors and
    produce the final pool (max 2, max 1 per event/position).

    Parameters
    ----------
    candidates — gate-passing candidates from weather_gate + sports_gate,
                 each with the fields documented in check_single() plus
                 the ranking fields:
                   net_edge_lower_bound       float | None
                   calibration_strength       float | None   (0–1, higher = better)
                   model_uncertainty          float | None   (0–1, lower = better)
                   price_age_minutes          float | None
                   calibrated_prob_lower_bound float | None
                   settlement_clarity_grade   str | None     (A/B/C/D/F)
                   spread_cents               float | None
                   exposure_overlap           bool

    Returns
    -------
    dict with:
      survivors        list[dict]   — passed all caps (before pool cap)
      rejected         list[dict]   — each with rejection_reason
      final_pool       list[dict]   — max 2, max 1/event, ranked
      ranking_detail   list[dict]   — full sort-key trace per survivor
    """
    survivors: list[dict[str, Any]] = []
    rejected:  list[dict[str, Any]] = []
    day_pool:  list[dict[str, Any]] = []   # accepted so far this run

    for cand in candidates:
        result = check_single(cand, day_pool)
        if result["passed"]:
            survivors.append(cand)
            day_pool.append(cand)
        else:
            rejected.append({**cand, "portfolio_rejection_reason": result["rejection_reason"]})

    # ── Ranking ───────────────────────────────────────────────────────────────
    def _sort_key(c: dict[str, Any]) -> tuple:
        return (
            -(c.get("net_edge_lower_bound") or 0.0),             # 1. edge desc
            -(c.get("calibration_strength")  or 0.0),            # 2. calibration desc
             (c.get("model_uncertainty")     or 1.0),             # 3. uncertainty asc
             (c.get("price_age_minutes")     or 9999.0),          # 4. freshness asc
            -(c.get("calibrated_prob_lower_bound") or 0.0),       # 5. prob desc
            -_GRADE_RANK.get(c.get("settlement_clarity_grade") or "F", 0),  # 6. grade desc
             (c.get("spread_cents") or 9999.0),                   # 7. spread asc
             int(c.get("exposure_overlap") or False),             # 8. no overlap first
        )

    ranked = sorted(survivors, key=_sort_key)

    ranking_detail = []
    for i, c in enumerate(ranked):
        ranking_detail.append({
            "rank":                       i + 1,
            "ticker":                     c.get("ticker"),
            "category":                   c.get("category"),
            "net_edge_lower_bound":       c.get("net_edge_lower_bound"),
            "calibration_strength":       c.get("calibration_strength"),
            "model_uncertainty":          c.get("model_uncertainty"),
            "price_age_minutes":          c.get("price_age_minutes"),
            "calibrated_prob_lower_bound": c.get("calibrated_prob_lower_bound"),
            "settlement_clarity_grade":   c.get("settlement_clarity_grade"),
            "spread_cents":               c.get("spread_cents"),
            "exposure_overlap":           c.get("exposure_overlap"),
        })

    # ── Final pool: max 2, max 1 per event/position ───────────────────────────
    final_pool: list[dict[str, Any]] = []
    seen_events: set[str] = set()

    for c in ranked:
        if len(final_pool) >= _MAX_POOL_SIZE:
            break
        event_id = c.get("event_id") or ""
        position = c.get("position") or ""        # e.g. bracket label for weather
        dedup_key = f"{event_id}::{position}"

        if dedup_key and dedup_key in seen_events:
            continue
        final_pool.append(c)
        if dedup_key:
            seen_events.add(dedup_key)

    return {
        "survivors":      survivors,
        "rejected":       rejected,
        "final_pool":     final_pool,
        "ranking_detail": ranking_detail,
    }
