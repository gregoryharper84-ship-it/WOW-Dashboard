"""Authenticated candidate-maintenance routes for first-six open-data lanes."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any, Callable

from fastapi import FastAPI

from github_actions_oidc import scout_route_auth_dependency
from v17.ncaab_sportsdataverse_candidate import NCAABCandidateUnavailable, train_and_persist as train_ncaab
from v17.soccer_openfootball_candidate import SoccerCandidateUnavailable, train_all as train_soccer
from v17.tennis_valuebet_candidate import TennisCandidateUnavailable, train_all as train_tennis

CAN_EXECUTE = False


def _sha() -> str:
    return str(os.getenv("RENDER_GIT_COMMIT") or os.getenv("GIT_COMMIT_SHA") or "").strip().lower()


def _run(name: str, fn: Callable[..., dict[str, Any]], db: Any) -> dict[str, Any]:
    sha = _sha()
    if len(sha) < 7:
        return {
            "status": "BLOCKED", "code": f"{name}_TRAINING_CODE_SHA_UNAVAILABLE",
            "automatic_certification": False, "automatic_promotion": False,
            "probability_publishable": False, "can_execute": False,
        }
    try:
        result = fn(db, training_code_sha=sha)
    except (NCAABCandidateUnavailable, SoccerCandidateUnavailable, TennisCandidateUnavailable) as exc:
        return {
            "status": "BLOCKED", "code": exc.code, "detail": str(exc),
            "automatic_certification": False, "automatic_promotion": False,
            "probability_publishable": False, "can_execute": False,
        }
    except Exception as exc:  # noqa: BLE001 - typed maintenance boundary
        return {
            "status": "BLOCKED", "code": f"{name}_CANDIDATE_MAINTENANCE_FAILED",
            "detail": {"error_type": type(exc).__name__},
            "automatic_certification": False, "automatic_promotion": False,
            "probability_publishable": False, "can_execute": False,
        }
    return {
        "status": "CANDIDATE_EVIDENCE_UPDATED",
        "generated_at": datetime.now(timezone.utc).isoformat(), **result,
        "automatic_certification": False, "automatic_promotion": False,
        "probability_publishable": False, "can_execute": False,
    }


def install_first_six_open_data_maintenance_routes(
    app: FastAPI, *, auth_dependency: Any, db_client_fn: Any,
) -> None:
    dependency = scout_route_auth_dependency(auth_dependency)
    routes = {getattr(route, "path", None) for route in app.router.routes}

    if "/internal/v17/ncaab-model-maintenance" not in routes:
        @app.post(
            "/internal/v17/ncaab-model-maintenance", dependencies=[dependency],
            operation_id="runWowV17NcaabModelMaintenance",
        )
        def run_ncaab_maintenance() -> dict[str, Any]:
            return _run("NCAAB", train_ncaab, db_client_fn())

    if "/internal/v17/soccer-model-maintenance" not in routes:
        @app.post(
            "/internal/v17/soccer-model-maintenance", dependencies=[dependency],
            operation_id="runWowV17SoccerModelMaintenance",
        )
        def run_soccer_maintenance() -> dict[str, Any]:
            return _run("SOCCER", train_soccer, db_client_fn())

    if "/internal/v17/tennis-model-maintenance" not in routes:
        @app.post(
            "/internal/v17/tennis-model-maintenance", dependencies=[dependency],
            operation_id="runWowV17TennisModelMaintenance",
        )
        def run_tennis_maintenance() -> dict[str, Any]:
            return _run("TENNIS", train_tennis, db_client_fn())


__all__ = ["CAN_EXECUTE", "install_first_six_open_data_maintenance_routes"]
