"""Governed NBA/WNBA training refresh and shadow-fit maintenance.

This module advances only model-development evidence:

  source hydration -> provenance check -> deterministic feature replay
  -> league-isolated fit/calibration -> SHADOW evidence

Fresh provider acquisition and persisted-corpus replay are deliberately separate
stages. A temporary/missing acquisition provider must not prevent replay of an
already persisted, provenance-complete, fresh corpus. Acquisition degradation is
retained as typed metadata and never converted into model readiness.

It never promotes or activates an artifact, never publishes a betting
probability, and never enables execution.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from fastapi import FastAPI

from basketball_event_hydration_runtime import BasketballHydrationError, hydrate
from basketball_training_replay import run_training_replay
from basketball_certification_write import BasketballCertificationWriteError
from github_actions_oidc import scout_route_auth_dependency
from v17.team_event_model_development_manifest import development_lane

CAN_EXECUTE = False
SUPPORTED_SPORTS = ("NBA", "WNBA")


def default_seasons(now: datetime | None = None) -> tuple[int, ...]:
    current = (now or datetime.now(timezone.utc)).year
    return tuple(range(current - 3, current + 1))


_PROVIDER_ERROR_FIELDS = ("code", "message", "details", "hint")


def _exception_details(exc: Exception) -> dict[str, str]:
    """Carry the provider's own error fields so a blocked row names its cause.

    A PostgREST ``APIError`` exposes a dict on ``.message``; other exceptions
    only carry a string.  Nothing here is trusted as proof of success -- it is
    diagnostic detail attached to an already-failed, fail-closed row.
    """
    details: dict[str, str] = {}
    payload = getattr(exc, "message", None)
    if isinstance(payload, Mapping):
        for field in _PROVIDER_ERROR_FIELDS:
            value = payload.get(field)
            if value not in (None, ""):
                details[field] = str(value)[:500]
    elif isinstance(payload, str) and payload.strip():
        details["message"] = payload.strip()[:500]
    for field in _PROVIDER_ERROR_FIELDS:
        if field in details:
            continue
        value = getattr(exc, field, None)
        if value not in (None, ""):
            details[field] = str(value)[:500]
    if "message" not in details:
        text = str(exc).strip()
        if text:
            details["message"] = text[:500]
    return details


def _blocked(
    sport: str,
    code: str,
    *,
    error_type: str | None = None,
    error_details: Mapping[str, str] | None = None,
    hydration_blocker: dict[str, Any] | None = None,
) -> dict[str, Any]:
    lane = development_lane(sport)
    payload: dict[str, Any] = {
        "sport": sport,
        "status": "BLOCKED",
        "code": code,
        "model_development": lane.as_dict() if lane is not None else None,
        "promotion_attempted": False,
        "probability_publishable": False,
        "can_execute": False,
    }
    if error_type:
        payload["error_type"] = error_type
    if error_details:
        payload["error_details"] = dict(error_details)
    if hydration_blocker:
        payload["hydration_blocker"] = hydration_blocker
    return payload


def _hydration_blocker(exc: Exception) -> dict[str, Any]:
    code = str(exc) or "BASKETBALL_HYDRATION_FAILED"
    return {
        "status": "BLOCKED",
        "stage": "FRESH_SOURCE_HYDRATION",
        "code": code,
        "error_type": type(exc).__name__,
        "recoverable": True,
        "can_execute": False,
    }


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

        hydration: dict[str, Any] | None = None
        hydration_blocker: dict[str, Any] | None = None
        try:
            hydration = hydrate(sport, season_values, client=db)
        except BasketballHydrationError as exc:
            hydration_blocker = _hydration_blocker(exc)
        except Exception as exc:  # noqa: BLE001 - preserve typed acquisition degradation
            hydration_blocker = _hydration_blocker(exc)

        # A blocked fresh-source fetch is not proof that the persisted corpus is
        # unusable. Replay performs its own provenance + freshness preflights and
        # therefore remains the authority on whether existing history is safe to
        # use for candidate/shadow development.
        try:
            replay = run_training_replay(sport, client=db)
            lane = development_lane(sport)
            row: dict[str, Any] = {
                "sport": sport,
                "status": "SHADOW_EVIDENCE_UPDATED",
                "hydration": hydration,
                "hydration_status": (
                    "FRESH_ACQUISITION_UPDATED"
                    if hydration_blocker is None
                    else "FRESH_ACQUISITION_BLOCKED_USING_PERSISTED_CORPUS"
                ),
                "hydration_blocker": hydration_blocker,
                "training_replay": replay,
                "model_development": lane.as_dict() if lane is not None else None,
                "promotion_attempted": False,
                "probability_publishable": False,
                "can_execute": False,
            }
            results.append(row)
        except BasketballCertificationWriteError as exc:
            results.append(
                _blocked(
                    sport,
                    exc.code,
                    error_type=type(exc).__name__,
                    error_details=_exception_details(exc),
                    hydration_blocker=hydration_blocker,
                )
            )
        except RuntimeError as exc:
            results.append(
                _blocked(
                    sport,
                    str(exc) or "BASKETBALL_TRAINING_REPLAY_BLOCKED",
                    error_type=type(exc).__name__,
                    error_details=_exception_details(exc),
                    hydration_blocker=hydration_blocker,
                )
            )
        except Exception as exc:  # noqa: BLE001 - typed fail-closed maintenance report
            results.append(
                _blocked(
                    sport,
                    "BASKETBALL_MODEL_MAINTENANCE_FAILED",
                    error_type=type(exc).__name__,
                    error_details=_exception_details(exc),
                    hydration_blocker=hydration_blocker,
                )
            )

    updated = sum(row["status"] == "SHADOW_EVIDENCE_UPDATED" for row in results)
    degraded = sum(
        row.get("hydration_status") == "FRESH_ACQUISITION_BLOCKED_USING_PERSISTED_CORPUS"
        for row in results
    )
    return {
        "status": "COMPLETE" if updated == len(results) else ("PARTIAL" if updated else "BLOCKED"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seasons": list(season_values),
        "rows": results,
        "rows_updated": updated,
        "rows_blocked": len(results) - updated,
        "rows_fresh_acquisition_degraded": degraded,
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
