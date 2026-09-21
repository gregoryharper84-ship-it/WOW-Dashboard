"""Install non-model V17 lifecycle hooks for durable pick-request state.

The hooks wrap the already-installed facade functions in
``pick_request_runtime_core``. They observe validated model inputs, terminal
outcomes, and completed model packages, then synchronously persist state through
``v17.pick_request_state_runtime`` when an outer durable run context is active.

They never alter probabilities, calibration, terminal reduction, or execution.
"""
from __future__ import annotations

from typing import Any

import pick_request_runtime_core as pick_runtime
from v17.pick_request_state_runtime import (
    record_inputs_ready,
    record_model_outcome,
    record_terminal_outcome,
)

_INSTALLED = False


def install_pick_request_state_hooks() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_validate = pick_runtime._validate_evidence
    original_terminal = pick_runtime._terminal
    original_completed = pick_runtime._completed_scored_outcome

    def validate_with_state(row: Any, canonical_stat: str) -> dict[str, Any]:
        normalized = original_validate(row, canonical_stat)
        record_inputs_ready(row)
        return normalized

    def terminal_with_state(*args: Any, **kwargs: Any) -> dict[str, Any]:
        outcome = original_terminal(*args, **kwargs)
        record_terminal_outcome(outcome)
        return outcome

    def completed_with_state(*args: Any, **kwargs: Any) -> dict[str, Any]:
        outcome = original_completed(*args, **kwargs)
        record_model_outcome(outcome)
        return outcome

    validate_with_state._v17_pick_state_hook = True  # type: ignore[attr-defined]
    terminal_with_state._v17_pick_state_hook = True  # type: ignore[attr-defined]
    completed_with_state._v17_pick_state_hook = True  # type: ignore[attr-defined]

    pick_runtime._validate_evidence = validate_with_state
    pick_runtime._terminal = terminal_with_state
    pick_runtime._completed_scored_outcome = completed_with_state
    _INSTALLED = True


__all__ = ["install_pick_request_state_hooks"]
