"""
gate_engine/ml_bullpen_gate.py
WOW-PATCH-2026-07-13 — P1-5: Full-Game ML Bullpen Decomposition Gate

A starting-pitcher advantage is NOT sufficient for a full-game ML.
Full-game ML approval requires bullpen component scoring.

Required component outputs for approval:
    starter_edge, offense_edge, bullpen_quality_edge,
    bullpen_freshness_edge, high_leverage_availability,
    defense_edge

Bullpen freshness sub-fields:
    bullpen_innings_last_3_days, closer_availability,
    top_setup_relief_availability, back_to_back_usage,
    three_in_four_usage, recent_high_leverage_pitch_counts

Hard rule:
    If bullpen_freshness_edge or high_leverage_availability is UNKNOWN
    → full-game ML capped at LLP_WATCH

Reason code: LLP_WATCH_BULLPEN_UNVERIFIED
"""
from __future__ import annotations

from typing import Any

from .ml_labels import MLReasonCode


# ---------------------------------------------------------------------------
# Required component edge fields for full-game ML
# ---------------------------------------------------------------------------

REQUIRED_COMPONENT_EDGES = [
    "starter_edge",
    "offense_edge",
    "bullpen_quality_edge",
    "bullpen_freshness_edge",
    "high_leverage_availability",
    "defense_edge",
]

BULLPEN_FRESHNESS_FIELDS = [
    "bullpen_innings_last_3_days",
    "closer_availability",
    "top_setup_relief_availability",
    "back_to_back_usage",
    "three_in_four_usage",
    "recent_high_leverage_pitch_counts",
]

OPTIONAL_COMPONENT_EDGES = [
    "platoon_edge",
    "park_weather_edge",
    "travel_rest_edge",
    "series_state_edge",
]

# Special sentinel: field value equals UNKNOWN string or None
_UNKNOWN_VALUES = {None, "unknown", "UNKNOWN", ""}


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------

def validate_bullpen_gate(
    candidate: dict[str, Any],
    is_full_game: bool = True,
) -> dict[str, Any]:
    """
    Validate that bullpen components are present and non-unknown for a
    full-game ML candidate.

    Parameters
    ----------
    candidate     : the ML pick dict — may contain component edge fields directly
                    or nested under a 'components' key
    is_full_game  : if False, gate is skipped (not applicable to F5 or live-in-game)

    Returns
    -------
    {
      passed              : bool
      code                : str
      detail              : str
      reason_code         : str | None
      ceiling             : str | None   (LLP_WATCH if bullpen unknown)
      missing_components  : list[str]
      unknown_components  : list[str]
      present_components  : list[str]
      bullpen_freshness_fields : dict   (field → value | "UNKNOWN")
      composite_score     : float | None
    }
    """
    from gate_engine.llp_governance import LLPLabel

    if not is_full_game:
        return {
            "passed":                True,
            "code":                  "BULLPEN_GATE_NOT_APPLICABLE",
            "detail":                "Bullpen gate only applies to full-game ML markets",
            "reason_code":           None,
            "ceiling":               None,
            "missing_components":    [],
            "unknown_components":    [],
            "present_components":    [],
            "bullpen_freshness_fields": {},
            "composite_score":       None,
        }

    # Flatten candidate — support both flat and nested 'components' dict
    components = dict(candidate)
    if "components" in candidate and isinstance(candidate["components"], dict):
        components.update(candidate["components"])

    missing  : list[str] = []
    unknown  : list[str] = []
    present  : list[str] = []

    for field in REQUIRED_COMPONENT_EDGES:
        val = components.get(field)
        if val is None:
            missing.append(field)
        elif _is_unknown(val):
            unknown.append(field)
        else:
            present.append(field)

    # Bullpen freshness sub-fields (informational, but UNKNOWN escalates)
    freshness_state: dict[str, Any] = {}
    for field in BULLPEN_FRESHNESS_FIELDS:
        val = components.get(field)
        freshness_state[field] = "UNKNOWN" if _is_unknown(val) else val

    # Hard rule: bullpen_freshness_edge or high_leverage_availability unknown → WATCH
    critical_unknown = [
        f for f in ("bullpen_freshness_edge", "high_leverage_availability")
        if f in unknown or f in missing
    ]

    # Compute composite score from numeric component edges (informational)
    composite = _compute_composite(components)

    if missing or critical_unknown:
        blocking = sorted(set(missing + critical_unknown))
        return {
            "passed":                  False,
            "code":                    "BULLPEN_UNVERIFIED",
            "detail":                  (
                f"Full-game ML capped at LLP_WATCH: "
                f"missing={missing}, unknown_critical={critical_unknown}. "
                f"Reason: {MLReasonCode.BULLPEN_UNVERIFIED.value}"
            ),
            "reason_code":             MLReasonCode.BULLPEN_UNVERIFIED.value,
            "ceiling":                 LLPLabel.WATCH.value,
            "missing_components":      missing,
            "unknown_components":      unknown,
            "present_components":      present,
            "bullpen_freshness_fields": freshness_state,
            "composite_score":         composite,
        }

    if unknown:  # non-critical unknowns — soft warning, not a hard ceiling
        return {
            "passed":                  True,
            "code":                    "BULLPEN_PARTIAL_UNKNOWN",
            "detail":                  (
                f"Non-critical bullpen components unknown: {unknown}. "
                f"Critical components present — no hard ceiling applied."
            ),
            "reason_code":             None,
            "ceiling":                 None,
            "missing_components":      [],
            "unknown_components":      unknown,
            "present_components":      present,
            "bullpen_freshness_fields": freshness_state,
            "composite_score":         composite,
        }

    return {
        "passed":                  True,
        "code":                    "BULLPEN_VERIFIED",
        "detail":                  (
            f"All required bullpen components present. "
            f"composite_score={composite}"
        ),
        "reason_code":             None,
        "ceiling":                 None,
        "missing_components":      [],
        "unknown_components":      [],
        "present_components":      present,
        "bullpen_freshness_fields": freshness_state,
        "composite_score":         composite,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_unknown(val: Any) -> bool:
    if val is None:
        return True
    if isinstance(val, str) and val.strip().upper() in ("", "UNKNOWN", "N/A", "NONE"):
        return True
    return False


def _compute_composite(components: dict[str, Any]) -> float | None:
    """Simple mean of all numeric component edges (informational only)."""
    all_edge_fields = REQUIRED_COMPONENT_EDGES + OPTIONAL_COMPONENT_EDGES
    values: list[float] = []
    for f in all_edge_fields:
        v = components.get(f)
        if v is not None and not _is_unknown(v):
            try:
                values.append(float(v))
            except (TypeError, ValueError):
                pass
    if not values:
        return None
    return round(sum(values) / len(values), 4)
