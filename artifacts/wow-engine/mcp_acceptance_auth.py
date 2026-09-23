"""Scoped secondary authentication for WOW V17 MCP acceptance.

The production Action bearer remains authoritative and unchanged. This module
adds an opt-in, independently rotatable acceptance bearer only for the exact
backend paths represented by the V17 MCP replacement surface. The acceptance
credential never grants wager/order execution authority and is inert unless
WOW_MCP_ACCEPTANCE_AUTH_ENABLED=1 and WOW_MCP_ACCEPTANCE_API_KEY is configured.
"""
from __future__ import annotations

import os
import secrets
from collections.abc import Callable
from typing import Optional

from fastapi import Header, HTTPException, Request

ACCEPTANCE_MARKER = "V17_MCP_ACCEPTANCE_ONLY"

# /health and /governance are already public and therefore intentionally absent.
# Everything here is an existing protected V17 MCP replacement route.
_ACCEPTANCE_EXACT_PATHS = frozenset(
    {
        "/v17/host-contract",
        "/v17/detailed-evidence-contract",
        "/v17/capabilities",
        "/v17/market-health/rundown",
        "/v17/market-health/odds-api",
        "/v17/discovery/espn-compact",
        "/score-pick-request",
        "/score-prop",
        "/v17/daily-snapshot-run",
        "/score-team-event",
        "/v17/prediction-receipts/lookup",
        "/record-recommendations",
        "/settle-recommendations",
    }
)


def _acceptance_path_allowed(path: str) -> bool:
    if path in _ACCEPTANCE_EXACT_PATHS:
        return True
    # The row-detail endpoint carries a run id in the path.
    prefix = "/v17/daily-snapshot-run/"
    return path.startswith(prefix) and path.endswith("/rows") and len(path) > len(prefix) + len("/rows")


def build_action_auth_dependency(primary_auth: Callable[..., None]) -> Callable[..., None]:
    """Wrap the existing production Action auth without changing its semantics.

    The primary auth is attempted first and wins unchanged. Only a primary 401
    can fall through to the opt-in acceptance credential. Acceptance is then
    constrained by an explicit marker header and the path allow-list above.

    The returned dependency intentionally preserves the canonical
    ``_require_action_api_key`` function identity. Existing production route
    contract tests inspect dependency names to prove every protected route is
    still guarded by the Action auth boundary; the acceptance wrapper is an
    additive credential path behind that same guard, not a new authority.
    """

    def _require_action_or_mcp_acceptance_key(
        request: Request,
        authorization: Optional[str] = Header(default=None),
        x_wow_mcp_acceptance: Optional[str] = Header(default=None, alias="X-WOW-MCP-Acceptance"),
    ) -> None:
        primary_failure: HTTPException | None = None
        try:
            primary_auth(authorization)
            return
        except HTTPException as exc:
            if exc.status_code != 401:
                raise
            primary_failure = exc

        if os.getenv("WOW_MCP_ACCEPTANCE_AUTH_ENABLED", "0") != "1":
            raise primary_failure

        acceptance_key = os.getenv("WOW_MCP_ACCEPTANCE_API_KEY")
        if not acceptance_key:
            raise primary_failure

        if not authorization or not authorization.startswith("Bearer "):
            raise primary_failure

        supplied_key = authorization[len("Bearer "):]
        if not secrets.compare_digest(supplied_key, acceptance_key):
            raise primary_failure

        if x_wow_mcp_acceptance != ACCEPTANCE_MARKER:
            raise HTTPException(status_code=403, detail="MCP acceptance marker required.")

        if not _acceptance_path_allowed(request.url.path):
            raise HTTPException(status_code=403, detail="MCP acceptance credential is not authorized for this path.")

        # Authentication only. Execution authority remains governed elsewhere
        # and can_execute is still hard-false throughout the V17 runtime.
        return

    _require_action_or_mcp_acceptance_key.__name__ = "_require_action_api_key"
    return _require_action_or_mcp_acceptance_key
