"""Bounded priority lifecycle endpoints for NFL/MLB/WNBA prop closure.

The scheduled priority workflow already owns sport-specific hydration/evidence
acquisition. Re-running the universal network-backed forward collector inside the
same synchronous HTTP request duplicated work and could exceed the production
proxy deadline before lifecycle health was persisted.

This module separates the remaining closure work into:
* exact-route settlement; and
* a durable DB-first lifecycle audit over already persisted predictions/outcomes.

Neither endpoint fits, certifies, promotes, publishes, ranks, prices or executes
sporting probabilities. ``can_execute`` is false unconditionally.
"""
from __future__ import annotations

from collections import defaultdict
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

from github_actions_oidc import scout_route_auth_dependency
import v17.prop_exact_route_settlement as settlement_runtime
import v17.prop_lifecycle_autopilot as lifecycle_base
from v17.prop_calibrator_candidate_runtime import (
    PropCalibratorCandidateRequest,
    run_prop_calibrator_candidate_audit,
)
from v17.prop_certification_runtime import (
    PropCertificationAuditRequest,
    run_prop_certification_audit,
)
from v17.prop_lifecycle_autopilot_runtime import (
    enrich_artifact_cohort_health,
    enrich_calibrator_candidate_health,
)
from v17.prop_operator_summary import summarize_prop_routes
from v17.prop_slip_lane_runtime import run_slip_lane
from v17.prop_production_registration import (
    PropProductionRegistrationAuditRequest,
    run_prop_production_registration_audit,
)
from v17.prop_universal_forward_evidence import (
    COLLECTOR_GENERIC,
    _collector_for,
    build_forward_evidence_inventory,
    route_key,
    run_generic_forward_route,
)

CAN_EXECUTE = False
PROVIDER = "WOW_PROP_FITTED_MODEL_V1"
FORWARD_EVIDENCE_KIND = "IMMUTABLE_PREGAME_SETTLED"
LEGACY_FORWARD_ROUTE = ("MLB", "PITCHER_STRIKEOUTS")
PAGE_SIZE = 1000
OUTCOME_CHUNK_SIZE = 150
COUNTING_BASIS = "UNIQUE_EVENT_PLAYER_STAT_LINE_THESIS"
# Pregame scoring of already persisted snapshots (DB-first, no acquisition).
# Bounded so the hourly audit stays inside the caller's 120 s deadline.
PREGAME_SCORING_MAX_SNAPSHOTS = 8
PREGAME_SCORING_DEADLINE_SECONDS = 45.0


class PriorityPropSettlementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    routes: list[str] = Field(default_factory=list)
    limit: int = Field(default=20, ge=1, le=200)


class PriorityPropDurableAuditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    routes: list[str] = Field(default_factory=list)


def _aware(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _line_key(value: Any) -> str | None:
    try:
        return format(Decimal(str(value)).normalize(), "f")
    except (InvalidOperation, TypeError, ValueError):
        return None


def _thesis_key(row: Mapping[str, Any]) -> tuple[str, str, str, str] | None:
    event_id = str(row.get("event_id") or "").strip()
    player = " ".join(str(row.get("player") or "").split()).casefold()
    stat_type = str(row.get("stat_type") or "").strip().upper()
    line = _line_key(row.get("line"))
    if not event_id or not player or not stat_type or line is None:
        return None
    return event_id, player, stat_type, line


def _artifact_identity(row: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(row.get("feature_schema_version") or ""),
        str(row.get("model_family") or ""),
        str(row.get("model_artifact_version") or ""),
        str(row.get("model_artifact_checksum") or row.get("artifact_checksum") or ""),
        str(row.get("calibration_version") or row.get("calibrator_version") or ""),
    )


def _paginate(build: Callable[[], Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    start = 0
    while True:
        page = build().range(start, start + PAGE_SIZE - 1).execute().data or []
        batch = [dict(row) for row in page]
        output.extend(batch)
        if len(batch) < PAGE_SIZE:
            return output
        start += PAGE_SIZE


def _settled_prediction_ids(db: Any, prediction_ids: list[str]) -> set[str]:
    settled: set[str] = set()
    for offset in range(0, len(prediction_ids), OUTCOME_CHUNK_SIZE):
        chunk = prediction_ids[offset:offset + OUTCOME_CHUNK_SIZE]
        rows = _paginate(
            lambda chunk=chunk: db.table("wow_outcomes")
            .select("prediction_id,actual_stat,settlement_timestamp,void")
            .in_("prediction_id", chunk)
        )
        settled.update(
            str(row["prediction_id"])
            for row in rows
            if row.get("prediction_id")
            and row.get("actual_stat") is not None
            and row.get("settlement_timestamp") is not None
            and row.get("void") is not True
        )
    return settled


def _durable_route_readiness(db: Any, *, sport: str, stat_type: str) -> dict[str, Any]:
    rows = _paginate(
        lambda: db.table("wow_predictions")
        .select(
            "prediction_id,event_id,event_start_time,model_timestamp,locked_at,player,stat_type,line,"
            "source_snapshot_id,evidence_source_kind,feature_schema_version,model_family,"
            "model_artifact_version,model_artifact_checksum,calibration_version"
        )
        .eq("sport", sport)
        .eq("stat_type", stat_type)
        .eq("model_provider_identity", PROVIDER)
    )

    eligible: list[dict[str, Any]] = []
    for row in rows:
        start = _aware(row.get("event_start_time"))
        model_ts = _aware(row.get("model_timestamp"))
        locked = _aware(row.get("locked_at"))
        if not row.get("prediction_id") or not row.get("source_snapshot_id"):
            continue
        if start is None or model_ts is None or locked is None or model_ts >= start or locked >= start:
            continue
        if (sport, stat_type) != LEGACY_FORWARD_ROUTE and row.get("evidence_source_kind") != FORWARD_EVIDENCE_KIND:
            continue
        if _thesis_key(row) is None:
            continue
        eligible.append(row)

    ids = [str(row["prediction_id"]) for row in eligible]
    settled_ids = _settled_prediction_ids(db, ids) if ids else set()
    route_theses = {_thesis_key(row) for row in eligible}
    route_theses.discard(None)
    settled_theses = {
        _thesis_key(row)
        for row in eligible
        if str(row.get("prediction_id")) in settled_ids
    }
    settled_theses.discard(None)

    grouped: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in eligible:
        identity = _artifact_identity(row)
        if not all(identity[:4]):
            continue
        grouped[identity].append(row)

    artifact_cohorts: list[dict[str, Any]] = []
    for identity, group in sorted(grouped.items()):
        theses = {_thesis_key(row) for row in group}
        theses.discard(None)
        settled = {
            _thesis_key(row)
            for row in group
            if str(row.get("prediction_id")) in settled_ids
        }
        settled.discard(None)
        feature_schema, family, version, checksum, calibration = identity
        artifact_cohorts.append({
            "feature_schema_version": feature_schema,
            "model_family": family,
            "model_artifact_version": version,
            "model_artifact_checksum": checksum,
            "calibration_version": calibration,
            "forward_prediction_n": len(theses),
            "forward_settled_n": len(settled),
            "forward_unsettled_n": max(0, len(theses) - len(settled)),
            "counting_basis": COUNTING_BASIS,
            "can_execute": False,
        })

    return {
        "sport": sport,
        "stat_type": stat_type,
        "forward_prediction_n": len(route_theses),
        "forward_settled_n": len(settled_theses),
        "forward_unsettled_n": max(0, len(route_theses) - len(settled_theses)),
        "artifact_cohorts": artifact_cohorts,
        "counting_basis": COUNTING_BASIS,
        "calibration_or_certification_performed": False,
        "can_execute": False,
    }


def _durable_forward_result(db: Any, tokens: list[str]) -> dict[str, Any]:
    inventory = {
        lifecycle_base._route_token(row.get("sport"), row.get("stat_type")): dict(row)
        for row in build_forward_evidence_inventory()
        if isinstance(row, Mapping)
    }
    route_results: list[dict[str, Any]] = []
    for token in tokens:
        sport, stat_type = token.split(":", 1)
        item = inventory.get(token, {})
        route_results.append({
            "sport": sport,
            "stat_type": stat_type,
            "collector": item.get("collector"),
            "status": item.get("status") or "DURABLE_EVIDENCE_AUDIT",
            "blockers": [item.get("blocker")] if item.get("blocker") else [],
            "calibration_readiness": _durable_route_readiness(db, sport=sport, stat_type=stat_type),
            "can_execute": False,
        })
    return {
        "terminal": True,
        "run_status": "COMPLETED",
        "routes_requested": len(tokens),
        "routes_terminated": len(route_results),
        "route_reconciliation_balanced": len(route_results) == len(tokens),
        "route_results": route_results,
        "calibration_performed": False,
        "certification_performed": False,
        "promotion_performed": False,
        "production_registration_performed": False,
        "can_execute": False,
    }


def _settlement_inventory_result(tokens: list[str]) -> dict[str, Any]:
    wanted = set(tokens)
    rows = [
        dict(row) for row in settlement_runtime.build_settlement_inventory()
        if lifecycle_base._route_token(row.get("sport"), row.get("stat_type")) in wanted
    ]
    return {
        "route_dispositions": rows,
        "results": [],
        "can_execute": False,
    }


def _captured_theses_for_active_artifact(db: Any, sport: str, stat_type: str, now: datetime
                                         ) -> frozenset[tuple[str, str, str, str]]:
    """Upcoming theses already captured in both directions by the route's active artifact."""
    active = db.table("wow_prop_fitted_model_artifacts").select("model_artifact_version") \
        .eq("sport", sport).eq("stat_type", stat_type).eq("active", True).execute().data or []
    versions = {str(row["model_artifact_version"]) for row in active if row.get("model_artifact_version")}
    if not versions:
        return frozenset()
    rows = _paginate(
        lambda: db.table("wow_predictions")
        .select("event_id,player,stat_type,line,direction,model_artifact_version")
        .eq("sport", sport)
        .eq("stat_type", stat_type)
        .eq("model_provider_identity", PROVIDER)
        .eq("evidence_source_kind", FORWARD_EVIDENCE_KIND)
        .gt("event_start_time", now.isoformat())
    )
    directions: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
    for row in rows:
        key = _thesis_key(row)
        if key is not None and str(row.get("model_artifact_version")) in versions:
            directions[key].add(str(row.get("direction") or "").upper())
    return frozenset(key for key, seen in directions.items() if {"MORE", "LESS"} <= seen)


def run_pregame_forward_scoring(
    db: Any,
    market_api: Any,
    tokens: list[str],
    *,
    now: datetime,
    max_snapshots: int = PREGAME_SCORING_MAX_SNAPSHOTS,
    deadline_seconds: float = PREGAME_SCORING_DEADLINE_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Score persisted pregame snapshots with the route's existing fitted scorer.

    The hourly lane seeds snapshots but nothing scored them pregame, so the
    immutable forward cohort could not grow. This runs only the generic
    exact-route scorer over snapshots already in the database (no acquisition),
    persists immutable IMMUTABLE_PREGAME_SETTLED predictions, and never fits,
    calibrates, certifies, promotes, publishes or executes.
    """
    started = clock()
    routes: list[dict[str, Any]] = []
    generic: list[tuple[str, frozenset]] = []
    for token in tokens:
        sport, stat_type = token.split(":", 1)
        collector, _status, blocker = _collector_for(route_key(sport, stat_type))
        if collector != COLLECTOR_GENERIC:
            routes.append({"route": token, "status": "SKIPPED_NOT_GENERIC_COLLECTOR",
                           "collector": collector, "blocker": blocker, "can_execute": False})
            continue
        try:
            skip = _captured_theses_for_active_artifact(db, sport, stat_type, now)
        except Exception:  # noqa: BLE001 - lookup failure only costs a rescore (upsert dedupes)
            skip = frozenset()
        generic.append((token, skip))
    # Least-covered route first, so a slow scorer cannot starve the other routes.
    generic.sort(key=lambda item: (len(item[1]), item[0]))

    def over_budget() -> bool:
        return clock() - started > deadline_seconds

    for token, skip in generic:
        sport, stat_type = token.split(":", 1)
        if over_budget():
            routes.append({"route": token, "status": "DEFERRED_DEADLINE",
                           "skipped_captured_for_active_artifact": len(skip), "can_execute": False})
            continue
        try:
            out = run_generic_forward_route(sport=sport, stat_type=stat_type, max_snapshots=max_snapshots,
                                            db=db, market_api=market_api, now=now, skip_theses=skip,
                                            should_stop=over_budget)
        except Exception as exc:  # noqa: BLE001 - one route never blocks another
            routes.append({"route": token, "status": "FAILED", "error_type": type(exc).__name__,
                           "can_execute": False})
            continue
        held_codes: dict[str, int] = defaultdict(int)
        for row in out.get("rows") or []:
            if str(row.get("status", "")).startswith("HELD"):
                detail = row.get("detail") if isinstance(row.get("detail"), Mapping) else {}
                held_codes[str(detail.get("code") or row.get("status"))] += 1
        routes.append({
            "route": token,
            "status": out.get("status"),
            "skipped_captured_for_active_artifact": len(skip),
            "snapshots_considered": out.get("snapshots_considered"),
            "captured_forward_predictions": out.get("captured_forward_predictions"),
            "skipped_already_captured": out.get("skipped_already_captured"),
            "held": out.get("held"),
            "held_by_code": dict(held_codes),
            "row_reconciliation": out.get("row_reconciliation"),
            "can_execute": False,
        })
    return {
        "routes": routes,
        "captured_forward_predictions": sum(int(r.get("captured_forward_predictions") or 0) for r in routes),
        "max_snapshots_per_route": max_snapshots,
        "elapsed_seconds": round(clock() - started, 3),
        "acquisition_performed": False,
        "calibration_or_certification_performed": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def run_priority_durable_audit(req: PriorityPropDurableAuditRequest, *, db: Any,
                               market_api: Any = None, now: datetime | None = None) -> dict[str, Any]:
    captured_at = datetime.now(timezone.utc).isoformat()
    cycle_id = str(uuid4())
    audit_now = now or datetime.now(timezone.utc)
    tokens = lifecycle_base._requested_tokens(req.routes)
    scoring_stage = None
    if market_api is not None:
        scoring_stage = lifecycle_base._safe_stage(
            "PREGAME_FORWARD_SCORING",
            lambda: run_pregame_forward_scoring(db, market_api, tokens, now=audit_now),
        )

    # Paper-trade construction reads only persisted, already-scored predictions,
    # so it runs after scoring and before the durable counting stages.
    slip_stage = lifecycle_base._safe_stage(
        "PAPER_TRADE_SLIP_LANE",
        lambda: run_slip_lane(db, now=audit_now),
    )

    forward_stage = lifecycle_base._safe_stage(
        "DURABLE_FORWARD_EVIDENCE_AUDIT",
        lambda: _durable_forward_result(db, tokens),
    )
    certification_stage = lifecycle_base._safe_stage(
        "CALIBRATION_CERTIFICATION_READINESS",
        lambda: run_prop_certification_audit(
            PropCertificationAuditRequest(routes=tokens, include_inactive=True), db=db
        ),
    )
    registration_stage = lifecycle_base._safe_stage(
        "PRODUCTION_REGISTRATION_AUDIT",
        lambda: run_prop_production_registration_audit(
            PropProductionRegistrationAuditRequest(routes=tokens), db=db
        ),
    )
    candidate_stage = lifecycle_base._safe_stage(
        "CALIBRATOR_CANDIDATE_EVIDENCE",
        lambda: run_prop_calibrator_candidate_audit(
            PropCalibratorCandidateRequest(routes=tokens, include_inactive=True), db=db
        ),
    )

    dashboard = lifecycle_base.build_lifecycle_dashboard(
        forward=forward_stage.get("result"),
        settlement=_settlement_inventory_result(tokens),
        certification=certification_stage.get("result"),
        registration=registration_stage.get("result"),
        requested_tokens=tokens,
        captured_at=captured_at,
    )
    result = {
        "terminal": True,
        "cycle_id": cycle_id,
        "captured_at": captured_at,
        "routes_requested": len(tokens),
        "forward_capture": forward_stage,
        "certification_readiness": certification_stage,
        "production_registration": registration_stage,
        "calibrator_candidate_evidence": candidate_stage,
        "paper_trade_slip_lane": slip_stage,
        "dashboard": dashboard,
        "automatic_certification": False,
        "automatic_promotion": False,
        "can_execute": False,
    }
    enrich_artifact_cohort_health(result)
    enrich_calibrator_candidate_health(result, candidate_stage.get("result"))
    stage_statuses = {
        stage["stage"]: stage["status"]
        for stage in (forward_stage, certification_stage, registration_stage, candidate_stage, slip_stage)
    }
    failed = [name for name, status in stage_statuses.items() if status != "PASS"]
    result["stage_statuses"] = stage_statuses
    result["operator_summary"] = summarize_prop_routes(
        result["dashboard"], requested_routes=tokens, stage_statuses=stage_statuses,
    )
    if scoring_stage is not None:
        result["pregame_forward_scoring"] = scoring_stage
        if scoring_stage["status"] != "PASS":
            failed.append(scoring_stage["stage"])
    result["failed_stages"] = failed
    result["run_status"] = "COMPLETED" if not failed else "COMPLETED_WITH_STAGE_BLOCKERS"
    result["health_persistence"] = lifecycle_base.persist_lifecycle_health(
        db, cycle_id=cycle_id, dashboard=result["dashboard"]
    )
    result["artifact_isolation_enforced"] = True
    result["calibrator_candidate_generation_enabled"] = True
    result["untouched_holdout_required"] = True
    result["automatic_certification"] = False
    result["automatic_promotion"] = False
    result["can_execute"] = False
    return result


def install_priority_prop_lifecycle_runtime(
    app: FastAPI,
    *,
    auth_dependency: Any,
    db_client_fn: Callable[[], Any],
    market_api: Any = None,
) -> None:
    automation_auth = scout_route_auth_dependency(auth_dependency)

    if not any(getattr(route, "path", None) == "/v17/prop-priority-settlement-run" for route in app.router.routes):
        @app.post(
            "/v17/prop-priority-settlement-run",
            dependencies=[automation_auth],
            operation_id="runWowV17PriorityPropSettlement",
        )
        def priority_prop_settlement(req: PriorityPropSettlementRequest):
            result = settlement_runtime.run_exact_route_settlement(
                settlement_runtime.ExactRouteSettlementRequest(routes=req.routes, limit=req.limit),
                db=db_client_fn(),
            )
            result["automatic_certification"] = False
            result["automatic_promotion"] = False
            result["can_execute"] = False
            return result

    if not any(getattr(route, "path", None) == "/v17/prop-priority-durable-audit-run" for route in app.router.routes):
        @app.post(
            "/v17/prop-priority-durable-audit-run",
            dependencies=[automation_auth],
            operation_id="runWowV17PriorityPropDurableAudit",
        )
        def priority_prop_durable_audit(req: PriorityPropDurableAuditRequest):
            return run_priority_durable_audit(req, db=db_client_fn(), market_api=market_api)


__all__ = [
    "CAN_EXECUTE",
    "PriorityPropDurableAuditRequest",
    "PriorityPropSettlementRequest",
    "install_priority_prop_lifecycle_runtime",
    "run_priority_durable_audit",
]
