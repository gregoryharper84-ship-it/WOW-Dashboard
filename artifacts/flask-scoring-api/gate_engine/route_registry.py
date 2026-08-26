"""
gate_engine/route_registry.py
WOW-PATCH-2026-08-02-MANDATORY-ROUTE-COMPLETION

Mandatory route completion enforcement — runs per row after classifier.classify().

Rule: any row that landed on a qualifying label (FINAL_APPROVED, MONEY_QUALIFIED,
MARKET_VERIFIED_HOLD) must have all required gates present in row['gates'].
A missing required gate means the engine cannot have independently verified that
constraint — the row's ceiling is lowered to MODEL_QUALIFIED_HOLD with a
REQUIRED_GATE_NOT_EXECUTED:<gate_id> blocker.

This is a one-way ceiling enforcement: it can only lower labels, never raise them.

can_execute = False  (unconditional)
"""
from __future__ import annotations

from typing import Any

can_execute: bool = False

# ---------------------------------------------------------------------------
# Labels that trigger enforcement.
# Rows that landed below MARKET_VERIFIED_HOLD already failed a gate explicitly;
# no additional enforcement is needed there.
# ---------------------------------------------------------------------------
QUALIFYING_LABELS: frozenset[str] = frozenset({
    "FINAL_APPROVED",
    "MONEY_QUALIFIED",
    "MARKET_VERIFIED_HOLD",
})

# Downgrade target when a required gate is missing
DOWNGRADE_LABEL: str = "MODEL_QUALIFIED_HOLD"

# ---------------------------------------------------------------------------
# Universal gates — required for every row reaching a qualifying label.
# Mirrors classifier.REQUIRED_FOR_FINAL but enforced here for the full
# qualifying set (not just FINAL_APPROVED).
# ---------------------------------------------------------------------------
UNIVERSAL_REQUIRED_GATES: frozenset[str] = frozenset({
    "slate_validation",
    "status_role",
    "l5_l10_ledger",
    "market_gate",
    "ev_gate",
    "slip_structure",
    "exposure_gate",
})

# ---------------------------------------------------------------------------
# Sport-specific additional required gates.
# Acquisition tracking is mandatory for all sports with live data feeds.
# ---------------------------------------------------------------------------
SPORT_REQUIRED_GATES: dict[str, frozenset[str]] = {
    "MLB":  frozenset({"acquisition"}),
    "NBA":  frozenset({"acquisition"}),
    "WNBA": frozenset({"acquisition"}),
    "NFL":  frozenset({"acquisition"}),
    "NHL":  frozenset({"acquisition"}),
}

# ---------------------------------------------------------------------------
# Prop-type-specific additional required gates.
# Keys are normalized prop_type strings (upper, underscored).
# ---------------------------------------------------------------------------
PROP_TYPE_REQUIRED_GATES: dict[str, frozenset[str]] = {
    "1IP_PITCHES_THROWN":             frozenset({"calibration_health"}),
    "FIRST_INNING_PITCHES_THROWN":    frozenset({"calibration_health"}),
    "FIRST_INNING_PITCHES":           frozenset({"calibration_health"}),
    "1IP_PITCHER_STRIKEOUTS":         frozenset({"calibration_health"}),
    # WOW-PATCH-2026-08-06-MLB-PLATE-APPEARANCES-COVERAGE
    # Section 18.9 — mlb_pa_gate must run for all PA prop variants.
    "MLB_PLATE_APPEARANCES":          frozenset({"mlb_pa_gate"}),
    "PA":                             frozenset({"mlb_pa_gate"}),
    "PLATE_APPEARANCES":              frozenset({"mlb_pa_gate"}),
}

# ---------------------------------------------------------------------------
# Market-family route overrides
# WOW-PATCH-2026-08-07-OUTRIGHT-MONEYLINE-ROUTING
#
# OUTRIGHT_WINNER rows are intercepted BEFORE _ge_run_pipeline and scored by
# the LLP Moneyline Probability Expert.  They never enter the player-prop gate
# pipeline, so the standard UNIVERSAL_REQUIRED_GATES (l5_l10_ledger,
# market_gate, status_role, ev_gate …) do NOT apply.
#
# The controlling gate for OUTRIGHT_WINNER is "moneyline_expert" — a virtual
# gate that gate_engine/moneyline_probability.py marks as run.
# ---------------------------------------------------------------------------
MARKET_FAMILY_REQUIRED_GATES: dict[str, frozenset[str]] = {
    "OUTRIGHT_WINNER": frozenset({"moneyline_expert"}),
    "PLAYER_PROP":     UNIVERSAL_REQUIRED_GATES,
    "COMBO_PROP":      UNIVERSAL_REQUIRED_GATES,
}

# Route fields that must be present in every scored row's output block
ROUTE_COMPATIBILITY_FIELDS: tuple[str, ...] = (
    "route_id",
    "market_family",
    "objective",
    "controlling_skill_id",
    "input_contract_version",
    "required_field_profile",
    "compatibility",
)


def _normalize_prop_type(prop_type: str | None) -> str:
    return (prop_type or "").upper().replace(" ", "_").replace("-", "_").strip()


def get_required_gates(row: dict[str, Any]) -> frozenset[str]:
    """
    Return the full set of gate keys that must be present in row['gates']
    for this row's sport and prop_type combination.
    """
    sport = (row.get("sport") or "").upper().strip()
    prop  = _normalize_prop_type(row.get("prop_type"))

    required = set(UNIVERSAL_REQUIRED_GATES)
    required.update(SPORT_REQUIRED_GATES.get(sport, frozenset()))
    required.update(PROP_TYPE_REQUIRED_GATES.get(prop, frozenset()))
    return frozenset(required)


def check_route_completion(row: dict[str, Any]) -> list[str]:
    """
    Return a list of required gate keys that are absent from row['gates'].
    Empty list means the route is complete.
    Only meaningful for rows in QUALIFYING_LABELS.
    """
    gates_ran: set[str] = set(row.get("gates") or {})
    required  = get_required_gates(row)
    return sorted(required - gates_ran)


def enforce_route_completion(row: dict[str, Any]) -> bool:
    """
    If the row holds a qualifying label but one or more required gates are
    missing, downgrade terminal_label to MODEL_QUALIFIED_HOLD and append
    REQUIRED_GATE_NOT_EXECUTED:<gate_id> blockers.

    Returns True when a downgrade was applied.
    One-way: only lowers labels, never raises them.
    """
    label = row.get("terminal_label") or ""
    if label not in QUALIFYING_LABELS:
        return False

    missing = check_route_completion(row)
    if not missing:
        return False

    row["terminal_label"] = DOWNGRADE_LABEL
    for gate_id in missing:
        row.setdefault("blockers", []).append(
            f"REQUIRED_GATE_NOT_EXECUTED:{gate_id}"
        )

    # Stamp a route_completion_fail key so _build_output can surface it
    row.setdefault("gates", {})["route_completion"] = {
        "passed": False,
        "missing_gates": missing,
        "original_label": label,
        "enforced_ceiling": DOWNGRADE_LABEL,
    }
    return True


# ---------------------------------------------------------------------------
# Execution trace helpers
# ---------------------------------------------------------------------------

def build_row_execution_trace(row: dict[str, Any]) -> dict[str, Any]:
    """
    Build a compact execution trace for one row, suitable for GPT inspection.
    Called from _build_output after all gates and enforcement have run.
    """
    gates: dict[str, Any] = row.get("gates") or {}
    gates_ran  = sorted(k for k in gates if k != "route_completion")
    gates_passed = sorted(
        k for k, v in gates.items()
        if k != "route_completion" and isinstance(v, dict) and v.get("passed") is True
    )
    gates_failed = sorted(
        k for k, v in gates.items()
        if k != "route_completion" and isinstance(v, dict) and v.get("passed") is False
    )

    required = get_required_gates(row)
    missing  = sorted(required - set(gates_ran))

    rc = gates.get("route_completion") or {}

    return {
        "row_id":           row.get("row_id"),
        "player":           row.get("player"),
        "prop_type":        row.get("prop_type"),
        "sport":            row.get("sport"),
        "terminal_label":   row.get("terminal_label"),
        "gates_ran":        gates_ran,
        "gates_passed":     gates_passed,
        "gates_failed":     gates_failed,
        "required_gates":   sorted(required),
        "required_missing": missing,
        "route_complete":   len(missing) == 0,
        "route_downgraded": bool(rc.get("missing_gates")),
        "original_label_before_route_enforcement": rc.get("original_label"),
    }
