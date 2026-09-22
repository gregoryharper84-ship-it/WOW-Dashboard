"""Sport-scoped V17 governance diagnostics for team/event and prop lanes.

The legacy compatibility route exposes a static global
``probability_publishable=false``. That is not a row publication decision and
must not hide healthy registered sport routes. This overlay reports route state
for every cataloged sport while leaving actual row publication exclusively to
the exact scorer + V17_TERMINAL_REDUCER.
"""
from __future__ import annotations

from typing import Any

CAN_EXECUTE = False


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

    @app.get("/governance", operation_id="getWowV17Governance")
    def governance_parity():
        base = dict(prod.governance())
        bridge_health = bridges.team_event_bridge_health()
        sport_parity = parity_health(bridge_health)
        prop_parity = prop_sport_parity_summary()

        # Capability diagnostics only. A route being ready means it may attempt
        # a governed score; it does not make any event/prop row publishable.
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

    market_api._v17_governance_parity_route_installed = True
    return True


__all__ = ["CAN_EXECUTE", "install_team_event_governance_parity_route"]
