"""Repair acquisition-detail projection mismatches for V17.

Three proven compatibility shapes can carry summary observability that differs
from the ordered concrete attempts used by durable persistence:

1. the complete nested ESPN -> (Odds, Rundown) fallback union, where aggregate
   fallback summary fields can describe the downstream union while persistence
   projects the final concrete attempt; the concrete trace may validly stop at
   Odds when that path succeeds, or continue through Rundown when needed;
2. a healthy schedule-first ESPN result with exactly one real acquisition
   attempt, where aggregate fallback status is correctly NOT_ATTEMPTED but a
   legacy endpoint scalar was stamped PATH_NOT_ATTEMPTED even though no fallback
   attempt exists; and
3. a legacy nested-fallback wrapper with one proven ESPN attempt plus an
   aggregate GOVERNED_FALLBACK_UNION summary whose downstream adapter did not
   emit ordered attempts. In that shape the one-item attempt array is partial,
   not complete. Persist the typed scalar provenance and omit the partial ordered
   list rather than pretending it is a complete execution trace.

This repair changes acquisition accounting only. It does not alter sporting
probabilities, calibration, model ownership, qualification, ranking, or
execution posture. Complete ordered attempts remain durable full provenance and
all unsupported shapes remain fail-closed in the existing persistence validator.
"""
from __future__ import annotations

import sys
from typing import Any

CAN_EXECUTE = False
CONTRACT_VERSION = "V17_ACQUISITION_DETAIL_PROJECTION_REPAIR_V5"

_GOVERNED_FALLBACK_UNION = "GOVERNED_FALLBACK_UNION"
_NESTED_FALLBACK_ATTEMPT_PATHS = (
    "ESPN_SCOREBOARD",
    "ODDS_PROXY",
    "RUNDOWN",
)
_PUBLIC_SINGLE_ATTEMPT_PATH = "ESPN_SCOREBOARD"
_FALLBACK_NOT_ATTEMPTED = "FALLBACK_NOT_ATTEMPTED"
_FALLBACK_ATTEMPTED_STATUSES = frozenset({"FALLBACK_SUCCEEDED", "FALLBACK_FAILED"})
_AGGREGATE_FALLBACK_STATES = frozenset({
    "SUCCEEDED_EMPTY",
    "SUCCEEDED_WITH_ROWS",
    "FAILED_TYPED",
    "CIRCUIT_OPEN_FROM_PRIOR_TYPED_FAILURE",
})
_PATH_NOT_ATTEMPTED = "NOT_ATTEMPTED"
_PATH_NOT_APPLICABLE = "NOT_APPLICABLE"


def _attempt_blocker(attempt: dict[str, Any]) -> Any:
    return attempt.get("originating_blocker_code") or attempt.get("blocker_code")


def _supplied_matches_attempt(detail: dict[str, Any], attempt: dict[str, Any]) -> bool:
    """Require every supplied primary scalar to agree with the one proven attempt."""
    projected = {
        "primary_path_id": attempt.get("path_id"),
        "primary_path_state": attempt.get("path_state"),
        "primary_blocker_code": _attempt_blocker(attempt),
        "primary_upstream_status": attempt.get("upstream_status"),
        "primary_content_type_class": attempt.get("content_type_class"),
        "primary_provider_alias": attempt.get("credential_alias"),
    }
    for field_name, projected_value in projected.items():
        supplied = detail.get(field_name)
        if supplied not in (None, "") and supplied != projected_value:
            return False
    return True


def normalize_nested_fallback_union_detail(detail: Any) -> Any:
    """Normalize proven complete ESPN -> Odds[/Rundown] union traces."""
    if not isinstance(detail, dict):
        return detail
    if str(detail.get("fallback_path_id") or "").strip().upper() != _GOVERNED_FALLBACK_UNION:
        return detail

    attempts = detail.get("attempts")
    if not isinstance(attempts, (list, tuple)) or len(attempts) not in {2, 3}:
        return detail
    if not all(isinstance(attempt, dict) for attempt in attempts):
        return detail

    paths = tuple(str(attempt.get("path_id") or "").strip().upper() for attempt in attempts)
    if paths != _NESTED_FALLBACK_ATTEMPT_PATHS[: len(attempts)]:
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


def normalize_partial_nested_fallback_union_detail(detail: Any) -> Any:
    """Downgrade only a proven partial ESPN+aggregate-union attempt list to scalars.

    The wrapper has one real ordered ESPN attempt and typed aggregate fallback
    scalars, but no concrete downstream ordered attempts. Keeping the one-item
    list would falsely claim completeness and contradict the aggregate fallback.
    Scalar-only persistence is the existing legacy-compatible representation for
    incomplete ordered provenance.
    """
    if not isinstance(detail, dict):
        return detail
    if str(detail.get("fallback_path_id") or "").strip().upper() != _GOVERNED_FALLBACK_UNION:
        return detail
    if str(detail.get("fallback_status") or "").strip().upper() not in _FALLBACK_ATTEMPTED_STATUSES:
        return detail
    if str(detail.get("fallback_path_state") or "").strip().upper() not in _AGGREGATE_FALLBACK_STATES:
        return detail

    attempts = detail.get("attempts")
    if not isinstance(attempts, (list, tuple)) or len(attempts) != 1:
        return detail
    first_attempt = attempts[0]
    if not isinstance(first_attempt, dict):
        return detail
    if str(first_attempt.get("path_id") or "").strip().upper() != _PUBLIC_SINGLE_ATTEMPT_PATH:
        return detail
    if not _supplied_matches_attempt(detail, first_attempt):
        return detail

    normalized = dict(detail)
    normalized["attempts"] = None
    return normalized


def normalize_public_single_attempt_detail(detail: Any) -> Any:
    """Normalize the proven schedule-first ESPN/no-fallback compatibility shape."""
    if not isinstance(detail, dict):
        return detail
    if str(detail.get("fallback_status") or "").strip().upper() != _FALLBACK_NOT_ATTEMPTED:
        return detail

    attempts = detail.get("attempts")
    if not isinstance(attempts, (list, tuple)) or len(attempts) != 1:
        return detail
    first_attempt = attempts[0]
    if not isinstance(first_attempt, dict):
        return detail
    if str(first_attempt.get("path_id") or "").strip().upper() != _PUBLIC_SINGLE_ATTEMPT_PATH:
        return detail

    supplied_state = str(detail.get("fallback_path_state") or "").strip().upper()
    if supplied_state not in ("", _PATH_NOT_ATTEMPTED, _PATH_NOT_APPLICABLE):
        return detail
    if detail.get("fallback_path_id") not in (None, ""):
        return detail

    normalized = dict(detail)
    normalized.update(
        {
            "fallback_path_id": None,
            "fallback_path_state": _PATH_NOT_APPLICABLE,
            "fallback_blocker_code": None,
            "fallback_upstream_status": None,
            "fallback_content_type_class": None,
            "fallback_provider_alias": None,
        }
    )
    return normalized


def normalize_acquisition_detail_projection(detail: Any) -> Any:
    """Apply only proven projection repairs; leave every other shape untouched."""
    normalized = normalize_nested_fallback_union_detail(detail)
    if normalized is not detail:
        return normalized
    normalized = normalize_partial_nested_fallback_union_detail(detail)
    if normalized is not detail:
        return normalized
    return normalize_public_single_attempt_detail(detail)


def install_acquisition_detail_projection_repair() -> bool:
    """Install normalization immediately before durable detail validation."""
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
            normalize_acquisition_detail_projection(detail) for detail in details
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

    runtime = sys.modules.get("v17.daily_snapshot_runtime")
    if runtime is not None:
        runtime.persist_acquisition_detail = repaired_persist_acquisition_detail

    return True


__all__ = [
    "CAN_EXECUTE",
    "CONTRACT_VERSION",
    "install_acquisition_detail_projection_repair",
    "normalize_acquisition_detail_projection",
    "normalize_nested_fallback_union_detail",
    "normalize_partial_nested_fallback_union_detail",
    "normalize_public_single_attempt_detail",
]
