"""Event-level mutual exclusion and close-game governor for LLP moneylines."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

can_execute = False
MINIMUM_EVENT_DECISION_LOWER_BOUND_GAP = 0.04

@dataclass(frozen=True)
class EventDecision:
    event_decision: str
    selected_participant: str | None
    selected_participant_count: int
    blocker: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    can_execute: bool = False
    def to_dict(self) -> dict[str, Any]:
        return {
            "event_decision": self.event_decision,
            "selected_participant": self.selected_participant,
            "selected_participant_count": self.selected_participant_count,
            "blocker": self.blocker,
            "diagnostics": dict(self.diagnostics),
            "can_execute": False,
        }

def _f(candidate: dict[str, Any], key: str) -> float | None:
    try:
        return float(candidate.get(key))
    except (TypeError, ValueError):
        return None

def decide_event(
    favorite: dict[str, Any] | None,
    underdog: dict[str, Any] | None,
    *,
    minimum_lower_bound_gap: float = MINIMUM_EVENT_DECISION_LOWER_BOUND_GAP,
) -> EventDecision:
    candidates = [c for c in (favorite, underdog) if c]
    if not candidates:
        return EventDecision("NO_PICK_UNCALIBRATED", None, 0, "NO_ELIGIBLE_SIDES")

    event_keys = {str(c.get("event_key") or "") for c in candidates}
    if "" in event_keys or len(event_keys) != 1:
        return EventDecision("NO_PICK_DATA_CONFLICT", None, 0, "EVENT_IDENTITY_CONFLICT")

    for c in candidates:
        if c.get("probability_audit_status") not in {
            "PASS_PROBABILITY_AUDIT", "PASS_WITH_CONFIDENCE_CEILING"
        }:
            return EventDecision("NO_PICK_UNCALIBRATED", None, 0, "PROBABILITY_AUDIT_NOT_PASSED")
        if not c.get("model_valid_after_latest_material_update", True):
            return EventDecision("NO_PICK_STATUS_UNRESOLVED", None, 0, "STALE_MODEL_INVALIDATED")
        if c.get("hard_blocker"):
            return EventDecision("NO_PICK_DATA_CONFLICT", None, 0, str(c["hard_blocker"]))

    if len(candidates) < 2:
        return EventDecision("NO_PICK_UNCALIBRATED", None, 0, "OPPOSING_OUTCOME_NOT_MODELED")

    fav, dog = favorite, underdog
    fav_lb, dog_lb = _f(fav, "calibrated_probability_lower_bound"), _f(dog, "calibrated_probability_lower_bound")
    fav_p, dog_p = _f(fav, "calibrated_probability"), _f(dog, "calibrated_probability")
    if None in (fav_lb, dog_lb, fav_p, dog_p):
        return EventDecision("NO_PICK_UNCALIBRATED", None, 0, "CALIBRATED_PROBABILITY_OR_BOUND_MISSING")

    gap = abs(fav_lb - dog_lb)
    diagnostics = {
        "favorite_point_probability": fav_p,
        "underdog_point_probability": dog_p,
        "favorite_lower_bound": fav_lb,
        "underdog_lower_bound": dog_lb,
        "lower_bound_gap": gap,
        "minimum_lower_bound_gap": minimum_lower_bound_gap,
    }
    if gap < minimum_lower_bound_gap:
        return EventDecision("NO_PICK_CLOSE_GAME", None, 0, "LOWER_BOUND_GAP_BELOW_MINIMUM", diagnostics)

    selected, decision = (fav, "PICK_FAVORITE") if fav_lb > dog_lb else (dog, "PICK_UNDERDOG")
    participant = selected.get("participant") or selected.get("team")
    if not participant:
        return EventDecision("NO_PICK_DATA_CONFLICT", None, 0, "SELECTED_PARTICIPANT_MISSING", diagnostics)
    return EventDecision(decision, str(participant), 1, diagnostics=diagnostics)
