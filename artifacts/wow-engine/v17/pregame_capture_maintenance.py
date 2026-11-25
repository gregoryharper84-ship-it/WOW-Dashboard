"""OIDC maintenance route for bounded, research-only pregame capture.

This is the wiring layer between the scheduler in
``v17.pregame_capture_schedule`` and the governed internal automation surface
used by the sport-maintenance workflows. It runs at most one bounded cycle per
supported sport and returns immutable receipts for the caller to retain.

It never fits, certifies, promotes, publishes, ranks or executes anything, and
it never converts an NHL goalie candidate into a confirmed starter.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any, Iterable

from fastapi import FastAPI

from github_actions_oidc import scout_route_auth_dependency
from v17.pregame_capture_schedule import (
    MAX_CAPTURES_PER_CYCLE,
    run_pregame_capture_cycle,
)

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
SUPPORTED_SPORTS = ("NBA", "NHL")
ROUTE_PATH = "/internal/v17/pregame-capture-maintenance"


def _blocked_row(sport: str, code: str, *, error_type: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "sport": sport or "UNKNOWN",
        "status": "BLOCKED",
        "code": code,
        "blockers": [code],
        "captures_attempted": 0,
        "captures_evidence_insufficient": 0,
        "captures_candidates_only": 0,
        "retention": None,
        "receipts": [],
        "starter_inference_allowed": False,
        "probability_publishable": False,
        "can_execute": False,
    }
    if error_type:
        row["error_type"] = error_type
    return row


def run_pregame_capture_maintenance(
    *,
    now: datetime | None = None,
    fetcher: Any = None,
    sports: Iterable[str] = SUPPORTED_SPORTS,
    artifact_root: str | None = None,
    max_captures: int = MAX_CAPTURES_PER_CYCLE,
) -> dict[str, Any]:
    """Run one bounded capture cycle per sport and preserve every blocker."""
    generated_at = now or datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for raw_sport in sports:
        sport = str(raw_sport or "").strip().upper()
        if sport not in SUPPORTED_SPORTS:
            rows.append(_blocked_row(sport, "PREGAME_CAPTURE_SPORT_UNSUPPORTED"))
            continue
        kwargs: dict[str, Any] = {"max_captures": max_captures}
        if now is not None:
            kwargs["now"] = now
        if fetcher is not None:
            kwargs["fetcher"] = fetcher
        if artifact_root is not None:
            kwargs["artifact_root"] = artifact_root
        try:
            cycle = run_pregame_capture_cycle(sport, **kwargs)
        except Exception as exc:  # noqa: BLE001 - typed fail-closed maintenance row
            rows.append(
                _blocked_row(
                    sport,
                    "PREGAME_CAPTURE_MAINTENANCE_FAILED",
                    error_type=type(exc).__name__,
                )
            )
            continue
        retention = cycle.get("retention") or {}
        rows.append(
            {
                "sport": sport,
                "status": cycle.get("status"),
                "code": cycle.get("status"),
                "blockers": list(cycle.get("blockers") or []),
                "captures_attempted": int(cycle.get("captures_attempted") or 0),
                "captures_evidence_insufficient": int(
                    cycle.get("captures_evidence_insufficient") or 0
                ),
                "captures_candidates_only": int(
                    cycle.get("captures_candidates_only") or 0
                ),
                "retention": {
                    "retained_count": retention.get("retained_count"),
                    "superseded_count": retention.get("superseded_count"),
                    "deduplicated_sha256": retention.get("deduplicated_sha256"),
                    "deletion_performed": retention.get("deletion_performed"),
                },
                "receipts": list(cycle.get("receipts") or []),
                "starter_inference_allowed": False,
                "probability_publishable": False,
                "can_execute": False,
            }
        )

    completed = sum(1 for row in rows if row["status"] == "CYCLE_COMPLETE")
    status = "COMPLETE" if completed == len(rows) and rows else (
        "PARTIAL" if completed else "BLOCKED"
    )
    return {
        "status": status,
        "generated_at": generated_at.astimezone(timezone.utc).isoformat(),
        "route": ROUTE_PATH,
        "rows": rows,
        "rows_completed": completed,
        "rows_blocked": len(rows) - completed,
        "automatic_certification": False,
        "automatic_promotion": False,
        "fitted_model": False,
        "starter_inference_allowed": False,
        "calibration_status": "NOT_CALIBRATED_RESEARCH_ONLY",
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def install_pregame_capture_route(
    app: FastAPI,
    *,
    auth_dependency: Any,
    capture_runner: Any = None,
) -> None:
    """Install the internal capture route once, behind the OIDC auth seam."""
    if any(getattr(route, "path", None) == ROUTE_PATH for route in app.router.routes):
        return

    runner = capture_runner or (
        lambda: run_pregame_capture_maintenance(
            artifact_root=os.getenv("WOW_PREGAME_CAPTURE_ROOT") or None
        )
    )

    @app.post(
        ROUTE_PATH,
        dependencies=[scout_route_auth_dependency(auth_dependency)],
        operation_id="runWowV17PregameCaptureMaintenance",
    )
    def run_capture() -> dict[str, Any]:
        return runner()


__all__ = [
    "CAN_EXECUTE",
    "PROBABILITY_PUBLISHABLE",
    "ROUTE_PATH",
    "SUPPORTED_SPORTS",
    "install_pregame_capture_route",
    "run_pregame_capture_maintenance",
]
