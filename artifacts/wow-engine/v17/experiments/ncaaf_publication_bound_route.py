"""Authenticated evidence-only routes for NCAAF publication-bound challengers."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI

from github_actions_oidc import scout_route_auth_dependency
from v17.experiments.ncaaf_publication_bound_challenger import (
    NCAAFPublicationBoundExperimentError,
    run_ncaaf_publication_bound_challenger,
)
from v17.experiments.ncaaf_publication_bound_challenger_v3 import (
    run_ncaaf_publication_bound_challenger_v3,
)

CAN_EXECUTE = False


def _blocked(exc: NCAAFPublicationBoundExperimentError, candidate_id: str) -> dict[str, Any]:
    return {
        "status": "EXPERIMENT_BLOCKED",
        "code": exc.code,
        "detail": str(exc),
        "candidate_id": candidate_id,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "database_mutated": False,
        "production_registry_mutated": False,
        "global_terminal_reducer": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }


def _install(
    app: FastAPI,
    *,
    path: str,
    operation_id: str,
    auth_dependency: Any,
    db_client_fn: Any,
    runner: Callable[[Any, str], dict[str, Any]],
) -> None:
    if any(getattr(route, "path", None) == path for route in app.router.routes):
        return

    @app.post(
        path,
        dependencies=[scout_route_auth_dependency(auth_dependency)],
        operation_id=operation_id,
    )
    def run(candidate_id: str) -> dict[str, Any]:
        try:
            return runner(db_client_fn(), candidate_id)
        except NCAAFPublicationBoundExperimentError as exc:
            return _blocked(exc, candidate_id)


def install_ncaaf_publication_bound_challenger_route(
    app: FastAPI, *, auth_dependency: Any, db_client_fn: Any
) -> None:
    _install(
        app,
        path="/internal/v17/experiments/ncaaf-publication-bound/{candidate_id}",
        operation_id="runWowV17NcaafPublicationBoundChallenger",
        auth_dependency=auth_dependency,
        db_client_fn=db_client_fn,
        runner=run_ncaaf_publication_bound_challenger,
    )
    _install(
        app,
        path="/internal/v17/experiments/ncaaf-publication-bound-v3/{candidate_id}",
        operation_id="runWowV17NcaafPublicationBoundChallengerV3",
        auth_dependency=auth_dependency,
        db_client_fn=db_client_fn,
        runner=run_ncaaf_publication_bound_challenger_v3,
    )


__all__ = ["CAN_EXECUTE", "install_ncaaf_publication_bound_challenger_route"]
