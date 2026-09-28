"""Fail-closed exact-line admission for V17 spread/run-line card construction.

Sporting probability and card construction are separate contracts. A spread or
run-line row may enter governed ranking/card construction only when the exact
event + exact signed line + exact side are bound to a sport-specific receipt and
the receipt is explicitly publishable/rank-eligible. Research, shadow, market,
or moneyline evidence can remain visible, but cannot substitute for that receipt.

This module does not alter sporting probability, calibration, or lower bounds.
It only decides whether an already-produced spread receipt may cross the
rank/card boundary. can_execute=false remains invariant.
"""
from __future__ import annotations

from math import isfinite
from typing import Any, Mapping

RESEARCH_ONLY_SPREAD = "RESEARCH_ONLY_SPREAD"
GOVERNED_SPREAD = "GOVERNED_SPREAD"
BLOCKED_SPREAD = "BLOCKED_SPREAD"

_SHADOW_STATES = {
    "FORWARD_SHADOW_UNCERTIFIED",
    "SHADOW",
    "RESEARCH_ONLY",
    "EXPERIMENT_CREATED",
}
_LIVE_OR_FINAL_STATES = {"STARTED", "LIVE", "IN_PROGRESS", "FINAL", "COMPLETED"}
_PREGAME_SCORE_STATUSES = {"PASS", "SHADOW_SCORED_PREGAME", "PREGAME", "SCHEDULED"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _finite_probability(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    parsed = float(value)
    return isfinite(parsed) and 0.0 <= parsed <= 1.0


def _event_id(row: Mapping[str, Any]) -> str:
    return _text(row.get("event_id") or row.get("shadow_event_id") or row.get("official_event_id"))


def _line_binding(row: Mapping[str, Any]) -> tuple[str | None, float | None]:
    side = _text(row.get("exact_line_side") or row.get("spread_side")).upper() or None
    value = row.get("exact_signed_spread")
    if value is None and row.get("home_spread") is not None:
        side = side or "HOME"
        value = row.get("home_spread")
    if value is None and row.get("home_run_line") is not None:
        side = side or "HOME"
        value = row.get("home_run_line")
    if value is None:
        return side, None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return side, None
    if not isfinite(parsed):
        return side, None
    return side, parsed


def _research_state(row: Mapping[str, Any]) -> bool:
    evaluation = _text(row.get("evaluation_state")).upper()
    status = _text(row.get("status")).upper()
    return (
        evaluation in _SHADOW_STATES
        or status in _SHADOW_STATES
        or row.get("probability_publishable") is False
        or row.get("rank_eligible") is False
    )


def spread_card_admission(
    row: Mapping[str, Any],
    *,
    requested_event_id: str,
    requested_side: str,
    requested_signed_spread: float,
) -> dict[str, Any]:
    """Return an immutable-style admission receipt for one exact spread thesis.

    The caller supplies the exact card thesis. The scorer receipt must bind to it
    exactly; adjacent lines, the opposite side, or a moneyline package are not
    interchangeable. This function never creates or adjusts a probability.
    """
    blockers: list[str] = []
    event_id = _event_id(row)
    sport = _text(row.get("sport") or row.get("league")).upper()
    side, signed_spread = _line_binding(row)
    requested_side_norm = _text(requested_side).upper()
    requested_event = _text(requested_event_id)

    try:
        requested_line = float(requested_signed_spread)
    except (TypeError, ValueError):
        requested_line = float("nan")

    if not requested_event or not event_id:
        blockers.append("SPREAD_ADMISSION:EVENT_ID_REQUIRED")
    elif event_id != requested_event:
        blockers.append("SPREAD_ADMISSION:EVENT_ID_MISMATCH")

    if not sport:
        blockers.append("SPREAD_ADMISSION:SPORT_REQUIRED")
    if requested_side_norm not in {"HOME", "AWAY"}:
        blockers.append("SPREAD_ADMISSION:REQUESTED_SIDE_INVALID")
    if side not in {"HOME", "AWAY"}:
        blockers.append("SPREAD_ADMISSION:RECEIPT_SIDE_REQUIRED")
    elif side != requested_side_norm:
        blockers.append("SPREAD_ADMISSION:SIDE_MISMATCH")

    if signed_spread is None or not isfinite(requested_line):
        blockers.append("SPREAD_ADMISSION:EXACT_SIGNED_SPREAD_REQUIRED")
    elif abs(signed_spread - requested_line) > 1e-12:
        blockers.append("SPREAD_ADMISSION:EXACT_LINE_MISMATCH")

    settlement_basis = _text(row.get("settlement_basis"))
    if not settlement_basis:
        blockers.append("SPREAD_ADMISSION:SETTLEMENT_BASIS_REQUIRED")

    specialist = _text(
        row.get("controlling_spread_specialist")
        or row.get("controlling_specialist")
    )
    if not specialist:
        blockers.append("SPREAD_ADMISSION:CONTROLLING_SPREAD_SPECIALIST_REQUIRED")

    artifact = _text(
        row.get("model_artifact_version")
        or row.get("model_family")
        or row.get("distribution_model_version")
    )
    if not artifact:
        blockers.append("SPREAD_ADMISSION:MODEL_ARTIFACT_REQUIRED")

    p_cover = row.get("p_cover")
    p_push = row.get("p_push")
    p_not_cover = row.get("p_not_cover")
    if not all(_finite_probability(value) for value in (p_cover, p_push, p_not_cover)):
        blockers.append("SPREAD_ADMISSION:PROBABILITY_DISTRIBUTION_REQUIRED")
    else:
        total = float(p_cover) + float(p_push) + float(p_not_cover)
        if abs(total - 1.0) > 1e-9:
            blockers.append("SPREAD_ADMISSION:PROBABILITY_DISTRIBUTION_NOT_NORMALIZED")

    if row.get("lower_bound_required") is True:
        lower = row.get("calibrated_lower_bound", row.get("research_lower_bound_cover"))
        if not _finite_probability(lower):
            blockers.append("SPREAD_ADMISSION:LOWER_BOUND_REQUIRED")

    if row.get("probability_publishable") is not True:
        blockers.append("SPREAD_ADMISSION:PROBABILITY_NOT_PUBLISHABLE")
    if row.get("rank_eligible") is not True:
        blockers.append("SPREAD_ADMISSION:RANK_INELIGIBLE")

    evaluation_state = _text(row.get("evaluation_state")).upper()
    if evaluation_state in _SHADOW_STATES:
        blockers.append(f"SPREAD_ADMISSION:{evaluation_state or 'SHADOW'}")

    model_timestamp = _text(row.get("model_timestamp") or row.get("final_refresh_timestamp"))
    if not model_timestamp:
        blockers.append("SPREAD_ADMISSION:MODEL_TIMESTAMP_REQUIRED")

    event_state = _text(row.get("event_state") or row.get("game_state")).upper()
    if event_state in _LIVE_OR_FINAL_STATES:
        blockers.append("SPREAD_ADMISSION:EVENT_ALREADY_STARTED_OR_FINAL")
    score_status = _text(row.get("score_snapshot_status")).upper()
    if score_status and score_status not in _PREGAME_SCORE_STATUSES:
        blockers.append("SPREAD_ADMISSION:SCORE_SNAPSHOT_NOT_PREGAME")

    if row.get("can_execute") is not False:
        blockers.append("SPREAD_ADMISSION:CAN_EXECUTE_INVARIANT_VIOLATION")

    research_only = _research_state(row)
    eligible = not blockers
    return {
        "status": "PASS" if eligible else "BLOCKED",
        "classification": (
            GOVERNED_SPREAD if eligible else RESEARCH_ONLY_SPREAD if research_only else BLOCKED_SPREAD
        ),
        "card_admission_eligible": eligible,
        "rank_eligible": eligible,
        "event_id": event_id or None,
        "sport": sport or None,
        "exact_line_side": side,
        "exact_signed_spread": signed_spread,
        "settlement_basis": settlement_basis or None,
        "controlling_spread_specialist": specialist or None,
        "model_artifact_version": artifact or None,
        "model_timestamp": model_timestamp or None,
        "p_cover": p_cover if _finite_probability(p_cover) else None,
        "p_push": p_push if _finite_probability(p_push) else None,
        "p_not_cover": p_not_cover if _finite_probability(p_not_cover) else None,
        "blockers": list(dict.fromkeys(blockers)),
        "moneyline_probability_substitution_allowed": False,
        "market_probability_substitution_allowed": False,
        "sporting_probability_mutated": False,
        "can_execute": False,
    }


__all__ = [
    "BLOCKED_SPREAD",
    "GOVERNED_SPREAD",
    "RESEARCH_ONLY_SPREAD",
    "spread_card_admission",
]
