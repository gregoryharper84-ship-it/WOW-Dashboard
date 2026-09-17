"""Artifact-isolated runtime wrapper for the continuous V17 prop lifecycle.

The base autopilot owns forward capture, exact settlement, certification audit,
registration audit and health persistence. This wrapper adds two universal
engineering stages without weakening governance:

* exact-artifact Forward N / Settled N / Eligible N joins; and
* candidate-only Platt/isotonic calibration evidence with untouched chronological
  holdout metrics and deterministic evidence hashes.

Candidate calibration evidence never certifies, promotes, registers or publishes
an artifact. Independent certification remains a separate reviewed boundary.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable, Mapping

from fastapi import FastAPI

from github_actions_oidc import scout_route_auth_dependency
from v17.prop_calibrator_candidate_runtime import (
    PropCalibratorCandidateRequest,
    run_prop_calibrator_candidate_audit,
)
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


def _model_artifact_identity(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("feature_schema_version") or ""),
        str(row.get("model_family") or ""),
        str(row.get("model_artifact_version") or ""),
        str(row.get("artifact_checksum") or row.get("model_artifact_checksum") or ""),
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


def enrich_calibrator_candidate_health(
    result: dict[str, Any],
    candidate_audit: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Attach candidate-only untouched-holdout evidence to exact model artifacts."""
    packets = [
        dict(row) for row in (candidate_audit or {}).get("artifact_packets") or []
        if isinstance(row, Mapping)
    ]
    by_identity = {
        (_route_token(row.get("sport"), row.get("stat_type")), _model_artifact_identity(row)): row
        for row in packets
    }
    dashboard = result.get("dashboard") or {}
    artifacts = dashboard.get("artifact_cohort_rows") or []
    matched = 0
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        key = (
            _route_token(artifact.get("sport"), artifact.get("stat_type")),
            _model_artifact_identity(artifact),
        )
        packet = by_identity.get(key)
        if packet is None:
            artifact["calibrator_candidate_status"] = "NO_EXACT_MODEL_ARTIFACT_CANDIDATE_PACKET"
            artifact["calibrator_candidate_review_ready"] = False
            continue
        matched += 1
        artifact.update({
            "calibrator_candidate_status": packet.get("status"),
            "calibrator_candidate_method": packet.get("selected_method"),
            "calibrator_candidate_evidence_hash": packet.get("evidence_hash"),
            "calibrator_candidate_training_n": packet.get("training_n"),
            "calibrator_candidate_holdout_n": packet.get("holdout_n"),
            "calibrator_candidate_raw_holdout_metrics": packet.get("raw_holdout_metrics"),
            "calibrator_candidate_holdout_metrics": packet.get("calibrated_holdout_metrics"),
            "calibrator_candidate_oof_metrics": packet.get("fit_oof_metrics"),
            "calibrator_candidate_review_ready": bool(packet.get("certification_review_packet_ready")),
            "calibrator_candidate_blockers": packet.get("blockers") or [],
        })

    kpi = dashboard.setdefault("kpi", {})
    kpi["calibrator_candidate_artifact_n"] = len(packets)
    kpi["calibrator_candidate_identity_match_n"] = matched
    kpi["calibrator_review_packet_ready_n"] = sum(
        1 for packet in packets if packet.get("certification_review_packet_ready") is True
    )
    result["dashboard"] = dashboard
    return result


def _run_candidate_stage(req: PropLifecycleAutopilotRequest, *, db: Any) -> dict[str, Any]:
    try:
        audit = run_prop_calibrator_candidate_audit(
            PropCalibratorCandidateRequest(routes=list(req.routes), include_inactive=True),
            db=db,
        )
        return {"stage": "CALIBRATOR_CANDIDATE_EVIDENCE", "status": "PASS", "result": audit, "can_execute": False}
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return {
            "stage": "CALIBRATOR_CANDIDATE_EVIDENCE",
            "status": "FAILED",
            "error_type": type(exc).__name__,
            "result": None,
            "can_execute": False,
        }


def run_prop_lifecycle_autopilot(
    req: PropLifecycleAutopilotRequest,
    *,
    db: Any,
    market_api: Any,
) -> dict[str, Any]:
    base_req = req.model_copy(update={"persist_health": False})
    result = _run_base(base_req, db=db, market_api=market_api)
    candidate_stage = _run_candidate_stage(req, db=db)
    result["calibrator_candidate_evidence"] = candidate_stage
    result.setdefault("stage_statuses", {})[candidate_stage["stage"]] = candidate_stage["status"]
    if candidate_stage["status"] != "PASS":
        failed = result.setdefault("failed_stages", [])
        if candidate_stage["stage"] not in failed:
            failed.append(candidate_stage["stage"])
        result["run_status"] = "COMPLETED_WITH_STAGE_BLOCKERS"

    enrich_artifact_cohort_health(result)
    enrich_calibrator_candidate_health(result, candidate_stage.get("result"))
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
    result["calibrator_candidate_generation_enabled"] = True
    result["untouched_holdout_required"] = True
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
                "WOW_PROP_LIFECYCLE_AUTOPILOT status=%s cycle_id=%s routes=%s artifacts=%s forward_n=%s settled_n=%s eligible_n=%s calibrator_review_ready=%s production_registered=%s improper_promotions=%s can_execute=false",
                result.get("run_status"),
                result.get("cycle_id"),
                result.get("routes_requested"),
                kpi.get("artifact_cohort_n"),
                kpi.get("artifact_forward_prediction_n"),
                kpi.get("artifact_forward_settled_n"),
                kpi.get("artifact_eligible_n"),
                kpi.get("calibrator_review_packet_ready_n"),
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
    # Preserve WOW_ACTION_API_KEY for Custom GPT/manual calls while permitting
    # only the explicitly allowlisted protected-main workflow to use short-lived
    # OIDC for scheduled production wakeups.
    automation_auth = scout_route_auth_dependency(auth_dependency)

    if not any(getattr(route, "path", None) == "/v17/prop-lifecycle-autopilot-run" for route in app.router.routes):
        @app.post(
            "/v17/prop-lifecycle-autopilot-run",
            dependencies=[automation_auth],
            operation_id="runWowV17PropLifecycleAutopilot",
        )
        def prop_lifecycle_autopilot_run(req: PropLifecycleAutopilotRequest):
            return run_prop_lifecycle_autopilot(req, db=db_client_fn(), market_api=market_api)

    if not any(getattr(route, "path", None) == "/v17/prop-lifecycle-health" for route in app.router.routes):
        @app.get(
            "/v17/prop-lifecycle-health",
            dependencies=[automation_auth],
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
    "enrich_calibrator_candidate_health",
    "install_prop_lifecycle_autopilot",
    "run_prop_lifecycle_autopilot",
    "run_prop_lifecycle_autopilot_loop",
]
