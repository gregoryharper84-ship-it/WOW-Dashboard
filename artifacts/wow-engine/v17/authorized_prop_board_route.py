"""Authenticated, opt-in read-only V17 board discovery route.

This is deliberately NOT /score-pick-request and cannot publish probabilities.
The route is disabled unless an operator explicitly enables it on the backend.
The independent provider key is read server-side and never returned in receipts.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Query

from v17.authorized_prop_board_discovery import (
    PropFeedHold, SCHEMA, discover_authorized_prop_board,
)

CAN_EXECUTE = False


def install_authorized_prop_board_route(app: FastAPI, *, auth_dependency: Any) -> None:
    path = "/v17/prop-board/authorized-discovery"
    if any(getattr(route, "path", None) == path for route in app.router.routes):
        return

    @app.get(
        path, dependencies=[auth_dependency],
        operation_id="getWowV17AuthorizedPropDiscovery",
    )
    def authorized_prop_discovery(
        sport: str = Query(min_length=2, max_length=12),
        max_pages: int = Query(default=4, ge=1, le=8),
    ) -> dict[str, Any]:
        try:
            return discover_authorized_prop_board(sport=sport, max_pages=max_pages)
        except PropFeedHold as exc:
            return {
                "schema": SCHEMA,
                "sport": sport.upper(),
                "status": "BLOCKED",
                "typed_blocker": exc.code,
                "source_coverage_complete_for_requested_sport": False,
                "directional_candidates": [], "blocked_rows": [],
                "governed_predictions": 0,
                "probability_publishable": False, "can_execute": False,
            }


__all__ = ["install_authorized_prop_board_route"]
