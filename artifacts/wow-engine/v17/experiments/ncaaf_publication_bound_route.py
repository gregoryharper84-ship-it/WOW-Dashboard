"""Authenticated evidence-only route for the NCAAF publication-bound challenger."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from github_actions_oidc import scout_route_auth_dependency
from v17.experiments.ncaaf_publication_bound_challenger import (
    NCAAFPublicationBoundExperimentError,
    run_ncaaf_publication_bound_challenger,
)

CAN_EXECUTE = False


def install_ncaaf_publication_bound_challenger_route(
    app: FastAPI, *, auth_dependency: Any, db_client_fn: Any
) -> None:
    path = "/internal/v17/experiments/ncaaf-publication-bound/{candidate_id}"
    if any(getattr(route, "path", None) == path for route in app.router.routes):
        return

    @app.post(
        path,
        dependencies=[scout_route_auth_dependency(auth_dependency)],
        operation_id="runWowV17NcaafPublicationBoundChallenger",
    )
    def run(candidate_id: str) -> dict[str, Any]:
        try:
            return run_ncaaf_publication_bound_challenger(db_client_fn(), candidate_id)
        except NCAAFPublicationBoundExperimentError as exc:
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


__all__ = ["CAN_EXECUTE", "install_ncaaf_publication_bound_challenger_route"]
