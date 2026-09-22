"""Durable V17 large-board recovery and run-control endpoints.

This module adds orchestration only. It never computes or changes sporting
probability. The existing /score-pick-request route remains the only scoring
path and V17_TERMINAL_REDUCER remains the sole terminal authority.

Guarantees:
- semantically equivalent row identities survive harmless serialization drift;
- the entire board manifest can be frozen before any scoring starts;
- a fresh client can read and resume by request_id alone;
- closed runs cannot silently return to RUNNING;
- can_execute is always false.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Optional

from fastapi import Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

import pick_request_runtime_core as pick_runtime
from v17 import pick_request_state_runtime as state
from v17.prediction_receipt_lookup_runtime import (
    PredictionReceiptLookupBatch,
    PredictionReceiptLookupRow,
    lookup_prediction_receipts,
)

CAN_EXECUTE = False
CONTROL_VERSION = "V17_PICK_REQUEST_RUN_CONTROL_V1"
IDENTITY_VERSION = "V2_SEMANTIC_UTC"
_STATE_KEY = "wow_pick_request_run_control_installed"
_TERMINAL_RUN_PREFIXES = ("STOPPED_", "CLOSED")

_ORIGINAL_BEGIN = state.PickRequestStateStore.begin
_ORIGINAL_FINALIZE = state.PickRequestStateStore.finalize
_ORIGINAL_REUSABLE = state.RunContext.reusable_outcome
_PATCH_INSTALLED = False


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _upper(value: Any) -> str:
    return "_".join(_clean(value).upper().replace("-", " ").split())


def _canonical_instant(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = _clean(value)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError("EVENT_START_TIME_INVALID") from exc
    if parsed.utcoffset() is None:
        raise ValueError("EVENT_START_TIME_TIMEZONE_REQUIRED")
    return parsed.astimezone(timezone.utc).isoformat()


def _canonical_stat(sport: Any, stat_type: Any) -> str:
    return pick_runtime._canonical_stat(_upper(sport), _upper(stat_type))


def _canonical_platform(value: Any) -> str | None:
    text = _clean(value)
    return text.upper() if text else None


def _semantic_identity_from_row(row: Any) -> dict[str, Any]:
    return {
        "event_id": _clean(getattr(row, "event_id", "")),
        "event_start_time": _canonical_instant(getattr(row, "event_start_time", "")),
        "sport": _upper(getattr(row, "sport", "")),
        "player": _clean(getattr(row, "player", "")),
        "stat_type": _canonical_stat(getattr(row, "sport", ""), getattr(row, "stat_type", "")),
        "exact_line": float(getattr(row, "line")),
        "direction": _upper(getattr(row, "direction", "")),
        "source_type": _upper(getattr(row, "source_type", "NORMALIZED")),
        "platform": _canonical_platform(getattr(row, "platform", None)),
    }


def _semantic_identity_from_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": _clean(record.get("event_id")),
        "event_start_time": _canonical_instant(record.get("event_start_time")),
        "sport": _upper(record.get("sport")),
        "player": _clean(record.get("player")),
        "stat_type": _canonical_stat(record.get("sport"), record.get("stat_type")),
        "exact_line": float(record.get("exact_line")),
        "direction": _upper(record.get("direction")),
        "source_type": _upper(record.get("source_type") or "NORMALIZED"),
        "platform": _canonical_platform(record.get("platform")),
    }


def _identity_diff(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    return [key for key in left if left.get(key) != right.get(key)]


def _canonical_row(row: pick_runtime.PickRequestRow) -> pick_runtime.PickRequestRow:
    identity = _semantic_identity_from_row(row)
    return row.model_copy(
        update={
            "event_id": identity["event_id"],
            "event_start_time": identity["event_start_time"],
            "sport": identity["sport"],
            "player": identity["player"],
            "stat_type": identity["stat_type"],
            "line": identity["exact_line"],
            "direction": identity["direction"],
            "source_type": identity["source_type"],
            "platform": identity["platform"],
        }
    )


def _run_row(db: Any, run_id: str) -> dict[str, Any] | None:
    result = db.table(state.RUN_TABLE).select("*").eq("run_id", run_id).execute()
    rows = list(getattr(result, "data", None) or [])
    return dict(rows[0]) if rows else None


def _is_closed(run: dict[str, Any] | None) -> bool:
    status = _upper((run or {}).get("run_status"))
    return any(status.startswith(prefix) for prefix in _TERMINAL_RUN_PREFIXES)


def _closed_error(run: dict[str, Any], request_id: str) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "RUN_CLOSED_REOPEN_REQUIRED",
            "request_id": request_id,
            "run_status": run.get("run_status"),
            "closure_code": run.get("closure_code"),
            "closed_at": run.get("closed_at"),
            "reopen_allowed": bool(run.get("reopen_allowed")),
            "can_execute": False,
        },
    )


def _semantic_begin(self: state.PickRequestStateStore, batch: Any) -> state.RunContext:
    request_id = state.effective_request_id(batch)
    run_id = state._run_id(request_id)
    run = _run_row(self.db, run_id)
    if _is_closed(run):
        raise _closed_error(run or {}, request_id)

    existing = {str(item.get("row_key")): item for item in self._load_all(run_id)}
    normalized_rows: list[Any] = []
    seen: set[str] = set()
    for index, raw_row in enumerate(batch.rows):
        key = state._row_key(raw_row, index)
        if key in seen:
            raise HTTPException(
                status_code=409,
                detail={"code": "RUN_ROW_KEY_DUPLICATE", "row_key": key, "can_execute": False},
            )
        seen.add(key)
        row = raw_row if getattr(raw_row, "row_key", None) else raw_row.model_copy(update={"row_key": key})
        canonical = _canonical_row(row)
        prior = existing.get(key)
        if prior is not None:
            incoming_identity = _semantic_identity_from_row(canonical)
            persisted_identity = _semantic_identity_from_record(prior)
            conflicts = _identity_diff(incoming_identity, persisted_identity)
            if conflicts:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "RUN_ROW_IDENTITY_CONFLICT",
                        "request_id": request_id,
                        "row_key": key,
                        "conflicting_fields": conflicts,
                        "identity_version": IDENTITY_VERSION,
                        "can_execute": False,
                    },
                )
            expected_hash = state._identity_hash(canonical)
            if str(prior.get("identity_hash") or "") != expected_hash:
                migrated = dict(prior)
                migrated["identity_hash"] = expected_hash
                migrated.update(state._identity(canonical))
                migrated["identity_version"] = IDENTITY_VERSION
                migrated["updated_at"] = state._now()
                self.db.table(state.ROW_TABLE).upsert(
                    migrated, on_conflict="run_id,row_key"
                ).execute()
        normalized_rows.append(canonical)

    normalized_batch = batch.model_copy(
        update={"request_id": request_id, "rows": normalized_rows}
    )
    ctx = _ORIGINAL_BEGIN(self, normalized_batch)
    records: list[dict[str, Any]] = []
    for index, row in enumerate(ctx.batch.rows):
        key = state._row_key(row, index)
        record = dict(ctx.rows[key])
        record["identity_version"] = IDENTITY_VERSION
        record["request_payload"] = row.model_dump(mode="json")
        records.append(record)
        ctx.rows[key] = record
    if records:
        self.db.table(state.ROW_TABLE).upsert(
            records, on_conflict="run_id,row_key"
        ).execute()
    return ctx


def _closure_safe_finalize(
    self: state.PickRequestStateStore,
    ctx: state.RunContext,
    outcomes: list[dict[str, Any]],
) -> dict[str, Any]:
    manifest = _ORIGINAL_FINALIZE(self, ctx, outcomes)
    run = _run_row(self.db, ctx.run_id)
    if run and run.get("closed_at") and run.get("closure_code"):
        restored = dict(run)
        restored["run_status"] = str(run["closure_code"])
        restored["updated_at"] = state._now()
        restored["can_execute"] = False
        self.db.table(state.RUN_TABLE).upsert(restored, on_conflict="run_id").execute()
        manifest["run_status"] = restored["run_status"]
        manifest["closed_at"] = restored.get("closed_at")
        manifest["closure_code"] = restored.get("closure_code")
    manifest["can_execute"] = False
    return manifest


def _reusable_terminal_outcome(self: state.RunContext, row_key: str) -> dict[str, Any] | None:
    record = self.rows.get(row_key) or {}
    outcome = record.get("outcome")
    if (
        isinstance(outcome, dict)
        and int(record.get("stage_seq") or 0) >= state.STAGE_SEQ["RECEIPT_PERSISTED"]
        and record.get("terminal_status") in {"COMPLETED", "HELD", "REJECTED"}
        and record.get("prediction_id")
    ):
        self.resumed_rows.add(row_key)
        copied = deepcopy(outcome)
        copied["resumed_from_durable_receipt"] = True
        copied["can_execute"] = False
        return copied
    return None


def install_semantic_identity_patch() -> None:
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return
    state.PickRequestStateStore.begin = _semantic_begin
    state.PickRequestStateStore.finalize = _closure_safe_finalize
    state.RunContext.reusable_outcome = _reusable_terminal_outcome
    _PATCH_INSTALLED = True


class ResumablePickRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1)
    rows: list[pick_runtime.PickRequestRow] = Field(default_factory=list, max_length=1000)
    response_mode: Literal["FULL", "COMPACT"] = "COMPACT"
    batch_size: int = Field(default=20, ge=1, le=50)
    max_batches: int = Field(default=25, ge=1, le=25)


class ClosePickRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    closure_code: str = Field(min_length=1)
    closure_reason: str = Field(default="", max_length=2000)
    reopen_allowed: bool = False
    closure_metadata: dict[str, Any] = Field(default_factory=dict)


def _load_run_by_request_id(db: Any, request_id: str) -> dict[str, Any]:
    result = db.table(state.RUN_TABLE).select("*").eq("request_id", request_id).execute()
    rows = list(getattr(result, "data", None) or [])
    if not rows:
        raise HTTPException(
            status_code=404,
            detail={"code": "PICK_REQUEST_RUN_NOT_FOUND", "request_id": request_id, "can_execute": False},
        )
    return dict(rows[0])


def _public_row(record: dict[str, Any], *, include_outcome: bool) -> dict[str, Any]:
    keys = (
        "row_key",
        "event_id",
        "event_start_time",
        "sport",
        "player",
        "stat_type",
        "exact_line",
        "direction",
        "source_type",
        "platform",
        "identity_hash",
        "identity_version",
        "current_stage",
        "stage_seq",
        "retry_count",
        "terminal_status",
        "terminal_code",
        "failure_domain",
        "durable_status",
        "model_evaluated",
        "probability_publishable",
        "rank_eligible",
        "prediction_id",
        "source_snapshot_id",
        "updated_at",
        "can_execute",
    )
    output = {key: record.get(key) for key in keys}
    if include_outcome:
        output["outcome"] = record.get("outcome")
    return output


def read_run_state(
    db: Any,
    request_id: str,
    *,
    offset: int = 0,
    limit: int = 100,
    include_outcomes: bool = False,
) -> dict[str, Any]:
    run = _load_run_by_request_id(db, request_id)
    run_id = str(run["run_id"])
    rows = state.PickRequestStateStore(db)._load_all(run_id)
    rows.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("row_key") or "")))
    page = rows[offset : offset + limit]
    return {
        "control_version": CONTROL_VERSION,
        "request_id": request_id,
        "run": run,
        "rows_total": len(rows),
        "offset": offset,
        "limit": limit,
        "next_offset": offset + len(page) if offset + len(page) < len(rows) else None,
        "rows": [_public_row(item, include_outcome=include_outcomes) for item in page],
        "resumable": not _is_closed(run) and any(
            item.get("terminal_status") == "PENDING"
            or (item.get("model_evaluated") is True and int(item.get("stage_seq") or 0) < state.STAGE_SEQ["RECEIPT_PERSISTED"])
            for item in rows
        ),
        "can_execute": False,
    }


def close_run(db: Any, request_id: str, request: ClosePickRunRequest) -> dict[str, Any]:
    run = _load_run_by_request_id(db, request_id)
    closure_code = _upper(request.closure_code)
    if not (closure_code.startswith("STOPPED_") or closure_code.startswith("CLOSED")):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "RUN_CLOSURE_CODE_INVALID",
                "required_prefixes": ["STOPPED_", "CLOSED"],
                "can_execute": False,
            },
        )
    if _is_closed(run):
        return {
            "request_id": request_id,
            "run_status": run.get("run_status"),
            "already_closed": True,
            "closure_code": run.get("closure_code"),
            "closed_at": run.get("closed_at"),
            "can_execute": False,
        }
    closed_at = state._now()
    updated = dict(run)
    updated.update(
        {
            "run_status": closure_code,
            "closure_code": closure_code,
            "closure_reason": request.closure_reason,
            "closed_at": closed_at,
            "reopen_allowed": bool(request.reopen_allowed),
            "closure_metadata": deepcopy(request.closure_metadata),
            "run_control_version": CONTROL_VERSION,
            "updated_at": closed_at,
            "can_execute": False,
        }
    )
    db.table(state.RUN_TABLE).upsert(updated, on_conflict="run_id").execute()
    return {
        "request_id": request_id,
        "run_id": run.get("run_id"),
        "run_status": updated["run_status"],
        "closure_code": updated["closure_code"],
        "closed_at": closed_at,
        "reopen_allowed": updated["reopen_allowed"],
        "can_execute": False,
    }


def _seed_manifest(db: Any, request: ResumablePickRunRequest) -> None:
    if not request.rows:
        return
    run_id = state._run_id(request.request_id)
    existing = {str(item.get("row_key")): item for item in state.PickRequestStateStore(db)._load_all(run_id)}
    seen: set[str] = set()
    canonical_rows: list[pick_runtime.PickRequestRow] = []
    for index, raw in enumerate(request.rows):
        key = state._row_key(raw, index)
        if key in seen:
            raise HTTPException(status_code=409, detail={"code": "RUN_ROW_KEY_DUPLICATE", "row_key": key, "can_execute": False})
        seen.add(key)
        row = raw if getattr(raw, "row_key", None) else raw.model_copy(update={"row_key": key})
        row = _canonical_row(row)
        prior = existing.get(key)
        if prior is not None:
            conflicts = _identity_diff(_semantic_identity_from_row(row), _semantic_identity_from_record(prior))
            if conflicts:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "RUN_ROW_IDENTITY_CONFLICT",
                        "request_id": request.request_id,
                        "row_key": key,
                        "conflicting_fields": conflicts,
                        "can_execute": False,
                    },
                )
        canonical_rows.append(row)

    new_rows = [row for row in canonical_rows if str(row.row_key) not in existing]
    store = state.PickRequestStateStore(db)
    for start in range(0, len(new_rows), 50):
        chunk = new_rows[start : start + 50]
        if chunk:
            store.begin(
                pick_runtime.PickRequestBatch(
                    request_id=request.request_id,
                    response_mode=request.response_mode,
                    rows=chunk,
                )
            )

    current = {str(item.get("row_key")): item for item in store._load_all(run_id)}
    enriched: list[dict[str, Any]] = []
    for row in canonical_rows:
        key = str(row.row_key)
        record = dict(current[key])
        record["identity_version"] = IDENTITY_VERSION
        record["request_payload"] = row.model_dump(mode="json")
        enriched.append(record)
    if enriched:
        db.table(state.ROW_TABLE).upsert(enriched, on_conflict="run_id,row_key").execute()


def _pending_rows(db: Any, request_id: str) -> list[dict[str, Any]]:
    run = _load_run_by_request_id(db, request_id)
    if _is_closed(run):
        raise _closed_error(run, request_id)
    rows = state.PickRequestStateStore(db)._load_all(str(run["run_id"]))
    pending = [
        item for item in rows
        if item.get("terminal_status") == "PENDING"
        or (item.get("model_evaluated") is True and int(item.get("stage_seq") or 0) < state.STAGE_SEQ["RECEIPT_PERSISTED"])
    ]
    pending.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("row_key") or "")))
    return pending


def _durable_pending_resume_is_safe(
    outcome: dict[str, Any],
    *,
    request_id: str,
    row_key: str,
) -> bool:
    resume = outcome.get("resume") if isinstance(outcome.get("resume"), dict) else {}
    return (
        outcome.get("status") == "UNRESOLVED"
        and outcome.get("code") == "DURABLE_ROW_PENDING_SAFE_TO_RESUME"
        and outcome.get("retry_allowed") is True
        and str(resume.get("request_id") or "") == str(request_id)
        and str(resume.get("row_key") or "") == str(row_key)
        and resume.get("contract") == "REUSE_EXACT_BOARD_REQUEST_ID_AND_ROW_KEY"
    )


def _receipt_preflight(
    db: Any,
    request_id: str,
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, dict[str, Any] | None]:
    if not records:
        return [], 0, None
    safe: list[dict[str, Any]] = []
    recovered = 0
    by_key = {str(item.get("row_key")): item for item in records}
    for start in range(0, len(records), 50):
        chunk = records[start : start + 50]
        lookup_rows = [
            PredictionReceiptLookupRow(
                row_key=str(item.get("row_key")),
                event_id=str(item.get("event_id")),
                sport=str(item.get("sport")),
                player=str(item.get("player")),
                stat_type=str(item.get("stat_type")),
                line=float(item.get("exact_line")),
                direction=str(item.get("direction")),
            )
            for item in chunk
        ]
        result = lookup_prediction_receipts(
            db, PredictionReceiptLookupBatch(request_id=request_id, rows=lookup_rows)
        )
        for outcome in result.get("rows") or []:
            key = str(outcome.get("row_key"))
            record = by_key[key]
            status = str(outcome.get("status") or "")
            if status == "NOT_FOUND" or _durable_pending_resume_is_safe(
                outcome,
                request_id=request_id,
                row_key=key,
            ):
                safe.append(record)
                continue
            if status != "MATCHED" or int(outcome.get("match_count") or 0) != 1:
                return safe, recovered, {
                    "code": outcome.get("code") or "RUN_RECEIPT_PREFLIGHT_BLOCKED",
                    "row_key": key,
                    "receipt_status": status,
                    "can_execute": False,
                }
            match = (outcome.get("matches") or [None])[0]
            if not isinstance(match, dict) or match.get("is_immutable_pregame") is not True:
                return safe, recovered, {
                    "code": "RUN_RECEIPT_NOT_PROVEN_IMMUTABLE_PREGAME",
                    "row_key": key,
                    "can_execute": False,
                }
            durable_outcome = record.get("outcome")
            if not isinstance(durable_outcome, dict):
                return safe, recovered, {
                    "code": "RUN_RECEIPT_MATCHED_OUTCOME_UNAVAILABLE",
                    "row_key": key,
                    "can_execute": False,
                }
            terminal_status = str(record.get("terminal_status") or "PENDING")
            if terminal_status == "PENDING":
                terminal_status = str(durable_outcome.get("terminal_status") or "PENDING")
            if terminal_status == "PENDING":
                return safe, recovered, {
                    "code": "RUN_RECEIPT_MATCHED_TERMINAL_STATUS_UNRESOLVED",
                    "row_key": key,
                    "can_execute": False,
                }
            prediction_id = match.get("governed_prediction_id")
            migrated = dict(record)
            migrated["prediction_id"] = prediction_id
            migrated["terminal_status"] = terminal_status
            migrated["current_stage"] = "RECEIPT_PERSISTED"
            migrated["stage_seq"] = state.STAGE_SEQ["RECEIPT_PERSISTED"]
            migrated["durable_status"] = state._durable_status(migrated)
            migrated["updated_at"] = state._now()
            migrated["can_execute"] = False
            db.table(state.ROW_TABLE).upsert(migrated, on_conflict="run_id,row_key").execute()
            safe.append(migrated)
            recovered += 1
    return safe, recovered, None


def run_resumable(
    db: Any,
    request: ResumablePickRunRequest,
    *,
    score_fn: Callable[..., dict[str, Any]],
    model_identity: str | None,
) -> dict[str, Any]:
    _seed_manifest(db, request)
    pending = _pending_rows(db, request.request_id)
    max_rows = request.batch_size * request.max_batches
    selected = pending[:max_rows]
    selected, receipt_recovered, stopped = _receipt_preflight(db, request.request_id, selected)
    batch_receipts: list[dict[str, Any]] = []

    for start in range(0, len(selected), request.batch_size):
        if stopped:
            break
        records = selected[start : start + request.batch_size]
        rows: list[pick_runtime.PickRequestRow] = []
        for record in records:
            payload = record.get("request_payload")
            if not isinstance(payload, dict):
                stopped = {
                    "code": "RUN_ROW_REQUEST_PAYLOAD_MISSING",
                    "row_key": record.get("row_key"),
                    "can_execute": False,
                }
                break
            rows.append(pick_runtime.PickRequestRow.model_validate(payload))
        if stopped:
            break
        try:
            result = score_fn(
                pick_runtime.PickRequestBatch(
                    request_id=request.request_id,
                    response_mode=request.response_mode,
                    rows=rows,
                ),
                model_identity,
            )
            reconciliation_pass = result.get("reconciliation_pass") is True
            batch_receipts.append(
                {
                    "rows_in": len(rows),
                    "run_controller_status": result.get("run_controller_status"),
                    "reconciliation_pass": reconciliation_pass,
                    "can_execute": False,
                }
            )
            if not reconciliation_pass:
                stopped = {
                    "code": "RUN_BATCH_RECONCILIATION_FAILED",
                    "can_execute": False,
                }
                break
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"code": "RUN_BATCH_FAILED", "detail": str(exc.detail)}
            stopped = {**detail, "http_status": exc.status_code, "can_execute": False}
            break
        except Exception as exc:
            stopped = {
                "code": "RUN_SERVER_SCORER_EXCEPTION",
                "error_type": type(exc).__name__,
                "can_execute": False,
            }
            break

    state_view = read_run_state(db, request.request_id, offset=0, limit=1)
    run = state_view["run"]
    remaining = _pending_rows(db, request.request_id) if not _is_closed(run) else []
    return {
        "control_version": CONTROL_VERSION,
        "request_id": request.request_id,
        "run_status": run.get("run_status"),
        "rows_manifested": state_view["rows_total"],
        "rows_attempted_this_call": sum(item["rows_in"] for item in batch_receipts),
        "batches_completed_this_call": len(batch_receipts),
        "pending_rows": len(remaining),
        "receipt_recovered_this_call": receipt_recovered,
        "stopped": stopped,
        "batch_receipts": batch_receipts,
        "next_action": "RESUME_SAME_REQUEST_ID" if remaining and not stopped else ("INSPECT_TYPED_STOP" if stopped else "RUN_TERMINAL"),
        "can_execute": False,
    }


def install_pick_request_run_control(
    app: Any,
    *,
    db_client_fn: Callable[[], Any],
) -> bool:
    install_semantic_identity_patch()
    if getattr(app.state, _STATE_KEY, False):
        return True

    score_route = next(
        (
            route
            for route in app.router.routes
            if getattr(route, "path", None) == "/score-pick-request"
            and "POST" in (getattr(route, "methods", set()) or set())
        ),
        None,
    )
    if score_route is None or not callable(getattr(score_route, "endpoint", None)):
        return False
    score_fn = score_route.endpoint
    dependencies = list(getattr(score_route, "dependencies", None) or [])

    @app.get(
        "/v17/pick-request-runs/{request_id}",
        dependencies=dependencies,
        operation_id="getWowV17PickRequestRunState",
    )
    def get_pick_request_run_state(
        request_id: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=250),
        include_outcomes: bool = Query(default=False),
    ) -> dict[str, Any]:
        return read_run_state(
            db_client_fn(),
            request_id,
            offset=offset,
            limit=limit,
            include_outcomes=include_outcomes,
        )

    @app.post(
        "/v17/pick-request-runs/resumable",
        dependencies=dependencies,
        operation_id="runWowV17ResumablePickRequest",
    )
    def run_resumable_pick_request(
        request: ResumablePickRunRequest,
        x_wow_model_identity: Optional[str] = Header(default=None, alias="X-WOW-Model-Identity"),
    ) -> dict[str, Any]:
        return run_resumable(
            db_client_fn(),
            request,
            score_fn=score_fn,
            model_identity=x_wow_model_identity,
        )

    @app.post(
        "/v17/pick-request-runs/{request_id}/close",
        dependencies=dependencies,
        operation_id="closeWowV17PickRequestRun",
    )
    def close_pick_request_run(
        request_id: str,
        request: ClosePickRunRequest,
    ) -> dict[str, Any]:
        return close_run(db_client_fn(), request_id, request)

    setattr(app.state, _STATE_KEY, True)
    return True


def schedule_pick_request_run_control_install(
    app: Any,
    *,
    db_client_fn: Callable[[], Any],
) -> None:
    scheduled_key = f"{_STATE_KEY}_scheduled"
    if getattr(app.state, scheduled_key, False):
        return

    @app.on_event("startup")
    async def _install() -> None:
        install_pick_request_run_control(app, db_client_fn=db_client_fn)

    setattr(app.state, scheduled_key, True)


__all__ = [
    "CAN_EXECUTE",
    "CONTROL_VERSION",
    "IDENTITY_VERSION",
    "ClosePickRunRequest",
    "ResumablePickRunRequest",
    "close_run",
    "install_pick_request_run_control",
    "install_semantic_identity_patch",
    "read_run_state",
    "run_resumable",
    "schedule_pick_request_run_control_install",
]
