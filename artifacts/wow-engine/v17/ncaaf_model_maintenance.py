"""Governed NCAAF data -> feature -> candidate-model maintenance lane.

The rich NCAAF feature lane remains preferred. When fresh CFBD acquisition is
unavailable, maintenance may continue from the already-persisted governed corpus
and fit the separately identified result/form baseline. Fresh acquisition
failure is retained as typed degradation; it is never hidden or converted into
production readiness.

The fallback never fills missing rich features, never uses market prices, and
remains CANDIDATE-only.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import os
from typing import Any, Iterable

from fastapi import FastAPI

from github_actions_oidc import scout_route_auth_dependency
from ncaaf_candidate_training_runner import NCAAFTrainingRunnerUnavailable, train_and_persist_candidate
from ncaaf_cfbd_client import CFBDClient, CFBDUnavailable
from ncaaf_cfbd_hydrator import hydrate_cfbd_season, persist_source_snapshots
from ncaaf_training_materializer import materialize_training_games
from ncaaf_feature_compiler import materialize_complete_training_features
from v17.first_six_open_data_maintenance import install_first_six_open_data_maintenance_routes
from v17.ncaaf_result_form_candidate import NCAAFResultFormUnavailable, train_and_persist as train_result_form_candidate
from v17.nhl_model_maintenance import install_nhl_model_maintenance_route
from v17.spread_margin_replay_route import install_spread_margin_replay_route
from v17.team_event_certification_replay import install_team_event_certification_replay_route
from v17.team_event_model_development_manifest import development_lane

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False


def default_seasons(now: datetime | None = None) -> tuple[int, ...]:
    current = (now or datetime.now(timezone.utc)).year
    return tuple(range(current - 4, current + 1))


def _blocked(code: str, *, stage: str, detail: Any = None) -> dict[str, Any]:
    lane = development_lane("NCAAF")
    return {
        "status": "BLOCKED", "code": code, "blocked_stage": stage, "detail": detail,
        "model_development": lane.as_dict() if lane is not None else None,
        "automatic_certification": False, "automatic_promotion": False,
        "probability_publishable": False, "can_execute": False,
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

    acquisition: list[dict[str, Any]] = []
    source_snapshot_n = source_persisted_n = 0
    training_game_candidate_n = training_game_persisted_n = 0
    acquisition_blockers: set[str] = set()
    fresh_acquisition_complete = True

    try:
        cfbd: CFBDClient | None = CFBDClient.from_environment()
    except CFBDUnavailable as exc:
        # Fresh acquisition is desirable but not allowed to erase the already
        # persisted corpus. Continue to feature compilation/candidate training;
        # those stages have their own provenance/sample gates and remain the
        # authority on whether stored evidence is usable.
        cfbd = None
        fresh_acquisition_complete = False
        acquisition_blockers.add(exc.code)
        acquisition.append({
            "status": "BLOCKED_USING_PERSISTED_CORPUS",
            "code": exc.code,
            "blocked_stage": "CFBD_ACQUISITION",
            "can_execute": False,
        })

    if cfbd is not None:
        for season in season_values:
            try:
                snapshots = hydrate_cfbd_season(
                    cfbd, season=season, weeks=week_values, rating_families=("elo",), classification="fbs"
                )
                persisted_n = persist_source_snapshots(db, snapshots)
                games = materialize_training_games(db, snapshots)
            except CFBDUnavailable as exc:
                fresh_acquisition_complete = False
                acquisition_blockers.add(exc.code)
                acquisition.append({
                    "season": season,
                    "status": "BLOCKED_USING_PERSISTED_CORPUS",
                    "code": exc.code,
                    "blocked_stage": "CFBD_ACQUISITION",
                    "can_execute": False,
                })
                break
            except Exception as exc:  # noqa: BLE001
                fresh_acquisition_complete = False
                acquisition_blockers.add("NCAAF_HISTORY_HYDRATION_FAILED")
                acquisition.append({
                    "season": season,
                    "status": "BLOCKED_USING_PERSISTED_CORPUS",
                    "code": "NCAAF_HISTORY_HYDRATION_FAILED",
                    "blocked_stage": "CFBD_ACQUISITION",
                    "error_type": type(exc).__name__,
                    "can_execute": False,
                })
                break
            source_snapshot_n += len(snapshots)
            source_persisted_n += int(persisted_n)
            training_game_candidate_n += int(games.candidate_rows)
            training_game_persisted_n += int(games.persisted_rows)
            acquisition_blockers.update(code for snap in snapshots for code in snap.blocker_codes)
            acquisition_blockers.update(games.blocker_codes)
            acquisition.append({
                "season": season, "status": "UPDATED", "source_snapshot_n": len(snapshots),
                "source_snapshot_persisted_n": persisted_n, "training_games": asdict(games),
            })

    try:
        feature_report = materialize_complete_training_features(db)
    except Exception as exc:  # noqa: BLE001
        return _blocked(
            "NCAAF_FEATURE_COMPILATION_FAILED",
            stage="FEATURE_COMPILATION",
            detail={
                "error_type": type(exc).__name__,
                "fresh_acquisition_complete": fresh_acquisition_complete,
                "acquisition_blockers": sorted(acquisition_blockers),
            },
        )

    complete_n = int(feature_report.get("complete_feature_rows") or 0)
    effective_sha = str(training_code_sha or os.getenv("RENDER_GIT_COMMIT") or "").strip()
    training: dict[str, Any] | None = None
    training_blocker: dict[str, Any] | None = None
    candidate_lane: str | None = None
    if not effective_sha:
        training_blocker = {"code": "NCAAF_TRAINING_CODE_SHA_UNAVAILABLE", "stage": "CANDIDATE_TRAINING"}
    elif complete_n >= 300:
        candidate_lane = "RICH_FEATURES_V1"
        try:
            training = train_and_persist_candidate(db, training_code_sha=effective_sha)
        except NCAAFTrainingRunnerUnavailable as exc:
            training_blocker = {"code": exc.code, "stage": "CANDIDATE_TRAINING", "detail": str(exc)}
        except Exception as exc:  # noqa: BLE001
            training_blocker = {"code": "NCAAF_CANDIDATE_TRAINING_FAILED", "stage": "CANDIDATE_TRAINING", "detail": type(exc).__name__}
    else:
        # Never back-fill unavailable rich evidence. Train a separately named,
        # prior-results-only candidate whose feature contract is auditable.
        candidate_lane = "RESULT_FORM_PRIOR_V1"
        try:
            training = train_result_form_candidate(db, training_code_sha=effective_sha)
        except NCAAFResultFormUnavailable as exc:
            training_blocker = {"code": exc.code, "stage": "RESULT_FORM_CANDIDATE_TRAINING", "detail": str(exc)}
        except Exception as exc:  # noqa: BLE001
            training_blocker = {"code": "NCAAF_RESULT_FORM_CANDIDATE_TRAINING_FAILED", "stage": "RESULT_FORM_CANDIDATE_TRAINING", "detail": type(exc).__name__}

    status = "CANDIDATE_EVIDENCE_UPDATED" if training and training.get("ok") is True else "BLOCKED"
    blockers = sorted(set(acquisition_blockers) | ({str(training_blocker["code"])} if training_blocker else set()))
    lane = development_lane("NCAAF")
    return {
        "status": status, "generated_at": datetime.now(timezone.utc).isoformat(),
        "seasons": list(season_values), "weeks": [min(week_values), max(week_values)],
        "acquisition": acquisition,
        "fresh_acquisition_complete": fresh_acquisition_complete,
        "maintenance_degraded": not fresh_acquisition_complete,
        "source_snapshot_n": source_snapshot_n,
        "source_snapshot_persisted_n": source_persisted_n,
        "training_game_candidate_n": training_game_candidate_n,
        "training_game_persisted_n": training_game_persisted_n,
        "feature_compilation": feature_report, "candidate_lane": candidate_lane,
        "candidate_training": training, "training_blocker": training_blocker,
        "blockers": blockers,
        "model_development": lane.as_dict() if lane is not None else None,
        "automatic_certification": False,
        "automatic_promotion": False, "probability_publishable": False, "can_execute": False,
    }


def install_ncaaf_model_maintenance_route(app: FastAPI, *, auth_dependency: Any, db_client_fn: Any) -> None:
    # Compose candidate-only maintenance and read-only certification surfaces
    # behind the same strict auth. None of these routes can promote/certify.
    install_nhl_model_maintenance_route(app, auth_dependency=auth_dependency, db_client_fn=db_client_fn)
    install_first_six_open_data_maintenance_routes(
        app, auth_dependency=auth_dependency, db_client_fn=db_client_fn
    )
    install_team_event_certification_replay_route(
        app, auth_dependency=auth_dependency, db_client_fn=db_client_fn
    )
    install_spread_margin_replay_route(
        app, auth_dependency=auth_dependency, db_client_fn=db_client_fn
    )
    path = "/internal/v17/ncaaf-model-maintenance"
    if any(getattr(route, "path", None) == path for route in app.router.routes):
        return

    @app.post(path, dependencies=[scout_route_auth_dependency(auth_dependency)], operation_id="runWowV17NcaafModelMaintenance")
    def run_maintenance() -> dict[str, Any]:
        return run_ncaaf_model_maintenance(db_client_fn())


__all__ = ["CAN_EXECUTE", "default_seasons", "install_ncaaf_model_maintenance_route", "run_ncaaf_model_maintenance"]