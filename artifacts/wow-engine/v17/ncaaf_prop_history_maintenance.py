"""Governed NCAAF player-prop historical corpus acquisition.

This module closes the earliest common blocker for the seven Phase-1 NCAAF prop
families: immutable player-game box-score history. It deliberately stops before
canonical stat mapping, feature construction, fitting, calibration, certification,
promotion, or production registration.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from fastapi import FastAPI

from github_actions_oidc import scout_route_auth_dependency
from ncaaf_cfbd_client import CFBDClient, CFBDUnavailable
from ncaaf_cfbd_hydrator import (
    hydrate_cfbd_player_stats_season,
    persist_source_snapshots,
    profile_cfbd_player_stats,
)

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
PHASE1_STAT_TYPES = (
    "PASSING_YARDS",
    "COMPLETIONS",
    "RUSHING_YARDS",
    "RECEPTIONS",
    "RECEIVING_YARDS",
    "RUSH_ATTEMPTS",
    "PASS_ATTEMPTS",
)


def default_seasons(now: datetime | None = None) -> tuple[int, ...]:
    current = (now or datetime.now(timezone.utc)).year
    return tuple(range(current - 3, current + 1))


def _blocked(code: str, *, stage: str, detail: Any = None) -> dict[str, Any]:
    return {
        "status": "BLOCKED",
        "code": code,
        "blocked_stage": stage,
        "detail": detail,
        "phase1_stat_types": list(PHASE1_STAT_TYPES),
        "normalization_ready": False,
        "model_build_ready": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def run_ncaaf_prop_history_maintenance(
    db: Any,
    *,
    seasons: Iterable[int] | None = None,
    weeks: Iterable[int] = range(1, 21),
) -> dict[str, Any]:
    season_values = tuple(sorted({int(v) for v in (seasons or default_seasons())}))
    week_values = tuple(sorted({int(v) for v in weeks}))
    if not season_values or not week_values:
        return _blocked("NCAAF_PROP_HISTORY_RANGE_EMPTY", stage="CONFIGURATION")

    try:
        client = CFBDClient.from_environment()
    except CFBDUnavailable as exc:
        return _blocked(exc.code, stage="CFBD_PLAYER_HISTORY_ACQUISITION")

    all_snapshots = []
    season_receipts: list[dict[str, Any]] = []
    persisted_total = 0
    for season in season_values:
        try:
            snapshots = hydrate_cfbd_player_stats_season(
                client,
                season=season,
                weeks=week_values,
                classification="fbs",
                season_type="both",
            )
            persisted_n = persist_source_snapshots(db, snapshots)
        except CFBDUnavailable as exc:
            return _blocked(
                exc.code,
                stage="CFBD_PLAYER_HISTORY_ACQUISITION",
                detail={
                    "season": season,
                    "completed_seasons": [row["season"] for row in season_receipts],
                    "persisted_before_block": persisted_total,
                },
            )
        except Exception as exc:  # noqa: BLE001
            return _blocked(
                "NCAAF_PROP_HISTORY_PERSISTENCE_FAILED",
                stage="PLAYER_HISTORY_PERSISTENCE",
                detail={"season": season, "error_type": type(exc).__name__},
            )

        profile = profile_cfbd_player_stats(snapshots)
        all_snapshots.extend(snapshots)
        persisted_total += int(persisted_n)
        season_receipts.append(
            {
                "season": season,
                "source_snapshot_n": len(snapshots),
                "source_snapshot_persisted_n": int(persisted_n),
                "game_n": profile["game_n"],
                "participant_id_n": profile["participant_id_n"],
                "athlete_stat_n": profile["athlete_stat_n"],
                "schema_status": profile["status"],
                "schema_blocker": profile["blocker"],
                "can_execute": False,
            }
        )

    profile = profile_cfbd_player_stats(all_snapshots)
    if profile.get("status") != "READY_FOR_NORMALIZATION":
        return _blocked(
            str(profile.get("blocker") or "NCAAF_PROP_PLAYER_STATS_NOT_NORMALIZATION_READY"),
            stage="PLAYER_HISTORY_SCHEMA_REVIEW",
            detail={
                "profile": profile,
                "season_receipts": season_receipts,
                "source_snapshot_persisted_n": persisted_total,
            },
        )

    return {
        "status": "CORPUS_UPDATED",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seasons": list(season_values),
        "weeks": [min(week_values), max(week_values)],
        "phase1_stat_types": list(PHASE1_STAT_TYPES),
        "season_receipts": season_receipts,
        "source_snapshot_n": len(all_snapshots),
        "source_snapshot_persisted_n": persisted_total,
        "player_stat_profile": profile,
        "normalization_ready": True,
        # Source acquisition alone never means a fitted specialist can be built
        # or promoted. Canonical stat mapping + chronological feature/training
        # evidence remain explicit next gates.
        "model_build_ready": False,
        "next_gate": "NCAAF_PROP_CANONICAL_STAT_NORMALIZATION_AND_TRAINING_CORPUS",
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def install_ncaaf_prop_history_maintenance_route(
    app: FastAPI,
    *,
    auth_dependency: Any,
    db_client_fn: Any,
) -> None:
    path = "/internal/v17/ncaaf-prop-history-maintenance"
    if any(getattr(route, "path", None) == path for route in app.router.routes):
        return

    @app.post(
        path,
        dependencies=[scout_route_auth_dependency(auth_dependency)],
        operation_id="runWowV17NcaafPropHistoryMaintenance",
    )
    def run_maintenance() -> dict[str, Any]:
        return run_ncaaf_prop_history_maintenance(db_client_fn())


__all__ = [
    "AUTOMATIC_CERTIFICATION",
    "AUTOMATIC_PROMOTION",
    "CAN_EXECUTE",
    "PHASE1_STAT_TYPES",
    "PROBABILITY_PUBLISHABLE",
    "default_seasons",
    "install_ncaaf_prop_history_maintenance_route",
    "run_ncaaf_prop_history_maintenance",
]
