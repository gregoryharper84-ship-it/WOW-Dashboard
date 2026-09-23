"""Sport-scoped V17 governance diagnostics for team/event and prop lanes.

The legacy compatibility route exposes a static global
``probability_publishable=false``. That is not a row publication decision and
must not hide healthy registered sport routes. This overlay reports route state
for every cataloged sport while leaving actual row publication exclusively to
the exact scorer + V17_TERMINAL_REDUCER.

The public ``/governance`` route is intentionally compact because it is part of
the Custom GPT / MCP preflight path. Full diagnostics remain available from the
separate detail route so transport limits cannot be misclassified as model or
backend failures.
"""
from __future__ import annotations

from typing import Any

CAN_EXECUTE = False
COMPACT_PROFILE = "V17_GOVERNANCE_ACTION_SAFE_V1"
DETAIL_PATH = "/v17/governance-detail"


def _status_from(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    for key in (
        "status",
        "capability_status",
        "calibration_health_status",
        "forward_shadow_status",
        "governed_probability_status",
    ):
        if value.get(key) is not None:
            return value.get(key)
    return None


def _compact_deployment_gates(value: Any) -> dict[str, Any]:
    if not isinstance(value, list):
        return {
            "status": value,
            "count": 0,
            "failed_gate_ids": [],
        }
    failed: list[str] = []
    for row in value:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "").upper()
        if status in {"PASS", "READY", "ACTIVE", "AVAILABLE", "CERTIFIED"}:
            continue
        gate_id = row.get("gate_id") or row.get("id") or row.get("name")
        if gate_id is not None:
            failed.append(str(gate_id))
    return {
        "status": "PASS" if not failed else "BLOCKED",
        "count": len(value),
        "failed_gate_ids": failed[:20],
    }


def _compact_lane_capabilities(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for lane, raw in value.items():
        if not isinstance(raw, dict):
            out[str(lane)] = {"status": raw, "can_execute": False}
            continue
        evidence = raw.get("evidence")
        reason = evidence.get("reason") if isinstance(evidence, dict) else None
        declared = raw.get("declared_lanes")
        if isinstance(declared, dict):
            declared_count = len(declared)
        elif isinstance(declared, list):
            declared_count = len(declared)
        else:
            declared_count = 0
        row = {
            "status": raw.get("status") or raw.get("capability_status") or "UNAVAILABLE",
            "probability_publishable": raw.get("probability_publishable"),
            "can_execute": False,
        }
        if reason is not None:
            row["reason"] = reason
        if declared_count:
            row["declared_lane_count"] = declared_count
        out[str(lane)] = row
    return out


def _compact_calibration_health(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"status": value}
    allowed = (
        "status",
        "calibration_health_status",
        "forward_shadow_status",
        "publication_status",
        "reason",
        "updated_at",
    )
    compact = {key: value.get(key) for key in allowed if value.get(key) is not None}
    if "status" not in compact:
        inferred = _status_from(value)
        if inferred is not None:
            compact["status"] = inferred
    return compact


def _sport_names(value: Any) -> list[str]:
    if isinstance(value, dict):
        return sorted(str(key) for key in value.keys())
    if isinstance(value, list):
        return sorted(str(item) for item in value)
    return []


def _compact_governance_payload(
    *,
    base: dict[str, Any],
    bridge_health: dict[str, Any],
    sport_parity: dict[str, Any],
    prop_parity: dict[str, Any],
) -> dict[str, Any]:
    """Project full V17 governance into a stable Action-safe response."""
    ready_sports = [
        sport
        for sport, state in sport_parity.items()
        if isinstance(state, dict) and state.get("model_capability_ready") is True
    ]
    registered_sports = [
        sport
        for sport, state in sport_parity.items()
        if isinstance(state, dict) and state.get("bridge_registered") is True
    ]
    prop_sports = _sport_names(prop_parity.get("sports"))

    payload = {
        "governed_probability_capability": base.get("governed_probability_capability", "UNAVAILABLE"),
        "governed_probability_status": base.get("governed_probability_status", "NOT_PRODUCED"),
        "probability_publishable": None,
        "probability_publishable_scope": "ROW_SCOPED_ONLY",
        "global_publication_hold_removed_from_diagnostics": True,
        "can_execute": False,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "compute_provider": base.get("compute_provider"),
        "database_provider": base.get("database_provider"),
        "deployment_gates": _compact_deployment_gates(base.get("deployment_gates")),
        "calibration_health": _compact_calibration_health(base.get("calibration_health")),
        "lane_capabilities": _compact_lane_capabilities(base.get("lane_capabilities")),
        "routing_contract": base.get("routing_contract") or {},
        "arithmetic_audit": {
            key: value
            for key, value in (base.get("arithmetic_audit") or {}).items()
            if key in {
                "provider",
                "status",
                "external_transport_required",
                "blocks_model_probability",
                "can_execute",
            }
        },
        "team_event_capability_summary": {
            "cataloged_sports": len(sport_parity),
            "registered_sports": len(registered_sports),
            "model_capability_ready_sports": len(ready_sports),
            "registered_sport_names": sorted(registered_sports),
            "model_capability_ready_sport_names": sorted(ready_sports),
            "row_publication_requires_terminal_reducer": True,
        },
        "prop_capability_summary": {
            "cataloged_sports": prop_parity.get("cataloged_sports", len(prop_sports)),
            "sport_names": prop_sports,
            "row_publication_requires_terminal_reducer": True,
        },
        "cross_lane_sport_parity": {
            "team_event_cataloged_sports": len(sport_parity),
            "prop_cataloged_sports": prop_parity.get("cataloged_sports", len(prop_sports)),
            "same_canonical_sport_universe": set(sport_parity) == set(prop_sports),
            "capability_equality_not_fabricated": True,
            "row_publication_requires_terminal_reducer": True,
            "can_execute": False,
        },
        "transport_contract": {
            "profile": COMPACT_PROFILE,
            "full_diagnostics_path": DETAIL_PATH,
            "full_sport_payloads_omitted": True,
            "can_execute": False,
        },
    }
    return payload


def install_team_event_governance_parity_route(*, market_api: Any) -> bool:
    if getattr(market_api, "_v17_governance_parity_route_installed", False):
        return True

    app = getattr(market_api, "app", None)
    prod = getattr(market_api, "prod", None)
    if app is None or prod is None or not callable(getattr(prod, "governance", None)):
        return False

    import v17.team_event_bridge_runtime as bridges
    from v17.team_event_sport_parity import parity_health
    from v17.prop_sport_parity import prop_sport_parity_summary

    existing = [
        route
        for route in list(app.router.routes)
        if getattr(route, "path", None) == "/governance"
        and "GET" in (getattr(route, "methods", set()) or set())
    ]
    for route in existing:
        app.router.routes.remove(route)

    def _full_payload() -> dict[str, Any]:
        base = dict(prod.governance())
        bridge_health = bridges.team_event_bridge_health()
        sport_parity = parity_health(bridge_health)
        prop_parity = prop_sport_parity_summary()

        ready_sports = [
            sport
            for sport, state in sport_parity.items()
            if state.get("model_capability_ready") is True
        ]
        registered_sports = [
            sport
            for sport, state in sport_parity.items()
            if state.get("bridge_registered") is True
        ]

        base["probability_publishable"] = None
        base["probability_publishable_scope"] = "ROW_SCOPED_ONLY"
        base["global_publication_hold_removed_from_diagnostics"] = True
        base["team_event_sports"] = bridge_health
        base["team_event_sport_parity"] = sport_parity
        base["team_event_capability_summary"] = {
            "cataloged_sports": len(sport_parity),
            "registered_sports": len(registered_sports),
            "model_capability_ready_sports": len(ready_sports),
            "registered_sport_names": sorted(registered_sports),
            "model_capability_ready_sport_names": sorted(ready_sports),
            "row_publication_requires_terminal_reducer": True,
        }
        base["prop_sport_parity"] = prop_parity
        base["cross_lane_sport_parity"] = {
            "team_event_cataloged_sports": len(sport_parity),
            "prop_cataloged_sports": prop_parity["cataloged_sports"],
            "same_canonical_sport_universe": set(sport_parity) == set(prop_parity["sports"]),
            "capability_equality_not_fabricated": True,
            "row_publication_requires_terminal_reducer": True,
            "can_execute": False,
        }
        base["global_terminal_authority"] = "V17_TERMINAL_REDUCER"
        base["can_execute"] = False
        return base

    @app.get("/governance", operation_id="getWowV17Governance")
    def governance_parity():
        base = dict(prod.governance())
        bridge_health = bridges.team_event_bridge_health()
        sport_parity = parity_health(bridge_health)
        prop_parity = prop_sport_parity_summary()
        return _compact_governance_payload(
            base=base,
            bridge_health=bridge_health,
            sport_parity=sport_parity,
            prop_parity=prop_parity,
        )

    @app.get(DETAIL_PATH, operation_id="getWowV17GovernanceDetail")
    def governance_detail():
        return _full_payload()

    market_api._v17_governance_parity_route_installed = True
    return True


__all__ = [
    "CAN_EXECUTE",
    "COMPACT_PROFILE",
    "DETAIL_PATH",
    "_compact_governance_payload",
    "install_team_event_governance_parity_route",
]
