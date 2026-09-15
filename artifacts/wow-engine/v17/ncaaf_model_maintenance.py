"""Governed NCAAF data -> feature -> candidate-model maintenance lane.

This lane advances only research/candidate evidence. It may hydrate raw CFBD
history, materialize settled games, compile complete pregame features, and fit a
CANDIDATE artifact when enough complete rows exist. It cannot activate, promote,
certify, publish a probability, or execute a wager.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import os
from typing import Any, Iterable

from fastapi import FastAPI

from github_actions_oidc import scout_route_auth_dependency
from ncaaf_candidate_training_runner import (
    NCAAFTrainingRunnerUnavailable,
    train_and_persist_candidate,
)
from ncaaf_cfbd_client import CFBDClient, CFBDUnavailable
from ncaaf_cfbd_hydrator import hydrate_cfbd_season, persist_source_snapshots
from ncaaf_training_materializer import materialize_training_games
from ncaaf_feature_compiler import materialize_complete_training_features
from v17.nhl_model_maintenance import install_nhl_model_maintenance_route

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False


def default_seasons(now: datetime | None = None) -> tuple[int, ...]:
    current = (now or datetime.now(timezone.utc)).year
    return tuple(range(current - 4, current + 1))


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


def run_ncaaf_model_maintenance(
    db: Any,
    *,
    seasons: Iterable[int] | None = None,
    weeks: Iterable[int] = range(1, 21),
    training_code_sha: str | None = None,
) -> dict[str, Any]:
    season_values = tuple(sorted({int(v) for v in (seasons or default_seasons())}))
    week_values = tuple(sorted({int(v) for v in weeks}))
    if not season_values or not week_values:
        return _blocked("NCAAF_MAINTENANCE_RANGE_EMPTY", stage="CONFIGURATION")

    try:
        cfbd = CFBDClient.from_environment()
    except CFBDUnavailable as exc:
        return _blocked(exc.code, stage="CFBD_ACQUISITION")

    acquisition: list[dict[str, Any]] = []
    source_snapshot_n = 0
    source_persisted_n = 0
    training_game_candidate_n = 0
    training_game_persisted_n = 0
    acquisition_blockers: set[str] = set()

    for season in season_values:
        try:
            snapshots = hydrate_cfbd_season(
                cfbd,
                season=season,
                weeks=week_values,
                rating_families=("elo",),
                classification="fbs",
            )
            persisted_n = persist_source_snapshots(db, snapshots)
            games = materialize_training_games(db, snapshots)
        except CFBDUnavailable as exc:
            return _blocked(exc.code, stage="CFBD_ACQUISITION", detail={"season": season})
        except Exception as exc:  # noqa: BLE001 - typed maintenance failure
            return _blocked(
                "NCAAF_HISTORY_HYDRATION_FAILED",
                stage="CFBD_ACQUISITION",
                detail={"season": season, "error_type": type(exc).__name__},
            )

        source_snapshot_n += len(snapshots)
        source_persisted_n += int(persisted_n)
        training_game_candidate_n += int(games.candidate_rows)
        training_game_persisted_n += int(games.persisted_rows)
        acquisition_blockers.update(code for snap in snapshots for code in snap.blocker_codes)
        acquisition_blockers.update(games.blocker_codes)
        acquisition.append({
            "season": season,
            "source_snapshot_n": len(snapshots),
            "source_snapshot_persisted_n": persisted_n,
            "training_games": asdict(games),
        })

    try:
        feature_report = materialize_complete_training_features(db)
    except Exception as exc:  # noqa: BLE001
        return _blocked(
            "NCAAF_FEATURE_COMPILATION_FAILED",
            stage="FEATURE_COMPILATION",
            detail={"error_type": type(exc).__name__},
        )

    complete_n = int(feature_report.get("complete_feature_rows") or 0)
    training: dict[str, Any] | None = None
    training_blocker: dict[str, Any] | None = None
    if complete_n >= 300:
        effective_sha = str(training_code_sha or os.getenv("RENDER_GIT_COMMIT") or "").strip()
        if not effective_sha:
            training_blocker = {
                "code": "NCAAF_TRAINING_CODE_SHA_UNAVAILABLE",
                "stage": "CANDIDATE_TRAINING",
            }
        else:
            try:
                training = train_and_persist_candidate(db, training_code_sha=effective_sha)
            except NCAAFTrainingRunnerUnavailable as exc:
                training_blocker = {
                    "code": exc.code,
                    "stage": "CANDIDATE_TRAINING",
                    "detail": str(exc),
                }
            except Exception as exc:  # noqa: BLE001
                training_blocker = {
                    "code": "NCAAF_CANDIDATE_TRAINING_FAILED",
                    "stage": "CANDIDATE_TRAINING",
                    "detail": type(exc).__name__,
                }
    else:
        training_blocker = {
            "code": "NCAAF_COMPLETE_TRAINING_ROWS_INSUFFICIENT",
            "stage": "CANDIDATE_TRAINING",
            "detail": f"complete_rows={complete_n}; minimum=300",
        }

    status = "CANDIDATE_EVIDENCE_UPDATED" if training and training.get("ok") is True else "BLOCKED"
    blockers = sorted(set(acquisition_blockers) | ({str(training_blocker["code"])} if training_blocker else set()))
    return {
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seasons": list(season_values),
        "weeks": [min(week_values), max(week_values)],
        "acquisition": acquisition,
        "source_snapshot_n": source_snapshot_n,
        "source_snapshot_persisted_n": source_persisted_n,
        "training_game_candidate_n": training_game_candidate_n,
        "training_game_persisted_n": training_game_persisted_n,
        "feature_compilation": feature_report,
        "candidate_training": training,
        "training_blocker": training_blocker,
        "blockers": blockers,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def install_ncaaf_model_maintenance_route(
    app: FastAPI,
    *,
    auth_dependency: Any,
    db_client_fn: Any,
) -> None:
    # Compose the next D1 candidate lane at the same authenticated internal
    # maintenance boundary. This remains candidate-only and does not alter
    # serving/scoring ownership for NCAAF or NHL.
    install_nhl_model_maintenance_route(
        app,
        auth_dependency=auth_dependency,
        db_client_fn=db_client_fn,
    )

    path = "/internal/v17/ncaaf-model-maintenance"
    if any(getattr(route, "path", None) == path for route in app.router.routes):
        return

    @app.post(
        path,
        dependencies=[scout_route_auth_dependency(auth_dependency)],
        operation_id="runWowV17NcaafModelMaintenance",
    )
    def run_maintenance() -> dict[str, Any]:
        return run_ncaaf_model_maintenance(db_client_fn())


__all__ = [
    "CAN_EXECUTE",
    "default_seasons",
    "install_ncaaf_model_maintenance_route",
    "run_ncaaf_model_maintenance",
]
