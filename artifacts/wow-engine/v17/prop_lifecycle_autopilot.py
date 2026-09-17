"""Continuous V17 prop evidence, settlement, certification-readiness and lifecycle health.

This control-plane loop intentionally stops short of autonomous certification or
promotion.  It continuously advances every *declared exact prop route* through
all engineering work that can be performed without weakening V17 governance:

    immutable forward evidence -> exact official settlement
    -> artifact-isolated calibration/certification audit
    -> production-registration audit -> lifecycle health snapshot

A route may become ``PRODUCTION_REGISTERED`` only when the existing governed
registration audit proves the same immutable artifact has an independently
reviewed certification release, exact registry promotion, runtime model and
calibrator adapters, exact hydration, and a real canonical Action canary.

Failures are stage-isolated: one source/route failure never terminates the
continuous loop and never upgrades another route. ``can_execute`` is false
unconditionally.
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

from v17.prop_capability_manifest import DECLARED_PROP_LANES
from v17.prop_certification_runtime import (
    PropCertificationAuditRequest,
    run_prop_certification_audit,
)
from v17.prop_exact_route_settlement import (
    ExactRouteSettlementRequest,
    build_settlement_inventory,
    run_exact_route_settlement,
)
from v17.prop_production_registration import (
    PropProductionRegistrationAuditRequest,
    run_prop_production_registration_audit,
)
from v17.prop_route_lifecycle import PRODUCTION_REGISTERED
from v17.prop_universal_forward_evidence import (
    UniversalPropForwardEvidenceRequest,
    build_forward_evidence_inventory,
    run_universal_prop_forward_evidence,
)

CAN_EXECUTE = False
HEALTH_TABLE = "wow_prop_lifecycle_health_snapshots"
AUTOPILOT_VERSION = "V17_PROP_LIFECYCLE_AUTOPILOT_V1"
_STATE_KEY = "wow_prop_lifecycle_autopilot_installed"
_LOGGER = logging.getLogger("wow.v17.prop_lifecycle_autopilot")


class PropLifecycleAutopilotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    routes: list[str] = Field(default_factory=list)
    max_snapshots_per_route: int = Field(default=50, ge=1, le=100)
    settlement_limit: int = Field(default=500, ge=1, le=1000)
    persist_health: bool = True


def _route_token(sport: Any, stat_type: Any) -> str:
    return f"{str(sport or '').strip().upper()}:{str(stat_type or '').strip().upper()}"


def _requested_tokens(values: list[str]) -> list[str]:
    if not values:
        return [_route_token(*key) for key in sorted(DECLARED_PROP_LANES)]
    output: list[str] = []
    declared = {_route_token(*key) for key in DECLARED_PROP_LANES}
    for raw in values:
        token = str(raw or "").strip().upper()
        if token not in declared:
            raise ValueError(f"undeclared prop route: {raw!r}")
        if token not in output:
            output.append(token)
    return output


def _safe_stage(name: str, call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        result = call()
        return {
            "stage": name,
            "status": "PASS",
            "result": result,
            "can_execute": False,
        }
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return {
            "stage": name,
            "status": "FAILED",
            "error_type": type(exc).__name__,
            "result": None,
            "can_execute": False,
        }


def _forward_result_map(forward: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for row in (forward or {}).get("route_results") or []:
        if not isinstance(row, Mapping):
            continue
        output[_route_token(row.get("sport"), row.get("stat_type"))] = dict(row)
    return output


def _settlement_disposition_map(settlement: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for row in (settlement or {}).get("route_dispositions") or []:
        if not isinstance(row, Mapping):
            continue
        output[_route_token(row.get("sport"), row.get("stat_type"))] = dict(row)
    return output


def _settlement_cycle_counts(settlement: Mapping[str, Any] | None) -> dict[str, dict[str, int]]:
    output: dict[str, dict[str, int]] = {}
    for row in (settlement or {}).get("results") or []:
        if not isinstance(row, Mapping):
            continue
        token = str(row.get("route") or "").strip().upper()
        if not token:
            continue
        counts = output.setdefault(token, {"considered": 0, "settled": 0, "held": 0})
        counts["considered"] += 1
        if str(row.get("status") or "").startswith("SETTLED"):
            counts["settled"] += 1
        else:
            counts["held"] += 1
    return output


def _registration_map(registration: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for row in (registration or {}).get("route_rows") or []:
        if not isinstance(row, Mapping):
            continue
        output[_route_token(row.get("sport"), row.get("stat_type"))] = dict(row)
    return output


def _certification_rows(certification: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    return [dict(row) for row in (certification or {}).get("artifact_rows") or [] if isinstance(row, Mapping)]


def _readiness_from_forward(row: Mapping[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    direct = row.get("calibration_readiness")
    if isinstance(direct, Mapping):
        return dict(direct)
    nested = row.get("result")
    if isinstance(nested, Mapping) and isinstance(nested.get("calibration_readiness"), Mapping):
        return dict(nested["calibration_readiness"])
    return {}


def _metrics(row: Mapping[str, Any]) -> dict[str, Any]:
    value = row.get("metrics")
    return dict(value) if isinstance(value, Mapping) else {}


def _identity_match(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return all(
        str(left.get(key) or "") == str(right.get(key) or "")
        for key in ("model_artifact_version", "artifact_checksum")
    )


def _unique_blockers(*groups: Any) -> list[str]:
    output: list[str] = []
    for group in groups:
        if isinstance(group, str):
            values = [group]
        elif isinstance(group, (list, tuple, set)):
            values = list(group)
        else:
            values = []
        for value in values:
            text = str(value or "").strip()
            if text and text not in output:
                output.append(text)
    return output


def build_lifecycle_dashboard(
    *,
    forward: Mapping[str, Any] | None,
    settlement: Mapping[str, Any] | None,
    certification: Mapping[str, Any] | None,
    registration: Mapping[str, Any] | None,
    requested_tokens: list[str],
    captured_at: str,
) -> dict[str, Any]:
    """Build route and exact-artifact health without manufacturing maturity."""
    fwd = _forward_result_map(forward)
    settle_disp = _settlement_disposition_map(settlement)
    settle_counts = _settlement_cycle_counts(settlement)
    reg = _registration_map(registration)
    cert_rows = _certification_rows(certification)

    cert_by_route: dict[str, list[dict[str, Any]]] = {}
    for row in cert_rows:
        cert_by_route.setdefault(_route_token(row.get("sport"), row.get("stat_type")), []).append(row)

    route_rows: list[dict[str, Any]] = []
    artifact_rows: list[dict[str, Any]] = []

    for token in requested_tokens:
        sport, stat_type = token.split(":", 1)
        fwd_row = fwd.get(token, {})
        settlement_row = settle_disp.get(token, {})
        registration_row = reg.get(token, {})
        readiness = _readiness_from_forward(fwd_row)
        route_cert_rows = cert_by_route.get(token, [])
        cycle_counts = settle_counts.get(token, {"considered": 0, "settled": 0, "held": 0})

        route_row = {
            "row_kind": "ROUTE_STATUS",
            "sport": sport,
            "stat_type": stat_type,
            "model_family": registration_row.get("model_family"),
            "model_artifact_version": registration_row.get("model_artifact_version"),
            "artifact_checksum": registration_row.get("artifact_checksum"),
            "calibrator_version": registration_row.get("calibrator_version"),
            "forward_collector": fwd_row.get("collector"),
            "forward_status": fwd_row.get("status") or "NOT_OBSERVED_THIS_CYCLE",
            "forward_prediction_n": readiness.get("forward_prediction_n"),
            "forward_settled_n": readiness.get("forward_settled_n"),
            "settlement_adapter_status": settlement_row.get("status") or "NOT_DECLARED",
            "settlement_source": settlement_row.get("official_source"),
            "settlement_cycle_considered_n": cycle_counts["considered"],
            "settlement_cycle_settled_n": cycle_counts["settled"],
            "settlement_cycle_held_n": cycle_counts["held"],
            "certification_artifact_n": len(route_cert_rows),
            "lifecycle_status": registration_row.get("status") or "MODEL_BUILD_REQUIRED",
            "production_numerical_authority": bool(registration_row.get("production_numerical_authority")),
            "model_adapter_registered": registration_row.get("model_adapter_registered"),
            "calibrator_adapter_registered": registration_row.get("calibrator_adapter_registered"),
            "hydration_route_registered": registration_row.get("hydration_route_registered"),
            "action_canary_verified": bool(registration_row.get("action_canary_verified")),
            "blockers": _unique_blockers(
                fwd_row.get("blockers"),
                [settlement_row.get("blocker")],
                registration_row.get("blockers"),
                registration_row.get("action_canary_blockers"),
            ),
            "captured_at": captured_at,
            "can_execute": False,
        }
        route_rows.append(route_row)

        for cert in route_cert_rows:
            metrics = _metrics(cert)
            registration_matches = bool(registration_row and _identity_match(cert, registration_row))
            artifact_rows.append({
                "row_kind": "ARTIFACT_COHORT",
                "sport": sport,
                "stat_type": stat_type,
                "model_family": cert.get("model_family"),
                "model_artifact_version": cert.get("model_artifact_version"),
                "artifact_checksum": cert.get("artifact_checksum"),
                "calibrator_version": cert.get("calibrator_version"),
                "forward_prediction_n": cert.get("forward_prediction_n"),
                "forward_settled_n": cert.get("forward_settled_n") or cert.get("independent_settled_thesis_n"),
                "eligible_n": cert.get("forward_eligible_n") or metrics.get("n"),
                "duplicate_or_twin_row_n": cert.get("duplicate_or_twin_row_n"),
                "excluded_invalid_row_n": cert.get("excluded_invalid_row_n"),
                "brier_score": metrics.get("brier_score"),
                "log_loss": metrics.get("log_loss"),
                "expected_calibration_error": metrics.get("expected_calibration_error"),
                "calibration_bias": metrics.get("calibration_bias"),
                "lower_bound_reliability_margin": metrics.get("lower_bound_reliability_margin"),
                "calibration_status": cert.get("status"),
                "policy_id": cert.get("policy_id"),
                "policy_review_status": cert.get("policy_review_status"),
                "evidence_hash": cert.get("evidence_hash"),
                "promotion_package_ready": bool(cert.get("promotion_package_ready")),
                "registry_lifecycle_state": cert.get("artifact_registry_lifecycle_state"),
                "registry_promoted": bool(cert.get("artifact_registry_promoted")),
                "registry_active": bool(cert.get("artifact_registry_active")),
                "selected_for_registration_audit": registration_matches,
                "lifecycle_status": registration_row.get("status") if registration_matches else "NON_SELECTED_ARTIFACT_COHORT",
                "model_adapter_registered": registration_row.get("model_adapter_registered") if registration_matches else None,
                "calibrator_adapter_registered": registration_row.get("calibrator_adapter_registered") if registration_matches else None,
                "hydration_route_registered": registration_row.get("hydration_route_registered") if registration_matches else None,
                "action_canary_verified": bool(registration_row.get("action_canary_verified")) if registration_matches else False,
                "blockers": _unique_blockers(
                    cert.get("blockers"),
                    registration_row.get("blockers") if registration_matches else [],
                    registration_row.get("action_canary_blockers") if registration_matches else [],
                ),
                "captured_at": captured_at,
                "can_execute": False,
            })

    # Required all-sports completeness: sports with no declared prop category remain
    # visible as typed placeholders rather than disappearing from the dashboard.
    requested_sports = {token.split(":", 1)[0] for token in requested_tokens}
    for row in build_forward_evidence_inventory():
        sport = str(row.get("sport") or "").upper()
        stat_type = str(row.get("stat_type") or "")
        if stat_type != "__SPORT_PROP_CATEGORY_INVENTORY__" or sport in requested_sports:
            continue
        settlement_inventory = next(
            (
                item for item in build_settlement_inventory()
                if item.get("sport") == sport and item.get("stat_type") == stat_type
            ),
            {},
        )
        route_rows.append({
            "row_kind": "ROUTE_STATUS",
            "sport": sport,
            "stat_type": stat_type,
            "model_family": None,
            "model_artifact_version": None,
            "artifact_checksum": None,
            "calibrator_version": None,
            "forward_collector": row.get("collector"),
            "forward_status": row.get("status"),
            "forward_prediction_n": 0,
            "forward_settled_n": 0,
            "settlement_adapter_status": settlement_inventory.get("status") or "NO_CURRENT_PROP_CATEGORY_DECLARED",
            "settlement_source": None,
            "settlement_cycle_considered_n": 0,
            "settlement_cycle_settled_n": 0,
            "settlement_cycle_held_n": 0,
            "certification_artifact_n": 0,
            "lifecycle_status": "NO_CURRENT_PROP_CATEGORY_DECLARED",
            "production_numerical_authority": False,
            "model_adapter_registered": False,
            "calibrator_adapter_registered": False,
            "hydration_route_registered": False,
            "action_canary_verified": False,
            "blockers": _unique_blockers([row.get("blocker"), settlement_inventory.get("blocker")]),
            "captured_at": captured_at,
            "can_execute": False,
        })

    route_rows.sort(key=lambda row: (row["sport"], row["stat_type"]))
    artifact_rows.sort(
        key=lambda row: (
            row["sport"], row["stat_type"],
            str(row.get("model_artifact_version") or ""),
            str(row.get("artifact_checksum") or ""),
        )
    )
    status_counts = Counter(str(row.get("lifecycle_status") or "UNKNOWN") for row in route_rows)
    production_rows = [row for row in route_rows if row.get("lifecycle_status") == PRODUCTION_REGISTERED]
    governance_violations = [
        row for row in production_rows
        if not (
            row.get("production_numerical_authority") is True
            and row.get("action_canary_verified") is True
            and row.get("model_adapter_registered") is True
            and row.get("calibrator_adapter_registered") is True
            and row.get("hydration_route_registered") is True
        )
    ]
    return {
        "dashboard_version": AUTOPILOT_VERSION,
        "captured_at": captured_at,
        "route_rows": route_rows,
        "artifact_cohort_rows": artifact_rows,
        "kpi": {
            "production_registered_n": len(production_rows),
            "lifecycle_status_counts": dict(sorted(status_counts.items())),
            "artifact_cohort_n": len(artifact_rows),
            "improperly_promoted_route_n": len(governance_violations),
            "target_improperly_promoted_route_n": 0,
        },
        "can_execute": False,
    }


def _health_key(row: Mapping[str, Any]) -> str:
    kind = str(row.get("row_kind") or "ROUTE_STATUS")
    token = _route_token(row.get("sport"), row.get("stat_type"))
    artifact = str(row.get("model_artifact_version") or "NO_ARTIFACT")
    checksum = str(row.get("artifact_checksum") or "NO_CHECKSUM")
    return f"{kind}|{token}|{artifact}|{checksum}"


def _persistence_rows(cycle_id: str, dashboard: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = list(dashboard.get("route_rows") or []) + list(dashboard.get("artifact_cohort_rows") or [])
    output: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        output.append({
            "cycle_id": cycle_id,
            "captured_at": dashboard["captured_at"],
            "health_key": _health_key(row),
            "row_kind": row.get("row_kind"),
            "sport": row.get("sport"),
            "stat_type": row.get("stat_type"),
            "model_family": row.get("model_family"),
            "model_artifact_version": row.get("model_artifact_version"),
            "artifact_checksum": row.get("artifact_checksum"),
            "calibrator_version": row.get("calibrator_version"),
            "forward_prediction_n": row.get("forward_prediction_n"),
            "forward_settled_n": row.get("forward_settled_n"),
            "eligible_n": row.get("eligible_n"),
            "calibration_status": row.get("calibration_status"),
            "policy_review_status": row.get("policy_review_status"),
            "lifecycle_status": row.get("lifecycle_status"),
            "action_canary_verified": bool(row.get("action_canary_verified")),
            "blockers": row.get("blockers") or [],
            "payload": row,
            "can_execute": False,
        })
    return output


def persist_lifecycle_health(db: Any, *, cycle_id: str, dashboard: Mapping[str, Any]) -> dict[str, Any]:
    rows = _persistence_rows(cycle_id, dashboard)
    if not rows:
        return {"status": "NO_ROWS", "persisted_n": 0, "can_execute": False}
    try:
        result = db.table(HEALTH_TABLE).upsert(
            rows,
            on_conflict="cycle_id,health_key",
        ).execute()
        written = len(getattr(result, "data", None) or rows)
        return {"status": "PASS", "persisted_n": written, "can_execute": False}
    except Exception as exc:
        return {
            "status": "HEALTH_PERSISTENCE_UNAVAILABLE",
            "persisted_n": 0,
            "error_type": type(exc).__name__,
            "can_execute": False,
        }


def read_latest_lifecycle_health(db: Any) -> dict[str, Any]:
    try:
        newest = (
            db.table(HEALTH_TABLE)
            .select("cycle_id,captured_at")
            .order("captured_at", desc=True)
            .limit(1)
            .execute().data or []
        )
        if not newest:
            return {"status": "NO_HEALTH_SNAPSHOT", "rows": [], "can_execute": False}
        cycle_id = str(newest[0]["cycle_id"])
        rows = (
            db.table(HEALTH_TABLE)
            .select("*")
            .eq("cycle_id", cycle_id)
            .order("sport")
            .order("stat_type")
            .execute().data or []
        )
        return {
            "status": "PASS",
            "cycle_id": cycle_id,
            "captured_at": newest[0].get("captured_at"),
            "rows": rows,
            "can_execute": False,
        }
    except Exception as exc:
        return {
            "status": "HEALTH_READ_UNAVAILABLE",
            "error_type": type(exc).__name__,
            "rows": [],
            "can_execute": False,
        }


def run_prop_lifecycle_autopilot(
    req: PropLifecycleAutopilotRequest,
    *,
    db: Any,
    market_api: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    captured_at = now.isoformat()
    cycle_id = str(uuid4())
    tokens = _requested_tokens(req.routes)

    forward_stage = _safe_stage(
        "UNIVERSAL_FORWARD_CAPTURE",
        lambda: run_universal_prop_forward_evidence(
            UniversalPropForwardEvidenceRequest(
                routes=tokens,
                max_snapshots_per_route=req.max_snapshots_per_route,
            ),
            db=db,
            market_api=market_api,
            now=now,
        ),
    )
    settlement_stage = _safe_stage(
        "UNIVERSAL_EXACT_SETTLEMENT",
        lambda: run_exact_route_settlement(
            ExactRouteSettlementRequest(routes=tokens, limit=req.settlement_limit),
            db=db,
            now=now,
        ),
    )
    certification_stage = _safe_stage(
        "CALIBRATION_CERTIFICATION_READINESS",
        lambda: run_prop_certification_audit(
            PropCertificationAuditRequest(routes=tokens, include_inactive=True),
            db=db,
        ),
    )
    registration_stage = _safe_stage(
        "PRODUCTION_REGISTRATION_AUDIT",
        lambda: run_prop_production_registration_audit(
            PropProductionRegistrationAuditRequest(routes=tokens),
            db=db,
        ),
    )

    dashboard = build_lifecycle_dashboard(
        forward=forward_stage.get("result"),
        settlement=settlement_stage.get("result"),
        certification=certification_stage.get("result"),
        registration=registration_stage.get("result"),
        requested_tokens=tokens,
        captured_at=captured_at,
    )
    persistence = (
        persist_lifecycle_health(db, cycle_id=cycle_id, dashboard=dashboard)
        if req.persist_health
        else {"status": "DISABLED_FOR_REQUEST", "persisted_n": 0, "can_execute": False}
    )
    stage_statuses = {
        stage["stage"]: stage["status"]
        for stage in (forward_stage, settlement_stage, certification_stage, registration_stage)
    }
    failed_stages = [name for name, status in stage_statuses.items() if status != "PASS"]
    return {
        "terminal": True,
        "autopilot_version": AUTOPILOT_VERSION,
        "cycle_id": cycle_id,
        "captured_at": captured_at,
        "run_status": "COMPLETED" if not failed_stages else "COMPLETED_WITH_STAGE_BLOCKERS",
        "routes_requested": len(tokens),
        "stage_statuses": stage_statuses,
        "failed_stages": failed_stages,
        "forward_capture": forward_stage,
        "settlement": settlement_stage,
        "certification_readiness": certification_stage,
        "production_registration": registration_stage,
        "dashboard": dashboard,
        "health_persistence": persistence,
        "automatic_certification": False,
        "automatic_promotion": False,
        "can_execute": False,
    }


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


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
    """Run forever; a failed cycle is logged and the next cycle still runs."""
    interval_seconds = max(300, int(interval_seconds))
    max_snapshots_per_route = max(1, min(int(max_snapshots_per_route), 100))
    settlement_limit = max(1, min(int(settlement_limit), 1000))
    initial_delay_seconds = max(0, int(initial_delay_seconds))
    if initial_delay_seconds:
        await asyncio.sleep(initial_delay_seconds)

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
                "WOW_PROP_LIFECYCLE_AUTOPILOT status=%s cycle_id=%s routes=%s production_registered=%s improper_promotions=%s failed_stages=%s can_execute=false",
                result.get("run_status"),
                result.get("cycle_id"),
                result.get("routes_requested"),
                kpi.get("production_registered_n"),
                kpi.get("improperly_promoted_route_n"),
                len(result.get("failed_stages") or []),
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
    """Install authenticated manual health endpoints and the optional continuous loop."""
    if not any(getattr(route, "path", None) == "/v17/prop-lifecycle-autopilot-run" for route in app.router.routes):
        @app.post(
            "/v17/prop-lifecycle-autopilot-run",
            dependencies=[auth_dependency],
            operation_id="runWowV17PropLifecycleAutopilot",
        )
        def prop_lifecycle_autopilot_run(req: PropLifecycleAutopilotRequest):
            return run_prop_lifecycle_autopilot(
                req,
                db=db_client_fn(),
                market_api=market_api,
            )

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
    "AUTOPILOT_VERSION",
    "CAN_EXECUTE",
    "HEALTH_TABLE",
    "PropLifecycleAutopilotRequest",
    "build_lifecycle_dashboard",
    "install_prop_lifecycle_autopilot",
    "persist_lifecycle_health",
    "read_latest_lifecycle_health",
    "run_prop_lifecycle_autopilot",
    "run_prop_lifecycle_autopilot_loop",
]
