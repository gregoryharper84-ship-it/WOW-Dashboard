"""Universal V17 governance coordinator for registered team/event bridges.

This wrapper makes governance coverage universal without pretending sporting-model
coverage is universal. It executes a sport profile preflight before the exact
registered scorer, preserves typed specialist failures, annotates the returned
package with the shared governance contract, and keeps V17_TERMINAL_REDUCER as
sole terminal authority. It never registers a missing model and never promotes
rank eligibility.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from v17.team_event_governance_profiles import (
    TERMINAL_AUTHORITY,
    governance_health,
    governance_profile_preflight,
)
from v17.team_event_model_development_manifest import TEAM_EVENT_MODEL_DEVELOPMENT

CAN_EXECUTE = False
_INSTALLED = False
_ORIGINAL_SCORE = None
_ORIGINAL_HEALTH = None


def _annotate(result: dict[str, Any], preflight: dict[str, Any]) -> dict[str, Any]:
    out = dict(result)
    out["universal_governance_profile"] = preflight.get("profile")
    out["universal_governance_preflight"] = {
        "status": preflight.get("status"),
        "code": preflight.get("code"),
        "sport": preflight.get("sport"),
    }
    out["global_terminal_reducer"] = TERMINAL_AUTHORITY
    out["can_execute"] = False
    return out


def _augment_exception(exc: HTTPException, preflight: dict[str, Any]) -> HTTPException:
    detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
    augmented = dict(detail)
    augmented["universal_governance_profile"] = preflight.get("profile")
    augmented["universal_governance_preflight"] = {
        "status": preflight.get("status"),
        "code": preflight.get("code"),
        "sport": preflight.get("sport"),
    }
    augmented["global_terminal_reducer"] = TERMINAL_AUTHORITY
    augmented["can_execute"] = False
    return HTTPException(status_code=exc.status_code, detail=augmented, headers=exc.headers)


def install_universal_team_event_governance() -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_SCORE, _ORIGINAL_HEALTH
    if _INSTALLED:
        return {"status": "ALREADY_INSTALLED", "governance_profiles": governance_health(), "can_execute": False}

    import v17.team_event_bridge_runtime as bridges
    import v17.team_event_request_runtime as base_runtime

    _ORIGINAL_SCORE = bridges.score_registered_team_event_request
    _ORIGINAL_HEALTH = bridges.team_event_bridge_health

    def universal_score(req: Any, *, event_api: Any, canonical_hydration_required: bool = False) -> dict[str, Any]:
        preflight = governance_profile_preflight(req)
        if preflight.get("status") != "PASS":
            detail = {
                "code": preflight.get("code") or "TEAM_EVENT_GOVERNANCE_PREFLIGHT_FAILED",
                "sport": preflight.get("sport"),
                "blockers": list(preflight.get("missing_identity_fields") or []),
                "failed_contract_scope": ["UNIVERSAL_GOVERNANCE_PREFLIGHT"],
                "market_probability_substitution_allowed": False,
                "generic_reasoning_substitution_allowed": False,
                "probability_publishable": False,
                "rank_eligible": False,
                "universal_governance_profile": preflight.get("profile"),
                "global_terminal_reducer": TERMINAL_AUTHORITY,
                "can_execute": False,
            }
            raise HTTPException(status_code=422, detail=detail)
        try:
            result = _ORIGINAL_SCORE(
                req,
                event_api=event_api,
                canonical_hydration_required=canonical_hydration_required,
            )
        except HTTPException as exc:
            raise _augment_exception(exc, preflight) from exc
        return _annotate(result, preflight)

    def universal_health() -> dict[str, dict[str, Any]]:
        bridge_health = _ORIGINAL_HEALTH()
        profiles = governance_health()
        output: dict[str, dict[str, Any]] = {}
        for sport, profile in profiles.items():
            base = dict(bridge_health.get(sport) or {})
            development = TEAM_EVENT_MODEL_DEVELOPMENT[sport].as_dict()
            output[sport] = {
                **base,
                "governance_profile_installed": True,
                "governance_profile": profile,
                "model_development": development,
                "global_terminal_authority": TERMINAL_AUTHORITY,
                "can_execute": False,
            }
        for sport, base in bridge_health.items():
            if sport not in output:
                output[sport] = {
                    **dict(base),
                    "governance_profile_installed": False,
                    "global_terminal_authority": TERMINAL_AUTHORITY,
                    "can_execute": False,
                }
        return output

    bridges.score_registered_team_event_request = universal_score
    bridges.team_event_bridge_health = universal_health
    base_runtime.score_team_event_request = universal_score
    bridges._install_health_overlay()
    _INSTALLED = True
    return {
        "status": "INSTALLED",
        "governance_profiles": governance_health(),
        "model_development": {sport: lane.as_dict() for sport, lane in TEAM_EVENT_MODEL_DEVELOPMENT.items()},
        "global_terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": False,
    }


__all__ = ["CAN_EXECUTE", "install_universal_team_event_governance"]
