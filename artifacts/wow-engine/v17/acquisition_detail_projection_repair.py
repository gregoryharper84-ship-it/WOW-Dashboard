"""Repair nested fallback-union acquisition detail normalization for V17.

The cross-sport resilience layer may represent an ESPN primary followed by the
ODDS/Rundown downstream union as ``GOVERNED_FALLBACK_UNION``. Durable acquisition
persistence validates the same evidence from the ordered concrete attempts and
stores the terminal fallback path. Without this adapter, the aggregate union
label is compared directly with the final concrete attempt (RUNDOWN) and a
false ``CROSS_SPORT_ACQUISITION_DETAIL_PROJECTION_CONTRADICTION`` is raised.

This repair changes acquisition accounting only. It does not alter sporting
probabilities, calibration, model ownership, qualification, or execution
posture. Malformed or unsupported union summaries remain fail-closed in the
existing persistence validator.
"""
from __future__ import annotations

import sys
from typing import Any

CAN_EXECUTE = False
CONTRACT_VERSION = "V17_ACQUISITION_DETAIL_PROJECTION_REPAIR_V1"

_GOVERNED_FALLBACK_UNION = "GOVERNED_FALLBACK_UNION"
_NESTED_FALLBACK_ATTEMPT_PATHS = (
    "ESPN_SCOREBOARD",
    "ODDS_PROXY",
    "RUNDOWN",
)


def normalize_nested_fallback_union_detail(detail: Any) -> Any:
    """Normalize only the proven ESPN -> (Odds, Rundown) nested union shape.

    Ordered attempts retain the complete provenance. The legacy summary slot is
    normalized to the terminal concrete fallback path so the existing strict
    projection validator can verify state/blocker/diagnostic consistency.
    Any other shape is returned unchanged and therefore continues to fail closed
    if it contradicts the ordered evidence.
    """
    if not isinstance(detail, dict):
        return detail
    if str(detail.get("fallback_path_id") or "").strip().upper() != _GOVERNED_FALLBACK_UNION:
        return detail

    attempts = detail.get("attempts")
    if not isinstance(attempts, (list, tuple)) or len(attempts) != 3:
        return detail
    if not all(isinstance(attempt, dict) for attempt in attempts):
        return detail

    paths = tuple(str(attempt.get("path_id") or "").strip().upper() for attempt in attempts)
    if paths != _NESTED_FALLBACK_ATTEMPT_PATHS:
        return detail

    normalized = dict(detail)
    normalized["fallback_path_id"] = paths[-1]
    return normalized


def install_acquisition_detail_projection_repair() -> bool:
    """Install the normalization immediately before durable detail validation."""
    from v17 import daily_response_contract as contract

    original = contract.persist_acquisition_detail
    if getattr(original, "_v17_acquisition_detail_projection_repair", False):
        return True

    def repaired_persist_acquisition_detail(
        db: Any,
        *,
        run_id: str,
        details: list[dict[str, Any]],
        expected_targets: list[dict[str, Any]],
    ) -> dict[str, Any]:
        normalized_details = [
            normalize_nested_fallback_union_detail(detail) for detail in details
        ]
        return original(
            db,
            run_id=run_id,
            details=normalized_details,
            expected_targets=expected_targets,
        )

    repaired_persist_acquisition_detail._v17_acquisition_detail_projection_repair = True
    repaired_persist_acquisition_detail._v17_acquisition_detail_projection_original = original
    contract.persist_acquisition_detail = repaired_persist_acquisition_detail

    # daily_snapshot_runtime imports the persistence callable directly. If that
    # module already exists, replace the bound reference too; if it imports
    # later, it receives the repaired contract function automatically.
    runtime = sys.modules.get("v17.daily_snapshot_runtime")
    if runtime is not None:
        runtime.persist_acquisition_detail = repaired_persist_acquisition_detail

    return True


__all__ = [
    "CAN_EXECUTE",
    "CONTRACT_VERSION",
    "install_acquisition_detail_projection_repair",
    "normalize_nested_fallback_union_detail",
]
