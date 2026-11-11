"""Bounded NFL full-week snapshot orchestration contract; nonpublishing.

The trusted GitHub Actions caller owns OIDC acquisition, authenticated HTTPS,
fresh schedule provenance, review, and production deployment. This helper does
not access credentials, select a game week, grant release authority, or publish.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping

from v17.cross_sport_acceptance_factory import SourceInventory
from v17.nfl_multiday_acceptance_preflight import (
    TERMINAL,
    hold,
    plan_nfl_week_dates,
    reconcile_nfl_week_snapshots,
)

AUDIENCE = "wow-v17-multiscout"
MAX_DATE_CALLS = 9


@dataclass(frozen=True)
class AuthenticatedSnapshotResponse:
    """Attestation prepared by a separately authorized authenticated client.

    Construct this only after the HTTPS response has been bound to the
    requested endpoint and authenticated GitHub OIDC context. Values in
    payload/body alone NEVER establish transport authentication.
    """

    http_status: int
    oidc_audience: str
    authenticated_https: bool
    response: Mapping[str, Any]


def execute_nfl_week_snapshot_preflight(
    selection: Mapping[str, Any],
    inventory: SourceInventory,
    *,
    independently_authenticated_source_sha256: str,
    trusted_now: datetime,
    post_daily_snapshot: Callable[[dict[str, Any]], AuthenticatedSnapshotResponse],
) -> dict[str, Any]:
    """Build and reconcile one bounded, serialized, authenticated NFL week.

    Only the trusted orchestrator may inject the transport callback. Returning
    a reconciled cohort NEVER certifies a fitted model or authorizes trading.
    No token, probability, or raw response is printed or returned.
    """
    plan = plan_nfl_week_dates(
        selection,
        inventory,
        independently_authenticated_source_sha256=independently_authenticated_source_sha256,
        trusted_now=trusted_now,
    )
    if plan["status"] != "DATE_PLAN_READY_NOT_ACTION_ACCEPTED":
        return plan

    dates = plan["dates"]
    if not 1 <= len(dates) <= MAX_DATE_CALLS:
        return hold("NFL_WEEK_DATE_REQUEST_BUDGET_INVALID")

    snapshots: dict[str, Mapping[str, Any]] = {}
    for batch in dates:
        day = batch["requested_slate_date"]
        payload = {
            "requested_slate_date": day,
            "requested_timezone": "America/Chicago",
            "lanes": ["MONEYLINE"],
            "max_props": 0,
            "max_team_events": 32,
            "response_mode": "FULL",
        }
        # Never retry after an ambiguous request outcome: doing so risks
        # duplicate scorer invocations. Reconcile persisted run receipt first.
        try:
            receipt = post_daily_snapshot(payload)
        except Exception:
            return hold("NFL_WEEK_SNAPSHOT_TRANSPORT_UNVERIFIED")

        if (
            not isinstance(receipt, AuthenticatedSnapshotResponse)
            or type(receipt.http_status) is not int
            or receipt.http_status != 200
            or receipt.oidc_audience != AUDIENCE
            or receipt.authenticated_https is not True
            or not isinstance(receipt.response, Mapping)
        ):
            return hold("NFL_WEEK_AUTHENTICATED_TRANSPORT_REQUIRED")

        if receipt.response.get("requested_slate_date") != day:
            return hold("NFL_WEEK_RESPONSE_DATE_MISMATCH")
        snapshots[day] = receipt.response

    result = reconcile_nfl_week_snapshots(plan, snapshots)
    # No model/release/Action/editor qualification is implied by snapshot
    # completeness, even when all exact-once date receipts reconcile.
    return result
