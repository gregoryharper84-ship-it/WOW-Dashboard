"""Cross-sport recoverable-hold semantics for V17 team/event scoring.

A missing pregame input is not the same thing as a dead candidate.  This overlay
adds explicit retry/hold metadata to typed MODEL_INPUTS_INSUFFICIENT failures
without inventing a probability or weakening terminal governance.

It deliberately does *not* claim that a durable server-side watcher exists yet;
that remains a separate infrastructure capability.  ``candidate_removed`` is
false for recoverable pregame input gaps so the same immutable event identity can
be refreshed later.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

CAN_EXECUTE = False
MODEL_INPUTS_INSUFFICIENT = "MODEL_INPUTS_INSUFFICIENT"

_STATUS_FIELDS = {
    "home_lineup_status",
    "away_lineup_status",
    "home_starter_status",
    "away_starter_status",
    "quarterback_status",
    "injury_report",
    "expected_starters_rotation",
    "rest_back_to_back",
    "goalie_status",
    "rest_travel",
    "starting_xi_status",
    "participant_status",
    "weigh_in_status",
    "field_status",
    "injuries_suspensions",
    "injury_news_status",
}


def _typed_hold(detail: dict[str, Any]) -> dict[str, Any]:
    code = str(detail.get("code") or detail.get("status") or "")
    if code != MODEL_INPUTS_INSUFFICIENT:
        return detail

    missing = [str(value) for value in (detail.get("missing_fields") or [])]
    blockers = [str(value) for value in (detail.get("blockers") or [])]
    blocker_code = str(detail.get("blocker_code") or "")
    blocker_tokens = {blocker_code, *blockers}

    if "MLB_TEAM_EVENT_LINEUP_NOT_YET_AVAILABLE" in blocker_tokens:
        state = "HELD_PENDING_FINAL_LINEUP"
        retry_trigger = "OFFICIAL_LINEUP_AVAILABLE"
    elif "calibration_artifact" in missing or any(
        "CALIBRATION_ARTIFACT" in token for token in blocker_tokens
    ):
        state = "HELD_PENDING_CALIBRATION_ARTIFACT"
        retry_trigger = "CERTIFIED_CALIBRATION_ARTIFACT_AVAILABLE"
    elif set(missing) & _STATUS_FIELDS:
        state = "HELD_PENDING_SPORT_EVIDENCE"
        retry_trigger = "REQUIRED_SPORT_STATUS_EVIDENCE_AVAILABLE"
    else:
        state = "HELD_PENDING_MODEL_INPUTS"
        retry_trigger = "REQUIRED_MODEL_INPUTS_AVAILABLE"

    out = dict(detail)
    out.update(
        {
            "candidate_state": state,
            "recoverable_hold": True,
            "candidate_removed": False,
            "retry_required": True,
            "retry_trigger": retry_trigger,
            "durable_retry_watcher_bound": False,
            "probability_package_present": bool(
                out.get("model_probability") is not None
                or out.get("calibrated_probability") is not None
            ),
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        }
    )
    return out


def install_team_event_recoverable_hold_overlay() -> bool:
    import v17.team_event_bridge_runtime as bridges
    import v17.team_event_request_runtime as base_runtime

    if getattr(bridges, "_v17_team_event_recoverable_hold_installed", False):
        return True

    original_score = bridges.score_registered_team_event_request

    def score_with_recoverable_holds(
        req: Any,
        *,
        event_api: Any,
        canonical_hydration_required: bool = False,
    ) -> dict[str, Any]:
        try:
            return original_score(
                req,
                event_api=event_api,
                canonical_hydration_required=canonical_hydration_required,
            )
        except HTTPException as exc:
            if not isinstance(exc.detail, dict):
                raise
            detail = _typed_hold(dict(exc.detail))
            if detail == exc.detail:
                raise
            raise HTTPException(
                status_code=exc.status_code,
                detail=detail,
                headers=exc.headers,
            ) from exc

    bridges.score_registered_team_event_request = score_with_recoverable_holds
    base_runtime.score_team_event_request = score_with_recoverable_holds
    bridges._v17_team_event_recoverable_hold_original_score = original_score
    bridges._v17_team_event_recoverable_hold_installed = True
    return True


__all__ = [
    "CAN_EXECUTE",
    "install_team_event_recoverable_hold_overlay",
]
