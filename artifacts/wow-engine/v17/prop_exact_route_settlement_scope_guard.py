"""Forward-evidence scope guard for exact-route settlement.

Settlement may write calibration outcomes only for immutable forward-evidence
predictions.  Ordinary fitted-provider rows are not calibration observations.
The legacy MLB strikeout collector predates ``evidence_source_kind`` so its
already-authoritative forward selector is reused verbatim; newer exact-route
collectors must carry the explicit immutable-forward evidence marker.

No calibration, certification, promotion, publication, or execution authority
is granted here. ``can_execute`` remains false.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

import httpx

import v17.prop_exact_route_settlement as runtime
from v17.prop_forward_cohort_runtime import _forward_predictions as legacy_strikeout_forward_predictions
from v17.prop_universal_forward_evidence import EVIDENCE_SOURCE_KIND, route_key, route_token


LEGACY_STRIKEOUT_ROUTE = ("MLB", "PITCHER_STRIKEOUTS")
BASE_RUNNER = runtime.run_exact_route_settlement


def _is_temporally_immutable_forward(row: dict[str, Any]) -> bool:
    start = runtime._aware(row.get("event_start_time"))
    model_ts = runtime._aware(row.get("model_timestamp"))
    locked = runtime._aware(row.get("locked_at"))
    return bool(
        row.get("source_snapshot_id")
        and start is not None
        and model_ts is not None
        and locked is not None
        and model_ts < start
        and locked < start
    )


def _eligible_forward_predictions(
    db: Any,
    *,
    requested_routes: set[tuple[str, str]],
    now: datetime,
) -> list[dict[str, Any]]:
    rows = runtime._paginate(
        "wow_predictions.select_exact_route_forward_settlement_candidates",
        lambda: db.table("wow_predictions")
        .select(
            "prediction_id,source_snapshot_id,event_id,event_start_time,model_timestamp,locked_at,"
            "player,sport,stat_type,line,direction,model_family,model_artifact_version,"
            "model_artifact_checksum,primary_failure_path,evidence_source_kind"
        )
        .eq("model_provider_identity", runtime.PROVIDER)
        .lt("event_start_time", now.isoformat())
        .order("event_start_time"),
    )

    legacy_ids: set[str] = set()
    if LEGACY_STRIKEOUT_ROUTE in requested_routes:
        legacy_ids = {
            str(row.get("prediction_id"))
            for row in legacy_strikeout_forward_predictions(db)
            if row.get("prediction_id")
        }

    eligible: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        key = route_key(row.get("sport"), row.get("stat_type"))
        if key not in requested_routes or key not in runtime.SUPPORTED_SETTLEMENT_ROUTES:
            continue
        if not _is_temporally_immutable_forward(row):
            continue
        prediction_id = str(row.get("prediction_id") or "")
        if key == LEGACY_STRIKEOUT_ROUTE:
            if prediction_id not in legacy_ids:
                continue
        elif str(row.get("evidence_source_kind") or "") != EVIDENCE_SOURCE_KIND:
            continue
        eligible.append(row)
    return eligible


def run_exact_route_settlement(
    req: runtime.ExactRouteSettlementRequest,
    *,
    db: Any,
    http_get: Callable[..., Any] = httpx.get,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    requested_routes = runtime._parse_routes(req.routes)
    supported_requested = set(requested_routes) & set(runtime.SUPPORTED_SETTLEMENT_ROUTES)
    predictions = _eligible_forward_predictions(
        db,
        requested_routes=supported_requested,
        now=now,
    )
    ids = [str(row["prediction_id"]) for row in predictions if row.get("prediction_id")]
    existing = runtime._existing_outcomes(db, ids) if ids else set()
    pending = [
        row for row in predictions
        if str(row.get("prediction_id")) not in existing
    ][: req.limit]
    snapshot_ids = [
        str(row["source_snapshot_id"])
        for row in pending
        if row.get("source_snapshot_id")
    ]
    snapshot_map = runtime._snapshots(db, snapshot_ids) if snapshot_ids else {}

    results: list[dict[str, Any]] = []
    for prediction in pending:
        prediction_id = str(prediction.get("prediction_id") or "")
        snapshot_id = str(prediction.get("source_snapshot_id") or "")
        snapshot = snapshot_map.get(snapshot_id)
        key = route_key(prediction.get("sport"), prediction.get("stat_type"))
        if not snapshot:
            results.append({
                "prediction_id": prediction_id,
                "route": route_token(*key),
                "status": "IDENTITY_UNRESOLVED",
                "blocker": "SOURCE_SNAPSHOT_REQUIRED",
                "can_execute": False,
            })
            continue
        if key in runtime.MLB_SUPPORTED:
            result = runtime.settle_mlb_scalar(
                prediction, snapshot, http_get=http_get, now=now
            )
        elif key in runtime.WNBA_SUPPORTED:
            result = runtime.settle_wnba_scalar(
                prediction, snapshot, http_get=http_get, now=now
            )
        else:
            result = {
                "status": "EXACT_ROUTE_SETTLEMENT_ADAPTER_REQUIRED",
                "can_execute": False,
            }
        outcome = result.get("outcome") if isinstance(result.get("outcome"), dict) else None
        if outcome is not None:
            runtime._persist_outcome(db, outcome)
        results.append({
            "prediction_id": prediction_id,
            "route": route_token(*key),
            **result,
        })

    settled = sum(
        1 for row in results if str(row.get("status") or "").startswith("SETTLED")
    )
    held = len(results) - settled
    route_dispositions = []
    for key in requested_routes:
        status, source, blocker = runtime._route_status(key)
        route_dispositions.append({
            "sport": key[0],
            "stat_type": key[1],
            "status": status,
            "official_source": source,
            "blocker": blocker,
            "can_execute": False,
        })
    return {
        "terminal": True,
        "run_status": "COMPLETED",
        "requested_routes": len(requested_routes),
        "supported_settlement_routes_requested": len(supported_requested),
        "forward_evidence_predictions_eligible": len(predictions),
        "pending_predictions_considered": len(pending),
        "settled_or_voided": settled,
        "held": held,
        "results": results,
        "route_dispositions": route_dispositions,
        "full_settlement_inventory": runtime.build_settlement_inventory(),
        "settlement_scope": "IMMUTABLE_FORWARD_EVIDENCE_ONLY",
        "ordinary_prediction_rows_excluded": True,
        "calibration_performed": False,
        "certification_performed": False,
        "promotion_performed": False,
        "production_registration_performed": False,
        "can_execute": False,
    }


def install() -> None:
    runtime.run_exact_route_settlement = run_exact_route_settlement


install()


__all__ = [
    "BASE_RUNNER",
    "install",
    "run_exact_route_settlement",
]
