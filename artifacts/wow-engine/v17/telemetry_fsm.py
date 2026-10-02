from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class TransitionRule:
    rule_id: str
    from_state: str
    to_state: str
    predicate: Callable[[Mapping[str, Any]], bool]


class TelemetryStateMachine:
    """Deterministic event-driven FSM for sport telemetry.

    The state machine encodes observed operational state transitions only. It does
    not create sporting probabilities, calibration, or qualification thresholds.
    """

    def __init__(self, initial_state: str, rules: Sequence[TransitionRule]):
        self.initial_state = initial_state
        self.rules = tuple(rules)

    def replay(self, events: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
        state = self.initial_state
        transitions = []
        unmatched = []

        for index, event in enumerate(events):
            candidates = [
                rule
                for rule in self.rules
                if rule.from_state == state and bool(rule.predicate(event))
            ]
            if len(candidates) > 1:
                return {
                    "valid": False,
                    "terminal_state": state,
                    "typed_failure": "FSM_AMBIGUOUS_TRANSITION",
                    "event_index": index,
                    "matching_rule_ids": [rule.rule_id for rule in candidates],
                    "transitions": transitions,
                    "unmatched_events": unmatched,
                }
            if not candidates:
                unmatched.append({"event_index": index, "state": state, "event": dict(event)})
                continue

            rule = candidates[0]
            previous = state
            state = rule.to_state
            transitions.append(
                {
                    "event_index": index,
                    "rule_id": rule.rule_id,
                    "from_state": previous,
                    "to_state": state,
                }
            )

        return {
            "valid": True,
            "terminal_state": state,
            "typed_failure": None,
            "transitions": transitions,
            "unmatched_events": unmatched,
        }
