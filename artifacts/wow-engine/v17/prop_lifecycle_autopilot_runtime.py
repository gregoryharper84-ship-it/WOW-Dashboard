"""Artifact-isolated runtime wrapper for the continuous V17 prop lifecycle.

The base autopilot owns stage ordering. This wrapper binds its forward-evidence
artifact cohorts to calibration/certification artifact rows so the operational
health surface reports independent predicted / settled / eligible N for the
*same immutable model artifact* instead of only route-level aggregate counts.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable, Mapping

from fastapi import FastAPI

from v17.prop_lifecycle_autopilot import (
    PropLifecycleAutopilotRequest,
    _int_env,
    _readiness_from_forward,
    _route_token,
    persist_lifecycle_health,
    read_latest_lifecycle_health,
    run_prop_lifecycle_autopilot as _run_base,
)

CAN_EXECUTE = False
_STATE_KEY = "wow_prop_lifecycle_autopilot_runtime_installed"
_LOGGER = logging.getLogger("wow.v17.prop_lifecycle_autopilot_runtime")


def _forward_route_rows(forward: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for row in (forward or {}).get("route_results") or []:
        if isinstance(row, Mapping):
            output[_route_token(row.get("sport"), row.get("stat_type"))] = dict(row)
    return output


def _artifact_identity(row: Mapping[str, Any], *, forward: bool = False) -> tuple[str, str, str, str, str]:
    checksum_key = "model_artifact_checksum" if forward else "artifact_checksum"
    calibrator_key = "calibration_version" if forward else "calibrator_version"
    return (
        str(row.get("feature_schema_version") or ""),
        str(row.get("model_family") or ""),
        str(row.get("model_artifact_version") or ""),
        str(row.get(checksum_key) or ""),
        str(row.get(calibrator_key) or ""),
    )


def enrich_artifact_cohort_health(result: dict[str, Any]) -> dict[str, Any]:
    """Join exact forward counts into exact certification artifact rows."""
    forward_stage = result.get("forward_capture") or {}
    forward = forward_stage.get("result") if isinstance(forward_stage, Mapping) else None
    route_forward = _forward_route_rows(forward if isinstance(forward, Mapping) else None)
    dashboard = result.get("dashboard") or {}
    artifacts = dashboard.get("artifact_cohort_rows") or []

    exact_cohorts: dict[tuple[str, tuple[str, str, str, str, str]], dict[str, Any]] = {}
    for token, route_row in route_forward.items():
        readiness = _readiness_from_forward(route_row)
        for cohort in readiness.get("artifact_cohorts") or []:
            if not isinstance(cohort, Mapping):
                continue
            exact_cohorts[(token, _artifact_identity(cohort, forward=True))] = dict(cohort)

    matched = 0
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        token = _route_token(artifact.get("sport"), artifact.get("stat_type"))
        cohort = exact_cohorts.get((token, _artifact_identity(artifact)))
        if cohort is None:
            continue
        matched += 1
        artifact["forward_prediction_n"] = int(cohort.get("forward_prediction_n") or 0)
        artifact["forward_settled_n"] = int(cohort.get("forward_settled_n") or 0)
        artifact["forward_unsettled_n"] = int(cohort.get("forward_unsettled_n") or 0)
        artifact["forward_counting_basis"] = cohort.get("counting_basis")
        artifact["eligible_n"] = int(artifact.get("eligible_n") or 0)
        artifact["cohort_identity_match"] = True

    for artifact in artifacts:
        if isinstance(artifact, dict) and "cohort_identity_match" not in artifact:
            artifact["cohort_identity_match"] = False
            artifact["forward_unsettled_n"] = None
            artifact["forward_counting_basis"] = "EXACT_ARTIFACT_COHORT_NOT_YET_OBSERVED"

    kpi = dashboard.setdefault("kpi", {})
    kpi["artifact_cohorts_with_forward_identity_match_n"] = matched
    kpi["artifact_forward_prediction_n"] = sum(
        int(row.get("forward_prediction_n") or 0) for row in artifacts if isinstance(row, Mapping)
    )
    kpi["artifact_forward_settled_n"] = sum(
        int(row.get("forward_settled_n") or 0) for row in artifacts if isinstance(row, Mapping)
    )
    kpi["artifact_eligible_n"] = sum(
        int(row.get("eligible_n") or 0) for row in artifacts if isinstance(row, Mapping)
    )
    kpi["promotion_package_ready_n"] = sum(
        1 for row in artifacts if isinstance(row, Mapping) and row.get("promotion_package_ready") is True
    )
    result["dashboard"] = dashboard
    return result


def run_prop_lifecycle_autopilot(
    req: PropLifecycleAutopilotRequest,
    *,
    db: Any,
    market_api: Any,
) -> dict[str, Any]:
    base_req = req.model_copy(update={"persist_health": False})
    result = _run_base(base_req, db=db, market_api=market_api)
    enrich_artifact_cohort_health(result)
    if req.persist_health:
        result["health_persistence"] = persist_lifecycle_health(
            db,
            cycle_id=str(result["cycle_id"]),
            dashboard=result["dashboard"],
        )
    else:
        result["health_persistence"] = {
            "status": "DISABLED_FOR_REQUEST",
            "persisted_n": 0,
            "can_execute": False,
        }
    result["artifact_isolation_enforced"] = True
    result["automatic_certification"] = False
    result["automatic_promotion"] = False
    result["can_execute"] = False
    return result


async def run_prop_lifecycle_autopilot_loop(
    *,
    db_client_fn: Callable[[], Any],
    market_api: Any,
    logger: logging.Logger = _LOGGER,
    interval_seconds: int = 900,
    max_snapshots_per_route: int = 50,
    settlement_limit: int = 500,
    initial_delay_seconds: int = 45,
) -> None:
    interval_seconds = max(300, int(interval_seconds))
    if initial_delay_seconds:
        await asyncio.sleep(max(0, int(initial_delay_seconds)))
    while True:
        try:
            result = await asyncio.to_thread(
                run_prop_lifecycle_autopilot,
                PropLifecycleAutopilotRequest(
                    max_snapshots_per_route=max_snapshots_per_route,
                    settlement_limit=settlement_limit,
                    persist_health=True,
                ),
                db=db_client_fn(),
                market_api=market_api,
            )
            kpi = result.get("dashboard", {}).get("kpi", {})
            logger.warning(
                "WOW_PROP_LIFECYCLE_AUTOPILOT status=%s cycle_id=%s routes=%s artifacts=%s forward_n=%s settled_n=%s eligible_n=%s production_registered=%s improper_promotions=%s can_execute=false",
                result.get("run_status"),
                result.get("cycle_id"),
                result.get("routes_requested"),
                kpi.get("artifact_cohort_n"),
                kpi.get("artifact_forward_prediction_n"),
                kpi.get("artifact_forward_settled_n"),
                kpi.get("artifact_eligible_n"),
                kpi.get("production_registered_n"),
                kpi.get("improperly_promoted_route_n"),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "WOW_PROP_LIFECYCLE_AUTOPILOT status=FAILED error_type=%s automatic_certification=false automatic_promotion=false can_execute=false",
                type(exc).__name__,
            )
        await asyncio.sleep(interval_seconds)


def install_prop_lifecycle_autopilot(
    app: FastAPI,
    *,
    auth_dependency: Any,
    db_client_fn: Callable[[], Any],
    market_api: Any,
) -> None:
    if not any(getattr(route, "path", None) == "/v17/prop-lifecycle-autopilot-run" for route in app.router.routes):
        @app.post(
            "/v17/prop-lifecycle-autopilot-run",
            dependencies=[auth_dependency],
            operation_id="runWowV17PropLifecycleAutopilot",
        )
        def prop_lifecycle_autopilot_run(req: PropLifecycleAutopilotRequest):
            return run_prop_lifecycle_autopilot(req, db=db_client_fn(), market_api=market_api)

    if not any(getattr(route, "path", None) == "/v17/prop-lifecycle-health" for route in app.router.routes):
        @app.get(
            "/v17/prop-lifecycle-health",
            dependencies=[auth_dependency],
            operation_id="readWowV17PropLifecycleHealth",
        )
        def prop_lifecycle_health():
            return read_latest_lifecycle_health(db_client_fn())

    if os.getenv("WOW_PROP_LIFECYCLE_AUTOPILOT_ENABLED", "0") != "1":
        return
    if getattr(app.state, _STATE_KEY, False):
        return
    setattr(app.state, _STATE_KEY, True)

    interval_seconds = _int_env(
        "WOW_PROP_LIFECYCLE_AUTOPILOT_INTERVAL_SECONDS", 900, minimum=300, maximum=86400
    )
    max_snapshots = _int_env(
        "WOW_PROP_LIFECYCLE_AUTOPILOT_MAX_SNAPSHOTS_PER_ROUTE", 50, minimum=1, maximum=100
    )
    settlement_limit = _int_env(
        "WOW_PROP_LIFECYCLE_AUTOPILOT_SETTLEMENT_LIMIT", 500, minimum=1, maximum=1000
    )
    initial_delay = _int_env(
        "WOW_PROP_LIFECYCLE_AUTOPILOT_INITIAL_DELAY_SECONDS", 45, minimum=0, maximum=600
    )

    @app.on_event("startup")
    async def schedule_prop_lifecycle_autopilot() -> None:
        task = asyncio.create_task(
            run_prop_lifecycle_autopilot_loop(
                db_client_fn=db_client_fn,
                market_api=market_api,
                logger=_LOGGER,
                interval_seconds=interval_seconds,
                max_snapshots_per_route=max_snapshots,
                settlement_limit=settlement_limit,
                initial_delay_seconds=initial_delay,
            )
        )
        tasks = getattr(app.state, "wow_prop_lifecycle_autopilot_tasks", None)
        if tasks is None:
            tasks = set()
            app.state.wow_prop_lifecycle_autopilot_tasks = tasks
        tasks.add(task)
        task.add_done_callback(tasks.discard)


__all__ = [
    "CAN_EXECUTE",
    "enrich_artifact_cohort_health",
    "install_prop_lifecycle_autopilot",
    "run_prop_lifecycle_autopilot",
    "run_prop_lifecycle_autopilot_loop",
]
