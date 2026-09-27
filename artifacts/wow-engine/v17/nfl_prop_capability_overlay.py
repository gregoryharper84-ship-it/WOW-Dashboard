"""Expose exact NFL prop-lane runtime truth through the existing V17 capability Action.

The canonical GPT Action already exposes GET /v17/capabilities.  This overlay
extends that existing read-only response instead of adding another Action
operation. Declaration and aggregate PROP_PROBABILITY availability are never
accepted as proof that one NFL stat is currently scoreable.

Probability math, calibration, terminal authority, and execution are untouched.
"""
from __future__ import annotations

from typing import Any, Callable

from v17.prop_capability_manifest import DECLARED_PROP_LANES

_PATCH_FLAG = "_wow_nfl_prop_exact_capability_overlay"


def build_nfl_prop_lane_runtime(market_api: Any) -> dict[str, Any]:
    """Return all declared NFL lanes with exact runtime preflight state."""
    from v17.nfl_prop_boundary_integrity import prop_lane_capability

    lanes: list[dict[str, Any]] = []
    for (sport, stat_type), declared in sorted(DECLARED_PROP_LANES.items()):
        if sport != "NFL":
            continue
        declared_row = declared.as_dict()
        runtime = prop_lane_capability(market_api, sport, stat_type)
        lanes.append(
            {
                "sport": sport,
                "stat_type": stat_type,
                "declared_lane_status": declared_row.get("lane_status"),
                "declared_route_active": declared_row.get("route_active") is True,
                "declared_publication_allowed": declared_row.get("publication_allowed") is True,
                "declared_blocker": declared_row.get("blocker"),
                "declared_controlling_specialist": declared_row.get("controlling_specialist"),
                "runtime_status": runtime.get("status"),
                "runtime_code": runtime.get("code"),
                "runtime_scoreable": runtime.get("scoreable") is True,
                "runtime_controlling_specialist": runtime.get("controlling_specialist"),
                "runtime_specialist_registered": runtime.get("specialist_registered") is True,
                "runtime_aggregate_prop_capability": runtime.get("aggregate_prop_capability"),
                "runtime_artifact_ready": runtime.get("artifact_ready") is True,
                "runtime_artifact_code": runtime.get("artifact_code"),
                "runtime_model_artifact_version": runtime.get("model_artifact_version"),
                "automatic_row_hydration_supported": runtime.get("automatic_row_hydration_supported") is True,
                "automatic_row_hydration_provider": runtime.get("automatic_row_hydration_provider"),
                "probability_publishable": False,
                "can_execute": False,
            }
        )

    return {
        "sport": "NFL",
        "status": "EXACT_LANE_RUNTIME_PREFLIGHT",
        "declared_lane_count": len(lanes),
        "runtime_scoreable_lane_count": sum(row["runtime_scoreable"] for row in lanes),
        "declared_certified_production_lane_count": sum(
            row["declared_lane_status"] == "CERTIFIED_PRODUCTION" for row in lanes
        ),
        "declared_candidate_lane_count": sum(
            row["declared_lane_status"] == "CANDIDATE_ONLY" for row in lanes
        ),
        "declared_build_required_lane_count": sum(
            row["declared_lane_status"] == "BUILD_REQUIRED" for row in lanes
        ),
        "aggregate_prop_capability_is_not_lane_authority": True,
        "lanes": lanes,
        "probability_publishable": False,
        "can_execute": False,
    }


def install_nfl_prop_capability_overlay(market_api: Any) -> bool:
    """Patch the existing capability response function, not the Action surface."""
    from v17 import full_board_runtime as runtime

    if getattr(runtime, _PATCH_FLAG, False):
        return True

    original: Callable[[], dict[str, Any]] = runtime.capability_matrix

    def exact_lane_capability_matrix() -> dict[str, Any]:
        payload = dict(original())
        payload["player_prop_lane_runtime"] = {
            "NFL": build_nfl_prop_lane_runtime(market_api)
        }
        payload["global_terminal_authority"] = "V17_TERMINAL_REDUCER"
        payload["can_execute"] = False
        return payload

    runtime.capability_matrix = exact_lane_capability_matrix
    setattr(runtime, _PATCH_FLAG, True)
    return True


__all__ = [
    "build_nfl_prop_lane_runtime",
    "install_nfl_prop_capability_overlay",
]
