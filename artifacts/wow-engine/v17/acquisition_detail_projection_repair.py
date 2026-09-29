"""Repair nested fallback-union acquisition detail normalization for V17.

The cross-sport resilience layer may represent an ESPN primary followed by the
ODDS/Rundown downstream union as ``GOVERNED_FALLBACK_UNION``. Durable acquisition
persistence validates endpoint observability from the ordered concrete attempts:
primary fields project from the first attempt and fallback fields project from
the last attempt. The union summary retains aggregate fallback status while its
observability fields may otherwise describe the first downstream fallback,
creating a false ``CROSS_SPORT_ACQUISITION_DETAIL_PROJECTION_CONTRADICTION``.

This repair changes acquisition accounting only. It does not alter sporting
probabilities, calibration, model ownership, qualification, or execution
posture. Ordered attempts remain the durable full provenance, aggregate status
remains unchanged, and malformed/unsupported union shapes remain fail-closed in
the existing persistence validator.
"""
from __future__ import annotations

import sys
from typing import Any

CAN_EXECUTE = False
CONTRACT_VERSION = "V17_ACQUISITION_DETAIL_PROJECTION_REPAIR_V2"

_GOVERNED_FALLBACK_UNION = "GOVERNED_FALLBACK_UNION"
_NESTED_FALLBACK_ATTEMPT_PATHS = (
    "ESPN_SCOREBOARD",
    "ODDS_PROXY",
    "RUNDOWN",
)


def _attempt_blocker(attempt: dict[str, Any]) -> Any:
    return attempt.get("originating_blocker_code") or attempt.get("blocker_code")


def normalize_nested_fallback_union_detail(detail: Any) -> Any:
    """Normalize only the proven ESPN -> (Odds, Rundown) nested union shape.

    The aggregate fields ``fallback_status`` and ``exhaustion_status`` are not
    changed. Only endpoint observability fields governed by the durable
    projection contract are normalized: primary from the first concrete attempt
    and fallback from the last concrete attempt. This preserves every ordered
    attempt while allowing the strict persistence validator to prove the summary
    against the same evidence.
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

    first_attempt = attempts[0]
    last_attempt = attempts[-1]
    normalized = dict(detail)
    normalized.update(
        {
            "primary_path_id": first_attempt.get("path_id"),
            "primary_path_state": first_attempt.get("path_state"),
            "primary_blocker_code": _attempt_blocker(first_attempt),
            "primary_upstream_status": first_attempt.get("upstream_status"),
            "primary_content_type_class": first_attempt.get("content_type_class"),
            "primary_provider_alias": first_attempt.get("credential_alias"),
            "fallback_path_id": last_attempt.get("path_id"),
            "fallback_path_state": last_attempt.get("path_state"),
            "fallback_blocker_code": _attempt_blocker(last_attempt),
            "fallback_upstream_status": last_attempt.get("upstream_status"),
            "fallback_content_type_class": last_attempt.get("content_type_class"),
            "fallback_provider_alias": last_attempt.get("credential_alias"),
        }
    )
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
