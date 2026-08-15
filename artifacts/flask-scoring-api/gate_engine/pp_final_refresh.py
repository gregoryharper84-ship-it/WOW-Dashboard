"""
pp_final_refresh.py — Binding Final Refresh Enforcement
WOW-PATCH-2026-08-15-PP-PROMOTION-AND-SAME-GAME-FRAGILITY

Material changes detected between analysis time and card-publication time
MUST force a rerun — not merely emit a warning.

Material change categories:
    LINEUP      — player status, injury flag, lineup confirmation change
    PARTICIPANT — player/team/opponent identity change
    MARKET      — prop type, stat key, or line change
    PRICE       — odds movement beyond materiality threshold
    SETTLEMENT  — game/series settlement state change
    WEATHER     — weather condition or forecast change (for relevant props)
    SOURCE      — primary data source version / staleness change

Any detected material change sets FINAL_REFRESH_REQUIRED on the row.
The row may not advance to FINAL_APPROVED or MONEY_QUALIFIED without passing
a final refresh (all material-change flags absent or cleared by a fresh run).

Blocking behaviour:
    A row with FINAL_REFRESH_REQUIRED that has not been refreshed is capped
    at MARKET_VERIFIED_HOLD.  The pipeline must re-score the row with fresh
    data before it can qualify for paid-card promotion.

Module invariants:
    can_execute              = False   (unconditional)
    PRODUCTION_AUTHORITY     = False
    EXECUTION_RULE           = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Module-level authority constants — unconditional
# ---------------------------------------------------------------------------
can_execute          = False
PRODUCTION_AUTHORITY = False
EXECUTION_RULE       = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

# Labels
_REFRESH_REQUIRED_LABEL = "FINAL_REFRESH_REQUIRED"
_CAP_LABEL              = "MARKET_VERIFIED_HOLD"

# Price materiality threshold: absolute change in American odds that triggers
# a material PRICE change.  Default: 10 lines (e.g. -110 → -120).
PRICE_MATERIALITY_THRESHOLD = 10.0

# Source staleness materiality threshold in minutes
SOURCE_STALENESS_MINUTES = 45

# Paid-card eligible labels that get capped on refresh required
_PAID_CARD_LABELS = frozenset({"MONEY_QUALIFIED", "FINAL_APPROVED"})

# Material change category codes
CATEGORY_LINEUP      = "LINEUP"
CATEGORY_PARTICIPANT = "PARTICIPANT"
CATEGORY_MARKET      = "MARKET"
CATEGORY_PRICE       = "PRICE"
CATEGORY_SETTLEMENT  = "SETTLEMENT"
CATEGORY_WEATHER     = "WEATHER"
CATEGORY_SOURCE      = "SOURCE"


# ---------------------------------------------------------------------------
# Individual change detectors
# ---------------------------------------------------------------------------

def _detect_lineup_change(row: dict, baseline: dict) -> dict | None:
    """
    Detect material lineup / participant status change.
    Returns a change descriptor or None.
    """
    fields = [
        "lineup_status", "status", "injury_flag",
        "is_confirmed", "dnp_flag",
    ]
    changes = {}
    for f in fields:
        cur = row.get(f)
        base = baseline.get(f)
        if cur != base and not (cur is None and base is None):
            changes[f] = {"baseline": base, "current": cur}
    if changes:
        return {"category": CATEGORY_LINEUP, "fields": changes}
    return None


def _detect_participant_change(row: dict, baseline: dict) -> dict | None:
    """Detect player/team/opponent identity change."""
    fields = ["player", "team", "opponent", "game", "game_id", "game_time"]
    changes = {}
    for f in fields:
        cur = row.get(f)
        base = baseline.get(f)
        if cur != base and not (cur is None and base is None):
            changes[f] = {"baseline": base, "current": cur}
    if changes:
        return {"category": CATEGORY_PARTICIPANT, "fields": changes}
    return None


def _detect_market_change(row: dict, baseline: dict) -> dict | None:
    """Detect prop type, stat key, or line change."""
    fields = [
        "prop_type", "market", "stat_key",
        "line", "side", "direction",
    ]
    changes = {}
    for f in fields:
        cur = row.get(f)
        base = baseline.get(f)
        if cur != base and not (cur is None and base is None):
            # For numeric line: only flag if delta > 0.5
            if f == "line":
                try:
                    delta = abs(float(cur) - float(base))
                    if delta > 0.5:
                        changes[f] = {"baseline": base, "current": cur, "delta": delta}
                except (TypeError, ValueError):
                    changes[f] = {"baseline": base, "current": cur}
            else:
                changes[f] = {"baseline": base, "current": cur}
    if changes:
        return {"category": CATEGORY_MARKET, "fields": changes}
    return None


def _detect_price_change(row: dict, baseline: dict) -> dict | None:
    """
    Detect odds movement beyond PRICE_MATERIALITY_THRESHOLD.
    Compares odds_more / price or odds_less between current and baseline.
    """
    price_fields = ["odds_more", "odds_less", "price", "price_more", "price_less"]
    changes = {}
    for f in price_fields:
        cur_raw  = row.get(f)
        base_raw = baseline.get(f)
        if cur_raw is None and base_raw is None:
            continue
        try:
            cur_v  = float(cur_raw)  if cur_raw  is not None else None
            base_v = float(base_raw) if base_raw is not None else None
            if cur_v is None or base_v is None:
                changes[f] = {"baseline": base_raw, "current": cur_raw, "reason": "one_side_missing"}
            elif abs(cur_v - base_v) >= PRICE_MATERIALITY_THRESHOLD:
                changes[f] = {
                    "baseline": base_v, "current": cur_v,
                    "delta": round(abs(cur_v - base_v), 2),
                }
        except (TypeError, ValueError):
            pass
    if changes:
        return {"category": CATEGORY_PRICE, "fields": changes}
    return None


def _detect_settlement_change(row: dict, baseline: dict) -> dict | None:
    """Detect game or series settlement state change."""
    fields = ["game_settled", "series_settled", "settlement_state", "game_status"]
    changes = {}
    for f in fields:
        cur  = row.get(f)
        base = baseline.get(f)
        if cur != base and not (cur is None and base is None):
            changes[f] = {"baseline": base, "current": cur}
    if changes:
        return {"category": CATEGORY_SETTLEMENT, "fields": changes}
    return None


def _detect_weather_change(row: dict, baseline: dict) -> dict | None:
    """Detect weather condition or forecast change (outdoor/weather-sensitive props)."""
    fields = [
        "weather_condition", "weather_forecast",
        "precipitation_probability", "wind_speed",
        "temperature", "weather_risk_flag",
    ]
    changes = {}
    for f in fields:
        cur  = row.get(f)
        base = baseline.get(f)
        if cur is None and base is None:
            continue
        if cur != base:
            changes[f] = {"baseline": base, "current": cur}
    if changes:
        return {"category": CATEGORY_WEATHER, "fields": changes}
    return None


def _detect_source_change(row: dict, baseline: dict) -> dict | None:
    """
    Detect primary data source version or staleness change.
    Compares source version fingerprints stored in row["sources"].
    """
    cur_sources  = row.get("sources")  or {}
    base_sources = baseline.get("sources") or {}

    if not isinstance(cur_sources, dict) or not isinstance(base_sources, dict):
        return None

    changes = {}
    for src in set(list(cur_sources.keys()) + list(base_sources.keys())):
        cur_v  = cur_sources.get(src)
        base_v = base_sources.get(src)
        if cur_v != base_v and not (cur_v is None and base_v is None):
            changes[src] = {"baseline": str(base_v)[:80], "current": str(cur_v)[:80]}

    if changes:
        return {"category": CATEGORY_SOURCE, "fields": changes}
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_material_changes(
    row: dict[str, Any],
    baseline: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Compare the current row state against a baseline snapshot.
    Returns a list of material change descriptors (empty = no changes).
    """
    changes: list[dict] = []
    for detector in (
        _detect_lineup_change,
        _detect_participant_change,
        _detect_market_change,
        _detect_price_change,
        _detect_settlement_change,
        _detect_weather_change,
        _detect_source_change,
    ):
        result = detector(row, baseline)
        if result is not None:
            changes.append(result)
    return changes


def enforce_final_refresh(
    row: dict[str, Any],
    baseline: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Run the binding final-refresh check for a single row.

    If baseline is None, the check passes vacuously (no prior state to compare).
    If material changes are detected, the row is blocked from final/money
    qualification until re-scored with fresh data.

    Mutates row["gates"]["pp_final_refresh"] and conditionally caps
    row["terminal_label"].

    Returns the gate result dict.
    """
    if baseline is None:
        result: dict[str, Any] = {
            "can_execute":       False,
            "execution_rule":    EXECUTION_RULE,
            "refresh_required":  False,
            "changes_detected":  [],
            "code":              "FINAL_REFRESH_VACUOUS",
            "detail":            "no baseline provided — check passes vacuously",
        }
        row.setdefault("gates", {})["pp_final_refresh"] = result
        return result

    changes = detect_material_changes(row, baseline)
    refresh_required = len(changes) > 0
    categories       = [c["category"] for c in changes]

    result = {
        "can_execute":      False,
        "execution_rule":   EXECUTION_RULE,
        "refresh_required": refresh_required,
        "changes_detected": changes,
        "change_categories": categories,
        "code":             "FINAL_REFRESH_REQUIRED" if refresh_required else "FINAL_REFRESH_CLEAR",
        "detail":           (
            f"material changes in: {', '.join(categories)}"
            if refresh_required
            else "no material changes detected"
        ),
    }

    row.setdefault("gates", {})["pp_final_refresh"] = result

    if refresh_required:
        blocker = f"FINAL_REFRESH_REQUIRED:categories={','.join(categories)}"
        if blocker not in (row.get("blockers") or []):
            row.setdefault("blockers", []).append(blocker)

        # Cap terminal_label for paid-card eligible rows
        if row.get("terminal_label") in _PAID_CARD_LABELS:
            row["terminal_label"] = _CAP_LABEL
            result["terminal_label_capped"] = True
        else:
            result["terminal_label_capped"] = False

    return result


def run(
    rows: list[dict[str, Any]],
    baselines: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Run binding final-refresh enforcement across a list of rows.

    baselines: mapping of row_id → baseline snapshot dict.
                Rows without a matching baseline pass vacuously.

    Returns a batch report.
    """
    baselines = baselines or {}
    refresh_required_count = 0
    row_summaries          = []

    for row in rows:
        row_id   = row.get("row_id") or ""
        baseline = baselines.get(row_id)
        gate_res = enforce_final_refresh(row, baseline)

        if gate_res["refresh_required"]:
            refresh_required_count += 1

        row_summaries.append({
            "row_id":            row_id,
            "refresh_required":  gate_res["refresh_required"],
            "change_categories": gate_res.get("change_categories", []),
            "terminal_label":    row.get("terminal_label"),
        })

    return {
        "can_execute":             False,
        "execution_rule":          EXECUTION_RULE,
        "rows_checked":            len(rows),
        "refresh_required_count":  refresh_required_count,
        "row_summaries":           row_summaries,
    }
