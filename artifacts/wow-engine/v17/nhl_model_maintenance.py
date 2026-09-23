"""Governed NHL historical-reconstruction -> D1 candidate maintenance.

This route may acquire official settled NHL results, reconstruct strictly prior-game
features, fit a research CANDIDATE, and persist immutable candidate evidence. It
cannot certify, promote, activate, publish, rank, or execute a wager.

Maintenance is intentionally replayable after candidate-lifecycle changes so a
protected-main merge can regenerate evidence against the exact deployed code SHA.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any, Iterable

from fastapi import FastAPI

from github_actions_oidc import scout_route_auth_dependency
from nhl_candidate_pipeline import NHLCandidateError, build_candidate
from v17.d1_bulk_candidate_registry import persist_candidate_package_bulk
from v17.d1_candidate_registry import D1RegistryError

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False


def default_start_years(now: datetime | None = None) -> tuple[int, ...]:
    current = (now or datetime.now(timezone.utc)).year
    # On Sep 15, 2026 the 2026-27 regular season has not produced settled
    # training observations yet. Use the five most recently completed/settled
    # season starts and never fabricate future outcomes.
    return tuple(range(current - 5, current))


def _blocked(code: str, *, stage: str, detail: Any = None) -> dict[str, Any]:
    return {
        "status": "BLOCKED",
        "code": code,
        "blocked_stage": stage,
        "detail": detail,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def _attach_training_outcomes(package: dict[str, Any]) -> None:
    outcomes = {
        str(game.get("official_event_id")): bool(game.get("positive_outcome"))
        for game in package.get("games") or []
        if game.get("official_event_id") is not None
    }
    for row in package.get("feature_rows") or []:
        event_id = str(row.get("official_event_id") or "")
        if event_id not in outcomes:
            raise D1RegistryError("NHL_TRAINING_OUTCOME_NOT_RECONCILED", event_id)
        row["positive_outcome"] = outcomes[event_id]


def run_nhl_model_maintenance(
    db: Any,
    *,
    start_years: Iterable[int] | None = None,
    training_code_sha: str | None = None,
    session: Any = None,
) -> dict[str, Any]:
    years = tuple(sorted({int(v) for v in (start_years or default_start_years())}))
    if not years:
        return _blocked("NHL_MAINTENANCE_RANGE_EMPTY", stage="CONFIGURATION")
    effective_sha = str(training_code_sha or os.getenv("RENDER_GIT_COMMIT") or "").strip()
    if not effective_sha:
        return _blocked("NHL_TRAINING_CODE_SHA_UNAVAILABLE", stage="CANDIDATE_TRAINING")

    try:
        kwargs: dict[str, Any] = {"training_code_sha": effective_sha}
        if session is not None:
            kwargs["session"] = session
        package = build_candidate(years, **kwargs)
        _attach_training_outcomes(package)
    except NHLCandidateError as exc:
        return _blocked(exc.code, stage="NHL_ACQUISITION_OR_TRAINING", detail=str(exc))
    except D1RegistryError as exc:
        return _blocked(exc.code, stage="NHL_RECONCILIATION", detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        return _blocked(
            "NHL_MODEL_MAINTENANCE_FAILED",
            stage="NHL_ACQUISITION_OR_TRAINING",
            detail={"error_type": type(exc).__name__},
        )

    try:
        persisted = persist_candidate_package_bulk(db, package)
    except D1RegistryError as exc:
        return _blocked(exc.code, stage="CANDIDATE_PERSISTENCE", detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        return _blocked(
            "NHL_CANDIDATE_PERSISTENCE_FAILED",
            stage="CANDIDATE_PERSISTENCE",
            detail={"error_type": type(exc).__name__},
        )

    candidate = package.get("candidate") or {}
    return {
        "status": "CANDIDATE_EVIDENCE_UPDATED",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sport": "NHL",
        "seasons": list(years),
        "source_event_n": len(package.get("games") or []),
        "training_feature_n": len(package.get("feature_rows") or []),
        "model_family": candidate.get("model_family"),
        "model_artifact_version": candidate.get("model_artifact_version"),
        "research_screen_pass": bool(candidate.get("research_screen_pass")),
        "source_review_status": candidate.get("source_review_status"),
        "persistence": persisted,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def install_nhl_model_maintenance_route(
    app: FastAPI,
    *,
    auth_dependency: Any,
    db_client_fn: Any,
) -> None:
    path = "/internal/v17/nhl-model-maintenance"
    if any(getattr(route, "path", None) == path for route in app.router.routes):
        return

    @app.post(
        path,
        dependencies=[scout_route_auth_dependency(auth_dependency)],
        operation_id="runWowV17NhlModelMaintenance",
    )
    def run_maintenance() -> dict[str, Any]:
        return run_nhl_model_maintenance(db_client_fn())


__all__ = [
    "CAN_EXECUTE",
    "default_start_years",
    "install_nhl_model_maintenance_route",
    "run_nhl_model_maintenance",
]
