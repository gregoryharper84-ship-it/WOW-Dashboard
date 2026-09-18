"""Governed NCAAF player-prop history maintenance.

This lane closes the prerequisite data gap for CFB prop specialists by staging
CFBD player box scores and materializing exact primitive stat histories. It is
candidate/research infrastructure only: no specialist is registered, no model
artifact is certified, and no probability is publishable from this module.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from fastapi import FastAPI

from github_actions_oidc import scout_route_auth_dependency
from ncaaf_cfbd_client import CFBDClient, CFBDUnavailable
from ncaaf_cfbd_hydrator import hydrate_cfbd_player_stats, persist_source_snapshots
from ncaaf_player_prop_materializer import materialize_player_stats, persist_player_stats

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False


def default_seasons(now: datetime | None = None) -> tuple[int, ...]:
    current = (now or datetime.now(timezone.utc)).year
    return tuple(range(current - 3, current + 1))


def _blocked(code: str, *, stage: str, detail: Any = None) -> dict[str, Any]:
    return {
        "status": "BLOCKED",
        "code": code,
        "blocked_stage": stage,
        "detail": detail,
        "model_capability_status": "MODEL_UNAVAILABLE",
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def run_ncaaf_player_prop_history_maintenance(
    db: Any,
    *,
    seasons: Iterable[int] | None = None,
    weeks: Iterable[int] = range(1, 21),
) -> dict[str, Any]:
    season_values = tuple(sorted({int(v) for v in (seasons or default_seasons())}))
    week_values = tuple(sorted({int(v) for v in weeks}))
    if not season_values or not week_values:
        return _blocked("NCAAF_PLAYER_PROP_HISTORY_RANGE_EMPTY", stage="CONFIGURATION")

    try:
        client = CFBDClient.from_environment()
    except CFBDUnavailable as exc:
        return _blocked(exc.code, stage="CFBD_PLAYER_STAT_ACQUISITION")

    source_snapshot_n = 0
    source_persisted_n = 0
    player_stat_candidate_n = 0
    player_stat_persisted_n = 0
    empty_snapshot_n = 0
    acquisition: list[dict[str, Any]] = []

    for season in season_values:
        try:
            snapshots = hydrate_cfbd_player_stats(
                client,
                season=season,
                weeks=week_values,
                classification="fbs",
                season_type="regular",
            )
            persisted_source = persist_source_snapshots(db, snapshots)
            observations = []
            for snapshot in snapshots:
                if snapshot.acquisition_status == "EMPTY":
                    empty_snapshot_n += 1
                observations.extend(materialize_player_stats(snapshot))
            persisted_stats = persist_player_stats(db, observations)
        except CFBDUnavailable as exc:
            return _blocked(exc.code, stage="CFBD_PLAYER_STAT_ACQUISITION", detail={"season": season})
        except Exception as exc:  # noqa: BLE001
            return _blocked(
                "NCAAF_PLAYER_PROP_HISTORY_MATERIALIZATION_FAILED",
                stage="PLAYER_STAT_MATERIALIZATION",
                detail={"season": season, "error_type": type(exc).__name__},
            )

        source_snapshot_n += len(snapshots)
        source_persisted_n += int(persisted_source)
        player_stat_candidate_n += len(observations)
        player_stat_persisted_n += int(persisted_stats)
        acquisition.append(
            {
                "season": season,
                "source_snapshot_n": len(snapshots),
                "source_snapshot_persisted_n": int(persisted_source),
                "player_stat_candidate_n": len(observations),
                "player_stat_persisted_n": int(persisted_stats),
            }
        )

    if player_stat_candidate_n <= 0:
        return _blocked(
            "NCAAF_PLAYER_PROP_HISTORY_EMPTY",
            stage="PLAYER_STAT_MATERIALIZATION",
            detail={"seasons": list(season_values), "weeks": [min(week_values), max(week_values)]},
        )

    return {
        "status": "PLAYER_PROP_HISTORY_READY",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seasons": list(season_values),
        "weeks": [min(week_values), max(week_values)],
        "acquisition": acquisition,
        "source_snapshot_n": source_snapshot_n,
        "source_snapshot_persisted_n": source_persisted_n,
        "player_stat_candidate_n": player_stat_candidate_n,
        "player_stat_persisted_n": player_stat_persisted_n,
        "empty_snapshot_n": empty_snapshot_n,
        "supported_primitives": [
            "PASS_YARDS",
            "PASS_TDS",
            "INTERCEPTIONS_THROWN",
            "PASS_COMPLETIONS",
            "PASS_ATTEMPTS",
            "RUSH_YARDS",
            "RUSH_TDS",
            "RUSH_ATTEMPTS",
            "RECEIVING_YARDS",
            "RECEIVING_TDS",
            "RECEPTIONS",
        ],
        "next_gate": "PLAYER_PROP_CANDIDATE_TRAINING_AND_FORWARD_VALIDATION",
        "model_capability_status": "MODEL_UNAVAILABLE",
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def install_ncaaf_player_prop_maintenance_route(
    app: FastAPI,
    *,
    auth_dependency: Any,
    db_client_fn: Any,
) -> None:
    path = "/internal/v17/ncaaf-player-prop-history-maintenance"
    if any(getattr(route, "path", None) == path for route in app.router.routes):
        return

    @app.post(
        path,
        dependencies=[scout_route_auth_dependency(auth_dependency)],
        operation_id="runWowV17NcaafPlayerPropHistoryMaintenance",
    )
    def run_maintenance() -> dict[str, Any]:
        return run_ncaaf_player_prop_history_maintenance(db_client_fn())


__all__ = [
    "CAN_EXECUTE",
    "default_seasons",
    "install_ncaaf_player_prop_maintenance_route",
    "run_ncaaf_player_prop_history_maintenance",
]
