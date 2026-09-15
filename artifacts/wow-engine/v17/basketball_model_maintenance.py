"""Governed NBA/WNBA training refresh and shadow-fit maintenance.

This module advances only model-development evidence:

  source hydration -> provenance check -> deterministic feature replay
  -> league-isolated fit/calibration -> SHADOW evidence

It never promotes or activates an artifact, never publishes a betting
probability, and never enables execution.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from fastapi import FastAPI

from basketball_event_hydration_runtime import BasketballHydrationError, hydrate
from basketball_training_replay import run_training_replay
from github_actions_oidc import scout_route_auth_dependency

CAN_EXECUTE = False
SUPPORTED_SPORTS = ("NBA", "WNBA")


def default_seasons(now: datetime | None = None) -> tuple[int, ...]:
    current = (now or datetime.now(timezone.utc)).year
    return tuple(range(current - 3, current + 1))


def _blocked(sport: str, code: str, *, error_type: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "sport": sport,
        "status": "BLOCKED",
        "code": code,
        "promotion_attempted": False,
        "probability_publishable": False,
        "can_execute": False,
    }
    if error_type:
        payload["error_type"] = error_type
    return payload


def run_basketball_model_maintenance(
    db: Any,
    *,
    sports: Iterable[str] = SUPPORTED_SPORTS,
    seasons: Iterable[int] | None = None,
) -> dict[str, Any]:
    season_values = tuple(int(v) for v in (seasons or default_seasons()))
    results: list[dict[str, Any]] = []

    for raw_sport in sports:
        sport = str(raw_sport or "").strip().upper()
        if sport not in SUPPORTED_SPORTS:
            results.append(_blocked(sport or "UNKNOWN", "BASKETBALL_SPORT_UNSUPPORTED"))
            continue
        try:
            hydration = hydrate(sport, season_values, client=db)
            replay = run_training_replay(sport, client=db)
            results.append({
                "sport": sport,
                "status": "SHADOW_EVIDENCE_UPDATED",
                "hydration": hydration,
                "training_replay": replay,
                "promotion_attempted": False,
                "probability_publishable": False,
                "can_execute": False,
            })
        except BasketballHydrationError as exc:
            code = str(exc) or "BASKETBALL_HYDRATION_FAILED"
            results.append(_blocked(sport, code, error_type=type(exc).__name__))
        except RuntimeError as exc:
            code = str(exc) or "BASKETBALL_TRAINING_REPLAY_BLOCKED"
            results.append(_blocked(sport, code, error_type=type(exc).__name__))
        except Exception as exc:  # noqa: BLE001 - typed fail-closed maintenance report
            results.append(_blocked(sport, "BASKETBALL_MODEL_MAINTENANCE_FAILED", error_type=type(exc).__name__))

    updated = sum(row["status"] == "SHADOW_EVIDENCE_UPDATED" for row in results)
    return {
        "status": "COMPLETE" if updated == len(results) else ("PARTIAL" if updated else "BLOCKED"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seasons": list(season_values),
        "rows": results,
        "rows_updated": updated,
        "rows_blocked": len(results) - updated,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def install_basketball_model_maintenance_route(
    app: FastAPI,
    *,
    auth_dependency: Any,
    db_client_fn: Any,
) -> None:
    path = "/internal/v17/basketball-model-maintenance"
    if any(getattr(route, "path", None) == path for route in app.router.routes):
        return

    @app.post(
        path,
        dependencies=[scout_route_auth_dependency(auth_dependency)],
        operation_id="runWowV17BasketballModelMaintenance",
    )
    def run_maintenance() -> dict[str, Any]:
        return run_basketball_model_maintenance(db_client_fn())


__all__ = [
    "CAN_EXECUTE",
    "SUPPORTED_SPORTS",
    "default_seasons",
    "install_basketball_model_maintenance_route",
    "run_basketball_model_maintenance",
]
