"""
gate_engine/mlb/plate_appearances_gate.py
WOW-PATCH-2026-08-06-MLB-PLATE-APPEARANCES-COVERAGE

Section 18.9 gating and routing logic for MLB Plate Appearances props.

SCOPE — THIS MODULE IS A GATING/ROUTING MODULE, NOT A PROBABILITY MODEL.
It validates data completeness, applies the Section 18.9 routing decision
tree, and assigns volatility flags.  It does NOT compute the PA opportunity
distribution (P(PA=3), P(PA=4), P(PA=5), P(PA≥6)).  The numeric distribution
must be supplied by the caller (Claude via wow-probability-estimator or
manual analysis) as pre-computed enrichment fields (expected_pa, pa_prob_more,
pa_prob_less, pa_distribution_interval).  Full distribution computation inside
the gate engine is a planned future enhancement.

Gate ID: mlb_pa_gate
Triggers: rows whose stat_key/prop_type normalizes to one of _PA_STAT_KEYS

can_execute = False  (unconditional)
"""
from __future__ import annotations

can_execute: bool = False

from typing import Any

# ── Stat keys this gate owns ──────────────────────────────────────────────────
_PA_STAT_KEYS: frozenset[str] = frozenset({
    "MLB_PLATE_APPEARANCES",
    "PA",
    "PLATE_APPEARANCES",
})

# ── Routing outcome labels (internal; mapped to WOW terminal labels below) ────
ROUTE_CORE          = "CORE_ELIGIBLE"
ROUTE_MICRO_WINDOW  = "MICRO_WINDOW"
ROUTE_HOLD          = "HOLD"
ROUTE_REJECT_DQ     = "REJECT_DATA_QUALITY"
ROUTE_CONTRACT_FAIL = "DATA_CONTRACT_FAIL"

# ── WOW terminal labels applied by this gate ──────────────────────────────────
_LABEL_REJECT_DQ    = "REJECT_DATA_QUALITY"
_LABEL_CONTRACT     = "DATA_CONTRACT_FAIL"
_LABEL_HOLD         = "MODEL_QUALIFIED_HOLD"   # ceiling for HOLD + MICRO_WINDOW

# Labels that are already more restrictive — never upgrade upward.
_TERMINAL_FLOOR: frozenset[str] = frozenset({
    "DATA_CONTRACT_FAIL",
    "REJECT_DATA_QUALITY",
})

# ── Volatility flag labels (Section 18.9) ────────────────────────────────────
VOLATILITY_GREEN  = "GREEN"
VOLATILITY_YELLOW = "YELLOW"
VOLATILITY_RED    = "RED"

# ── Required enrichment fields (Section 18.9, all must be non-None) ──────────
# A completely absent/None field triggers DATA_CONTRACT_FAIL with the specific
# missing_fields list.  A field present but with an unfavorable value (e.g.
# starting_status_confirmed=False) triggers a routing decision, not a contract
# failure.
_REQUIRED_FIELDS: tuple[str, ...] = (
    # Structural context fields — always known by the caller.
    # Absent or None → DATA_CONTRACT_FAIL (caller error, not data-quality gap).
    "lineup_slot",
    "starting_status_confirmed",
    "home_away",
    "team_implied_run_total",
    "opposing_starter_run_prevention",
    "opposing_starter_bb_rate",
    "opposing_bullpen_quality",
    "recent_full_game_start_rate",
    "platoon_substitution_risk",
    "pinch_hit_risk",
    "defensive_replacement_risk",
)
# Note: l5_pa_exact_line / l10_* / expected_pa are NOT in _REQUIRED_FIELDS.
# Their absence means "data not available" (→ REJECT_DATA_QUALITY via routing
# steps 3d / 3e), not a structural contract violation.

# ── Risk flag values treated as "HIGH" ───────────────────────────────────────
_HIGH_RISK: frozenset[str] = frozenset({"HIGH", "ELEVATED", "YES", "TRUE", "1"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get(row: dict, enrichment: dict, key: str) -> Any:
    """Read from row first, enrichment second.  Returns None if absent in both."""
    v = row.get(key)
    if v is not None:
        return v
    return enrichment.get(key)


def _is_confirmed(value: Any) -> bool:
    """Return True when a confirmation field signals 'yes'."""
    if value is True or value == 1:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "confirmed", "active"}
    return False


def _is_high_risk(value: Any) -> bool:
    return str(value or "").strip().upper() in _HIGH_RISK


def _slot_band(slot: Any) -> str:
    """Map a lineup slot to its Section 18.9 modeling band."""
    try:
        s = int(slot)
        if 1 <= s <= 3:
            return "1-3"
        if 4 <= s <= 6:
            return "4-6"
        if 7 <= s <= 9:
            return "7-9"
    except (TypeError, ValueError):
        pass
    return "UNKNOWN"


def _assign_volatility(
    slot: Any,
    start_rate: Any,
    platoon_risk: Any,
    pinch_hit_risk: Any,
) -> str:
    """
    Assign GREEN / YELLOW / RED per Section 18.9 volatility flag criteria.

    GREEN: locked-in slots 1-6, start_rate ≥ 0.80, no elevated substitution risk.
    RED:   start_rate < 0.50 OR elevated platoon/substitution risk.
    YELLOW: all other cases (slots 7-9, moderate risk, recent order changes, etc.)
    """
    try:
        s = int(slot)
    except (TypeError, ValueError):
        return VOLATILITY_RED

    try:
        sr = float(start_rate or 0)
    except (TypeError, ValueError):
        sr = 0.0

    if sr < 0.50 or _is_high_risk(platoon_risk):
        return VOLATILITY_RED

    if 1 <= s <= 6 and sr >= 0.80 and not _is_high_risk(pinch_hit_risk):
        return VOLATILITY_GREEN

    return VOLATILITY_YELLOW


def _apply_ceiling(row: dict, label: str, reason: str) -> None:
    """
    Apply a terminal label ceiling — only if it is more restrictive than the
    current label.  Stamps pa_route_ceiling on the row for audit trail.
    """
    current = row.get("terminal_label") or ""
    if current not in _TERMINAL_FLOOR:
        row["terminal_label"] = label
    row["pa_route_ceiling"] = {
        "ceiling_label": label,
        "ceiling_reason": reason,
        "prior_label": current or None,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run(row: dict[str, Any]) -> None:
    """
    Execute the Section 18.9 MLB Plate Appearances gate for one row.
    Modifies row in-place.  No-ops immediately for non-PA rows.
    """
    # ── Gate applicability check ─────────────────────────────────────────
    raw_stat = (row.get("stat_key") or row.get("prop_type") or "")
    stat_key = raw_stat.upper().replace(" ", "_").replace("-", "_").strip()
    if stat_key not in _PA_STAT_KEYS:
        return

    enrichment: dict = row.get("enrichment") or {}
    gates     = row.setdefault("gates",    {})
    blockers  = row.setdefault("blockers", [])

    # ── Step 1: Data contract — required fields completeness ─────────────
    missing_fields = [
        f for f in _REQUIRED_FIELDS
        if _get(row, enrichment, f) is None
    ]
    if missing_fields:
        gates["mlb_pa_gate"] = {
            "passed":         False,
            "gate_id":        "mlb_pa_gate",
            "result":         ROUTE_CONTRACT_FAIL,
            "missing_fields": missing_fields,
            "spec_section":   "18.9",
            "note": (
                "All Section 18.9 required fields must be supplied as enrichment. "
                "Fabricating or defaulting any field is not permitted."
            ),
        }
        blockers.append(
            "MLB_PA_GATE:DATA_CONTRACT_FAIL:missing=" + ",".join(missing_fields)
        )
        if row.get("terminal_label") not in _TERMINAL_FLOOR:
            row["terminal_label"] = _LABEL_CONTRACT
        return

    # ── Step 2: Resolve field values ─────────────────────────────────────
    lineup_slot    = _get(row, enrichment, "lineup_slot")
    start_conf     = _get(row, enrichment, "starting_status_confirmed")
    home_away      = _get(row, enrichment, "home_away")
    exact_line     = row.get("line")
    l5_exact       = _get(row, enrichment, "l5_pa_exact_line")
    l10_exact      = _get(row, enrichment, "l10_pa_exact_line")
    l10_median     = _get(row, enrichment, "l10_pa_median")
    l10_average    = _get(row, enrichment, "l10_pa_average")
    start_rate     = _get(row, enrichment, "recent_full_game_start_rate")
    platoon_risk   = _get(row, enrichment, "platoon_substitution_risk")
    pinch_risk     = _get(row, enrichment, "pinch_hit_risk")
    def_risk       = _get(row, enrichment, "defensive_replacement_risk")
    expected_pa    = _get(row, enrichment, "expected_pa")

    slot_band      = _slot_band(lineup_slot)

    # ── Step 3: Routing decision tree (Section 18.9 order-of-operations) ─

    # 3a. Starting lineup unconfirmed
    if not _is_confirmed(start_conf):
        gates["mlb_pa_gate"] = {
            "passed":       False,
            "gate_id":      "mlb_pa_gate",
            "result":       ROUTE_REJECT_DQ,
            "route_reason": "STARTING_LINEUP_UNCONFIRMED",
            "lineup_slot":  lineup_slot,
            "spec_section": "18.9",
        }
        blockers.append("MLB_PA_GATE:REJECT_DATA_QUALITY:STARTING_LINEUP_UNCONFIRMED")
        if row.get("terminal_label") not in _TERMINAL_FLOOR:
            row["terminal_label"] = _LABEL_REJECT_DQ
        return

    # 3b. Batting slot unresolved
    if slot_band == "UNKNOWN":
        gates["mlb_pa_gate"] = {
            "passed":       False,
            "gate_id":      "mlb_pa_gate",
            "result":       ROUTE_HOLD,
            "route_reason": "BATTING_SLOT_UNRESOLVED",
            "lineup_slot":  lineup_slot,
            "spec_section": "18.9",
        }
        blockers.append("MLB_PA_GATE:HOLD:BATTING_SLOT_UNRESOLVED")
        _apply_ceiling(row, _LABEL_HOLD, "BATTING_SLOT_UNRESOLVED")
        return

    # 3c. Exact PA line unavailable
    if exact_line is None:
        gates["mlb_pa_gate"] = {
            "passed":       False,
            "gate_id":      "mlb_pa_gate",
            "result":       ROUTE_REJECT_DQ,
            "route_reason": "EXACT_PA_LINE_UNAVAILABLE",
            "lineup_slot":  lineup_slot,
            "spec_section": "18.9",
        }
        blockers.append("MLB_PA_GATE:REJECT_DATA_QUALITY:EXACT_PA_LINE_UNAVAILABLE")
        if row.get("terminal_label") not in _TERMINAL_FLOOR:
            row["terminal_label"] = _LABEL_REJECT_DQ
        return

    # 3d. L5/L10 ledger unavailable
    if not any(v is not None for v in [l5_exact, l10_exact, l10_median, l10_average]):
        gates["mlb_pa_gate"] = {
            "passed":       False,
            "gate_id":      "mlb_pa_gate",
            "result":       ROUTE_REJECT_DQ,
            "route_reason": "L5_L10_LEDGER_UNAVAILABLE",
            "lineup_slot":  lineup_slot,
            "spec_section": "18.9",
        }
        blockers.append("MLB_PA_GATE:REJECT_DATA_QUALITY:L5_L10_LEDGER_UNAVAILABLE")
        if row.get("terminal_label") not in _TERMINAL_FLOOR:
            row["terminal_label"] = _LABEL_REJECT_DQ
        return

    # 3e. No PA distribution built (expected_pa not supplied by caller)
    if expected_pa is None:
        gates["mlb_pa_gate"] = {
            "passed":       False,
            "gate_id":      "mlb_pa_gate",
            "result":       ROUTE_REJECT_DQ,
            "route_reason": "PA_DISTRIBUTION_NOT_BUILT",
            "lineup_slot":  lineup_slot,
            "spec_section": "18.9",
            "note": (
                "expected_pa must be supplied as pre-computed enrichment. "
                "Full P(PA=3/4/5/≥6) distribution computation is a planned "
                "future enhancement to this module."
            ),
        }
        blockers.append("MLB_PA_GATE:REJECT_DATA_QUALITY:PA_DISTRIBUTION_NOT_BUILT")
        if row.get("terminal_label") not in _TERMINAL_FLOOR:
            row["terminal_label"] = _LABEL_REJECT_DQ
        return

    # ── Step 4: Volatility flag ───────────────────────────────────────────
    volatility = _assign_volatility(lineup_slot, start_rate, platoon_risk, pinch_risk)

    # 3f. Platoon or defensive substitution risk materially elevated
    if _is_high_risk(platoon_risk) or _is_high_risk(def_risk):
        gates["mlb_pa_gate"] = {
            "passed":          False,
            "gate_id":         "mlb_pa_gate",
            "result":          ROUTE_MICRO_WINDOW,
            "route_reason":    "SUBSTITUTION_RISK_ELEVATED",
            "lineup_slot":     lineup_slot,
            "slot_band":       slot_band,
            "volatility_flag": volatility,
            "platoon_risk":    platoon_risk,
            "defensive_risk":  def_risk,
            "spec_section":    "18.9",
        }
        blockers.append("MLB_PA_GATE:MICRO_WINDOW:SUBSTITUTION_RISK_ELEVATED")
        _apply_ceiling(row, _LABEL_HOLD, "MICRO_WINDOW:SUBSTITUTION_RISK_ELEVATED")
        return

    # 3g. Slots 7-9 with unstable start history
    if slot_band == "7-9":
        try:
            sr = float(start_rate or 0)
        except (TypeError, ValueError):
            sr = 0.0
        if sr < 0.70:
            gates["mlb_pa_gate"] = {
                "passed":                False,
                "gate_id":               "mlb_pa_gate",
                "result":                ROUTE_MICRO_WINDOW,
                "route_reason":          "SLOT_7_9_UNSTABLE_START_HISTORY",
                "lineup_slot":           lineup_slot,
                "slot_band":             slot_band,
                "volatility_flag":       volatility,
                "recent_start_rate":     start_rate,
                "spec_section":          "18.9",
                "note": (
                    "MICRO_WINDOW ceiling applied; may be exceeded if model "
                    "demonstrates sufficient opportunity probability (Section 18.9)."
                ),
            }
            blockers.append("MLB_PA_GATE:MICRO_WINDOW:SLOT_7_9_UNSTABLE_START_HISTORY")
            _apply_ceiling(row, _LABEL_HOLD, "MICRO_WINDOW:SLOT_7_9_UNSTABLE_START_HISTORY")
            return

    # 3h. CORE eligible — all checks passed
    gates["mlb_pa_gate"] = {
        "passed":            True,
        "gate_id":           "mlb_pa_gate",
        "result":            ROUTE_CORE,
        "route_reason":      "CONFIRMED_STABLE_SLOT_COMPLETE_DISTRIBUTION",
        "lineup_slot":       lineup_slot,
        "slot_band":         slot_band,
        "volatility_flag":   volatility,
        "exact_line":        exact_line,
        "home_away":         home_away,
        "l5_pa_exact_line":  l5_exact,
        "l10_pa_exact_line": l10_exact,
        "l10_pa_average":    l10_average,
        "expected_pa":       expected_pa,
        "spec_section":      "18.9",
    }
    # CORE eligible — no ceiling applied; downstream gates run normally.
    # The four-lane stamping and classifier determine the final label.
