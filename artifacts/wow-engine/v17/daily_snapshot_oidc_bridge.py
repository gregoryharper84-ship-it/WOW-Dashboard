"""Strict internal GitHub OIDC bridge for full-slate V17 Daily runs.

The public Custom GPT Daily route retains its existing bounded request contract.
This module adds a separate internal automation route for approved GitHub Actions
workflows so scheduled production verification can cover a full daily slate
without copying WOW_ACTION_API_KEY or provider credentials into GitHub.

This route is orchestration only. It does not add a model, modify a probability,
relax publication gates, or authorize wager execution.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from github_actions_oidc import (
    GitHubOIDCValidationError,
    authorize_action_key_or_multiscout_oidc,
)
from v17 import daily_snapshot_runtime as daily_runtime


INTERNAL_DAILY_SNAPSHOT_ROUTE = "/internal/v17/daily-snapshot"
INTERNAL_MAX_TEAM_EVENTS = 32


class InternalDailySnapshotRequest(BaseModel):
    """Server-automation request contract, intentionally separate from Action API."""

    model_config = ConfigDict(extra="forbid")

    requested_slate_date: str
    requested_timezone: str = Field(min_length=1, max_length=64)
    lanes: list[Literal["PROPS", "MONEYLINE"]] = Field(
        default_factory=lambda: ["MONEYLINE"]
    )
    max_props: int = Field(default=0, ge=0, le=12)
    max_team_events: int = Field(default=24, ge=0, le=INTERNAL_MAX_TEAM_EVENTS)
    response_mode: Literal["COMPACT", "FULL"] = "COMPACT"


def _authorize(authorization: str | None) -> str:
    try:
        return authorize_action_key_or_multiscout_oidc(authorization)
    except GitHubOIDCValidationError as exc:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "DAILY_SNAPSHOT_AUTOMATION_AUTH_INVALID",
                "can_execute": False,
            },
        ) from exc


def install_daily_snapshot_oidc_bridge(*, app: FastAPI, market_api: Any) -> bool:
    """Install one internal, OIDC-capable full-slate Daily endpoint.

    Authentication remains exact-repository/protected-main/workflow allowlisted
    through github_actions_oidc. The backend owns database/provider credentials.
    """

    if getattr(app.state, "v17_daily_snapshot_oidc_bridge_installed", False):
        return True

    prod = getattr(market_api, "prod", None)
    if prod is None or not callable(getattr(prod, "get_client", None)):
        return False
    event_api = getattr(prod, "event_api", None)
    if event_api is None:
        return False

    @app.post(
        INTERNAL_DAILY_SNAPSHOT_ROUTE,
        operation_id="runWowV17InternalDailySnapshot",
    )
    def run_internal_daily_snapshot(
        req: InternalDailySnapshotRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        auth_mode = _authorize(authorization)
        result = daily_runtime.run_daily_snapshot(
            req,
            db=prod.get_client(),
            market_api=market_api,
            event_api=event_api,
        )
        if not isinstance(result, dict):
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "DAILY_SNAPSHOT_AUTOMATION_OUTPUT_INVALID",
                    "can_execute": False,
                },
            )

        return {
            **result,
            "automation_auth": auth_mode,
            "global_terminal_authority": "V17_TERMINAL_REDUCER",
            "can_execute": False,
        }

    app.state.v17_daily_snapshot_oidc_bridge_installed = True
    return True


__all__ = [
    "INTERNAL_DAILY_SNAPSHOT_ROUTE",
    "INTERNAL_MAX_TEAM_EVENTS",
    "InternalDailySnapshotRequest",
    "install_daily_snapshot_oidc_bridge",
]
