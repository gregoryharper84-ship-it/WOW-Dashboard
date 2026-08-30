"""Unified Pick Request row contract and exact-once batch controller.

This module is deliberately orchestration-only. It does not calculate sporting
probabilities, loosen evidence requirements, or replace controlling specialists.
It normalizes ingress metadata, acquires governed evidence for currently
supported automatic-hydration routes when a snapshot is absent, invokes the
existing governed single-row scorer, contains row-local failures, and
reconciles every submitted row exactly once.

can_execute is always false.
"""
from __future__ import annotations

from typing import Any, Callable, Literal, Optional, Type

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

import api_prod as prod
from prop_auto_hydration import PropAutoHydrationError, auto_hydrate_prop_candidate


PickSourceType = Literal[
    "SCREENSHOT",
    "PDF",
    "AUTONOMOUS_DISCOVERY",
    "PASTED_BOARD",
    "NORMALIZED",
]
SYNTHETIC_VALIDATION_SNAPSHOT_ID = "00000000-0000-0000-0000-000000000001"


class PickRequestRow(BaseModel):
    """Source-agnostic wrapper around one exact governed prop candidate."""

    model_config = ConfigDict(extra="forbid")

    row_id: str = Field(min_length=1)
    source_type: PickSourceType = "NORMALIZED"
    platform: Optional[str] = None
    league: Optional[str] = None
    opponent: Optional[str] = None
    settlement_operator: Optional[str] = None
    source_capture_timestamp: Optional[str] = None
    candidate: dict[str, Any]


class PickRequestBatch(BaseModel):
    """One request may contain independently terminalized candidate rows."""

    model_config = ConfigDict(extra="forbid")

    request_id: Optional[str] = None
    rows: list[PickRequestRow] = Field(min_length=1, max_length=100)


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().upper().split())


def canonical_candidate_key(row: PickRequestRow, candidate: Any) -> str:
    """Build the stable downstream key shared by upload and discovery ingress."""
    stat_type = _norm(getattr(candidate, "stat_type", ""))
    period = "FIRST_INNING" if "1IP" in stat_type or "FIRST_INNING" in stat_type else "FULL_GAME"
    return "|".join(
        (
            _norm(getattr(candidate, "sport", "")),
            _norm(row.league),
            _norm(getattr(candidate, "event_id", "")),
            _norm(getattr(candidate, "player", "")),
            _norm(row.opponent),
            "PLAYER_PROP",
            period,
            stat_type,
            format(float(getattr(candidate, "line")), ".12g"),
            _norm(getattr(candidate, "direction", "")),
            _norm(row.settlement_operator),
        )
    )


def _detail_dict(exc: HTTPException) -> dict[str, Any]:
    if isinstance(exc.detail, dict):
        return dict(exc.detail)
    return {"code": "ROW_HTTP_ERROR", "message": str(exc.detail)}


def _bucket_for_http_error(status_code: int, detail: dict[str, Any]) -> str:
    """Keep dependency/model unavailability separate from row rejection."""
    code = str(detail.get("code") or detail.get("blocker_code") or "")
    hold_codes = {
        "MODEL_UNAVAILABLE",
        "PROP_PROBABILITY_UNAVAILABLE",
        "SPECIALIST_ROUTING_UNAVAILABLE",
        "PROP_MODEL_REGISTRY_UNAVAILABLE",
        "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND",
        "PROP_CALIBRATOR_ADAPTER_UNAVAILABLE",
        "PROP_MODEL_FAMILY_ADAPTER_UNAVAILABLE",
        "PROP_AUTO_HYDRATION_UNSUPPORTED_ROUTE",
        "PROP_AUTO_HYDRATION_PROVIDER_UNAVAILABLE",
        "PROP_EVIDENCE_WRITE_UNAVAILABLE",
        "PROP_EVIDENCE_WRITE_UNPROVEN",
        "MLB_STARTER_STATUS_UNRESOLVED",
    }
    if status_code >= 500 or status_code == 409 or code in hold_codes:
        return "HELD"
    return "REJECTED"


def _auto_hydration_detail(exc: PropAutoHydrationError) -> dict[str, Any]:
    detail = dict(exc.detail)
    detail.update(
        {
            "code": exc.code,
            "message": str(exc),
            "evidence_hydration": "AUTO_HYDRATION_FAILED",
            "probability_publishable": False,
            "can_execute": False,
        }
    )
    return detail


def _telemetry(rows: list[dict[str, Any]]) -> dict[str, int]:
    route_preflight_blocked = 0
    hydration_not_attempted = 0
    acquisition_failures = 0
    auto_hydration_attempted = 0
    auto_hydration_succeeded = 0
    row_processing_errors = 0
    model_completed = 0

    for row in rows:
        detail = row.get("detail") or {}
        preparation = row.get("preparation") or {}
        code = str(row.get("termination_code") or "")
        if row.get("row_bucket") == "COMPLETED":
            model_completed += 1
        if detail.get("evidence_hydration") == "NOT_ATTEMPTED_ROUTE_BLOCKED":
            route_preflight_blocked += 1
            hydration_not_attempted += 1
        if preparation.get("auto_hydration_attempted") is True:
            auto_hydration_attempted += 1
        if preparation.get("auto_hydration_status") == "PASS":
            auto_hydration_succeeded += 1
        if code in {
            "RUN_INVALID_ACQUISITION_INCOMPLETE",
            "PROP_EVIDENCE_SNAPSHOT_NOT_FOUND",
            "PROP_EVIDENCE_INCOMPLETE",
            "PROP_EVIDENCE_STALE",
            "PROP_AUTO_HYDRATION_UNSUPPORTED_ROUTE",
            "PROP_AUTO_HYDRATION_PROVIDER_UNAVAILABLE",
            "PROP_EVIDENCE_WRITE_UNAVAILABLE",
            "PROP_EVIDENCE_WRITE_UNPROVEN",
            "MLB_RECENT_STARTS_INSUFFICIENT",
            "MLB_STARTER_STATUS_UNRESOLVED",
        }:
            acquisition_failures += 1
        if code == "ROW_PROCESSING_ERROR":
            row_processing_errors += 1

    return {
        "route_preflight_blocked": route_preflight_blocked,
        "evidence_hydration_not_attempted_route_blocked": hydration_not_attempted,
        "auto_hydration_attempted": auto_hydration_attempted,
        "auto_hydration_succeeded": auto_hydration_succeeded,
        "acquisition_failures": acquisition_failures,
        "model_completed": model_completed,
        "row_processing_errors": row_processing_errors,
        # A row-local failure never aborts another row in this controller.
        "false_global_failure_count": 0,
    }


def _validated_candidate(
    row: PickRequestRow,
    score_prop_model: Type[BaseModel],
) -> tuple[Any, bool]:
    payload = dict(row.candidate)
    needs_auto_hydration = not bool(payload.get("source_snapshot_id"))
    if needs_auto_hydration:
        # Pydantic validates every other field before any external acquisition.
        # The sentinel is never scored or persisted; successful auto hydration
        # replaces it before the existing governed scorer is invoked.
        payload["source_snapshot_id"] = SYNTHETIC_VALIDATION_SNAPSHOT_ID
    candidate = score_prop_model.model_validate(payload)
    return candidate, needs_auto_hydration


def _hydrate_candidate_if_needed(
    row: PickRequestRow,
    candidate: Any,
    *,
    needs_auto_hydration: bool,
) -> tuple[Any, dict[str, Any]]:
    if not needs_auto_hydration:
        return candidate, {
            "auto_hydration_attempted": False,
            "auto_hydration_status": "NOT_NEEDED",
            "source_snapshot_id": getattr(candidate, "source_snapshot_id", None),
            "can_execute": False,
        }

    hydration = auto_hydrate_prop_candidate(
        candidate,
        client=prod.get_client(),
        board_source=row.platform or row.source_type,
        board_capture=row.source_capture_timestamp,
    )
    prepared = candidate.model_copy(update={"source_snapshot_id": hydration["source_snapshot_id"]})
    return prepared, {
        "auto_hydration_attempted": True,
        "auto_hydration_status": "PASS",
        "provider": hydration.get("provider"),
        "source_snapshot_id": hydration.get("source_snapshot_id"),
        "official_game_pk": hydration.get("official_game_pk"),
        "starter_status": hydration.get("starter_status"),
        "historical_start_count": hydration.get("historical_start_count"),
        "captured_at": hydration.get("captured_at"),
        "can_execute": False,
    }


def run_pick_request_batch(
    *,
    batch: PickRequestBatch,
    score_prop_model: Type[BaseModel],
    score_prop_callable: Callable[..., dict[str, Any]],
    model_identity: Optional[str],
) -> dict[str, Any]:
    """Validate, hydrate, score, and terminalize every row independently."""
    row_ids = [row.row_id for row in batch.rows]
    if len(row_ids) != len(set(row_ids)):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PICK_REQUEST_DUPLICATE_ROW_ID",
                "rows_in": len(row_ids),
                "probability_publishable": False,
                "can_execute": False,
            },
        )

    terminal_rows: list[dict[str, Any]] = []

    for row in batch.rows:
        try:
            candidate, needs_auto_hydration = _validated_candidate(row, score_prop_model)
        except ValidationError as exc:
            terminal_rows.append(
                {
                    "row_id": row.row_id,
                    "canonical_key": None,
                    "source_type": row.source_type,
                    "row_bucket": "REJECTED",
                    "termination_code": "CANDIDATE_NORMALIZATION_INVALID",
                    "detail": {"validation_errors": exc.errors()},
                    "preparation": {
                        "auto_hydration_attempted": False,
                        "auto_hydration_status": "NOT_ATTEMPTED_INVALID_CANDIDATE",
                        "can_execute": False,
                    },
                    "probability_publishable": False,
                    "can_execute": False,
                }
            )
            continue

        canonical_key = canonical_candidate_key(row, candidate)
        try:
            candidate, preparation = _hydrate_candidate_if_needed(
                row,
                candidate,
                needs_auto_hydration=needs_auto_hydration,
            )
        except PropAutoHydrationError as exc:
            detail = _auto_hydration_detail(exc)
            terminal_rows.append(
                {
                    "row_id": row.row_id,
                    "canonical_key": canonical_key,
                    "source_type": row.source_type,
                    "row_bucket": _bucket_for_http_error(409, detail),
                    "termination_code": exc.code,
                    "http_status": 409,
                    "detail": detail,
                    "preparation": {
                        "auto_hydration_attempted": True,
                        "auto_hydration_status": "FAILED",
                        "can_execute": False,
                    },
                    "probability_publishable": False,
                    "can_execute": False,
                }
            )
            continue
        except Exception as exc:
            terminal_rows.append(
                {
                    "row_id": row.row_id,
                    "canonical_key": canonical_key,
                    "source_type": row.source_type,
                    "row_bucket": "HELD",
                    "termination_code": "AUTO_HYDRATION_INTERNAL_ERROR",
                    "detail": {"message": str(exc), "probability_publishable": False, "can_execute": False},
                    "preparation": {
                        "auto_hydration_attempted": True,
                        "auto_hydration_status": "FAILED",
                        "can_execute": False,
                    },
                    "probability_publishable": False,
                    "can_execute": False,
                }
            )
            continue

        try:
            result = score_prop_callable(
                candidate,
                x_wow_model_identity=model_identity,
            )
        except HTTPException as exc:
            detail = _detail_dict(exc)
            termination_code = str(detail.get("code") or detail.get("blocker_code") or "ROW_HTTP_ERROR")
            terminal_rows.append(
                {
                    "row_id": row.row_id,
                    "canonical_key": canonical_key,
                    "source_type": row.source_type,
                    "row_bucket": _bucket_for_http_error(exc.status_code, detail),
                    "termination_code": termination_code,
                    "http_status": exc.status_code,
                    "detail": detail,
                    "preparation": preparation,
                    "probability_publishable": False,
                    "can_execute": False,
                }
            )
            continue
        except Exception as exc:  # defensive row isolation; fail closed locally
            terminal_rows.append(
                {
                    "row_id": row.row_id,
                    "canonical_key": canonical_key,
                    "source_type": row.source_type,
                    "row_bucket": "HELD",
                    "termination_code": "ROW_PROCESSING_ERROR",
                    "detail": {"message": str(exc)},
                    "preparation": preparation,
                    "probability_publishable": False,
                    "can_execute": False,
                }
            )
            continue

        prediction = result.get("prediction") if isinstance(result, dict) else None
        terminal_rows.append(
            {
                "row_id": row.row_id,
                "canonical_key": canonical_key,
                "source_type": row.source_type,
                "row_bucket": "COMPLETED",
                "termination_code": (
                    prediction.get("terminal_label")
                    if isinstance(prediction, dict) and prediction.get("terminal_label")
                    else "MODEL_COMPLETED"
                ),
                "preparation": preparation,
                "result": result,
                "probability_publishable": bool(result.get("probability_publishable")) if isinstance(result, dict) else False,
                "can_execute": False,
            }
        )

    rows_in = len(batch.rows)
    rows_completed = sum(row["row_bucket"] == "COMPLETED" for row in terminal_rows)
    rows_held = sum(row["row_bucket"] == "HELD" for row in terminal_rows)
    rows_rejected = sum(row["row_bucket"] == "REJECTED" for row in terminal_rows)
    rows_terminal = rows_completed + rows_held + rows_rejected

    if rows_terminal != rows_in or len(terminal_rows) != rows_in:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "PICK_REQUEST_RECONCILIATION_FAILED",
                "rows_in": rows_in,
                "rows_terminal": rows_terminal,
                "terminal_rows": len(terminal_rows),
                "probability_publishable": False,
                "can_execute": False,
            },
        )

    if rows_completed == rows_in:
        controller_status = "COMPLETE"
    elif rows_completed > 0:
        controller_status = "DEGRADED"
    else:
        controller_status = "BLOCKED"

    return {
        "ok": rows_completed > 0,
        "request_id": batch.request_id,
        "run_controller_status": controller_status,
        "rows_in": rows_in,
        "rows_completed": rows_completed,
        "rows_held": rows_held,
        "rows_rejected": rows_rejected,
        "rows_terminal": rows_terminal,
        "reconciliation": {
            "equation": "rows_in = rows_completed + rows_held + rows_rejected",
            "passed": rows_in == rows_terminal,
        },
        "telemetry": _telemetry(terminal_rows),
        "rows": terminal_rows,
        "probability_publishable": any(row.get("probability_publishable") for row in terminal_rows),
        "can_execute": False,
    }


def install_pick_request_routes(
    *,
    app: Any,
    score_prop_model: Type[BaseModel],
    score_prop_callable: Callable[..., dict[str, Any]],
    require_action_api_key: Callable[..., Any],
) -> None:
    """Install the unified batch ingress on the existing production app."""

    @app.post(
        "/pick-request/props",
        dependencies=[Depends(require_action_api_key)],
        operation_id="scoreWowPickRequestProps",
    )
    def score_pick_request_props(
        batch: PickRequestBatch,
        x_wow_model_identity: Optional[str] = Header(default=None, alias="X-WOW-Model-Identity"),
    ):
        return run_pick_request_batch(
            batch=batch,
            score_prop_model=score_prop_model,
            score_prop_callable=score_prop_callable,
            model_identity=x_wow_model_identity,
        )
