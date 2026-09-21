"""Durable, resumable lifecycle state for V17 ``/score-pick-request``.

This layer does not score props and never changes a sporting probability. It
sits outside the interactive hydration wrapper, creates an exact-row run
manifest before external acquisition begins, and stores monotonic row-stage
receipts as the canonical scorer advances.

Correctness receipts are intentionally separate from fail-open Action invocation
telemetry. If this state ledger cannot accept the request, scoring does not begin.
If it becomes unavailable after model computation, the request fails closed
instead of acknowledging a governed completion that was not durably recorded.

``can_execute=false`` is invariant.
"""
from __future__ import annotations

import json
import logging
import uuid
from contextvars import ContextVar, Token
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Callable, Optional

from fastapi import Header, HTTPException

import pick_request_runtime_core as pick_runtime
from v17.top10_model_reconciliation import enforce_top10_completion

LOGGER = logging.getLogger("wow.v17.pick_request_state")
CAN_EXECUTE = False
RUN_TABLE = "wow_pick_request_runs"
ROW_TABLE = "wow_pick_request_row_states"
TRANSITION_TABLE = "wow_pick_request_row_transitions"
_STATE_KEY = "wow_pick_request_state_installed"

STAGES = (
    "INGESTED",
    "IDENTITY_VERIFIED",
    "MODEL_INPUTS_READY",
    "MODEL_COMPUTED",
    "RECEIPT_PERSISTED",
    "GOVERNANCE_AUDITED",
    "PUBLICATION_AUTHORIZED",
)
STAGE_SEQ = {stage: index for index, stage in enumerate(STAGES)}


class PickRequestStatePersistenceError(RuntimeError):
    def __init__(self, *, operation: str, row_key: str | None = None, outcome: dict[str, Any] | None = None, cause: Exception | None = None):
        super().__init__(f"PICK_REQUEST_STATE_PERSISTENCE_FAILED:{operation}")
        self.operation = operation
        self.row_key = row_key
        self.outcome = deepcopy(outcome) if isinstance(outcome, dict) else None
        self.cause = cause


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _upper(value: Any) -> str:
    return "_".join(_clean_text(value).upper().replace("-", " ").split())


def _row_key(row: Any, index: int) -> str:
    return str(getattr(row, "row_key", None) or f"row-{index + 1}")


def _identity_payload(row: Any) -> dict[str, Any]:
    return {
        "event_id": _clean_text(getattr(row, "event_id", "")),
        "event_start_time": _clean_text(getattr(row, "event_start_time", "")),
        "sport": _upper(getattr(row, "sport", "")),
        "player": _clean_text(getattr(row, "player", "")),
        "stat_type": _upper(getattr(row, "stat_type", "")),
        "exact_line": float(getattr(row, "line")),
        "direction": _upper(getattr(row, "direction", "")),
        "source_type": _upper(getattr(row, "source_type", "NORMALIZED")),
        "platform": _clean_text(getattr(row, "platform", None)) or None,
    }


def _identity_hash(row: Any) -> str:
    payload = json.dumps(_identity_payload(row), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(payload.encode("utf-8")).hexdigest()


def _batch_fingerprint(batch: Any) -> str:
    rows = []
    for index, row in enumerate(batch.rows):
        rows.append({"row_key": _row_key(row, index), "identity_hash": _identity_hash(row)})
    raw = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()


def effective_request_id(batch: Any) -> str:
    supplied = _clean_text(getattr(batch, "request_id", None))
    if supplied:
        return supplied
    return f"wow-pick-{_batch_fingerprint(batch)[:24]}"


def _run_id(request_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"wow-pick-request:{request_id}"))


def _transition_id(run_id: str, row_key: str, stage_seq: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"wow-pick-transition:{run_id}:{row_key}:{stage_seq}"))


def _prediction_id(outcome: dict[str, Any]) -> str | None:
    direct = outcome.get("prediction_id")
    if direct:
        return str(direct)
    result = outcome.get("result") if isinstance(outcome.get("result"), dict) else {}
    prediction = result.get("prediction") if isinstance(result.get("prediction"), dict) else {}
    value = prediction.get("prediction_id")
    return str(value) if value else None


def _source_snapshot_id(outcome: dict[str, Any]) -> str | None:
    value = outcome.get("source_snapshot_id")
    return str(value) if value else None


def _failure_domain(outcome: dict[str, Any]) -> str | None:
    code = _upper(outcome.get("code"))
    detail = outcome.get("detail") if isinstance(outcome.get("detail"), dict) else {}
    error_type = _upper(detail.get("error_type"))
    model_evaluated = outcome.get("model_evaluated") is True

    if code == "MODEL_UNAVAILABLE":
        return "MODEL_UNAVAILABLE"
    if code in {"MODEL_SCORER_FAILED", "ROW_SCORING_UNAVAILABLE", "PROP_PROBABILITY_UNAVAILABLE"}:
        return "MODEL_SERVICE_UNREACHABLE"
    if "TIMEOUT" in code or "CONNECTION" in code or "TIMEOUT" in error_type or "CONNECTION" in error_type:
        return "MODEL_SERVICE_UNREACHABLE" if not model_evaluated else "PUBLICATION_SERVICE_UNREACHABLE"
    if code in {"PROP_EVIDENCE_PERSISTENCE_UNAVAILABLE", "PREDICTION_PERSISTENCE_FAILED", "PREDICTION_RECEIPT_UNAVAILABLE"}:
        return "RECEIPT_SERVICE_UNREACHABLE"
    if model_evaluated and outcome.get("probability_publishable") is not True:
        return "PUBLICATION_SERVICE_UNREACHABLE"
    return None


def _durable_status(record: dict[str, Any]) -> str:
    seq = int(record.get("stage_seq") or 0)
    model_evaluated = record.get("model_evaluated") is True
    if model_evaluated and seq < STAGE_SEQ["RECEIPT_PERSISTED"]:
        return "MODEL_COMPUTED_RESEARCH_ONLY"
    if model_evaluated and seq >= STAGE_SEQ["RECEIPT_PERSISTED"] and record.get("probability_publishable") is not True:
        return "SPORTING_MODEL_COMPLETE_PUBLICATION_PENDING"
    if seq >= STAGE_SEQ["PUBLICATION_AUTHORIZED"]:
        return "GOVERNED_PUBLICATION_AUTHORIZED"
    terminal = str(record.get("terminal_status") or "PENDING")
    code = str(record.get("terminal_code") or "")
    return f"{terminal}:{code}" if code else terminal


def _execute(query: Any, *, operation: str, row_key: str | None = None, outcome: dict[str, Any] | None = None) -> Any:
    try:
        return query.execute()
    except Exception as exc:
        raise PickRequestStatePersistenceError(operation=operation, row_key=row_key, outcome=outcome, cause=exc) from exc


@dataclass
class RunContext:
    db: Any
    request_id: str
    run_id: str
    batch: Any
    rows: dict[str, dict[str, Any]] = field(default_factory=dict)
    row_models: dict[str, Any] = field(default_factory=dict)
    resumed_rows: set[str] = field(default_factory=set)

    def reusable_outcome(self, row_key: str) -> dict[str, Any] | None:
        record = self.rows.get(row_key) or {}
        outcome = record.get("outcome")
        if not isinstance(outcome, dict):
            return None
        if int(record.get("stage_seq") or 0) < STAGE_SEQ["RECEIPT_PERSISTED"]:
            return None
        if record.get("terminal_status") != "COMPLETED":
            return None
        if not record.get("prediction_id"):
            return None
        self.resumed_rows.add(row_key)
        copied = deepcopy(outcome)
        copied["resumed_from_durable_receipt"] = True
        copied["can_execute"] = False
        return copied


_ACTIVE: ContextVar[RunContext | None] = ContextVar("wow_pick_request_state", default=None)


class PickRequestStateStore:
    def __init__(self, db: Any):
        self.db = db

    def begin(self, batch: Any) -> RunContext:
        request_id = effective_request_id(batch)
        run_id = _run_id(request_id)
        normalized_rows: list[Any] = []
        seen: set[str] = set()
        for index, row in enumerate(batch.rows):
            key = _row_key(row, index)
            if key in seen:
                raise HTTPException(status_code=409, detail={"code": "RUN_ROW_KEY_DUPLICATE", "row_key": key, "can_execute": False})
            seen.add(key)
            normalized_rows.append(row if getattr(row, "row_key", None) else row.model_copy(update={"row_key": key}))
        normalized_batch = batch.model_copy(update={"request_id": request_id, "rows": normalized_rows})

        existing_result = _execute(
            self.db.table(ROW_TABLE).select("*").eq("run_id", run_id),
            operation="LOAD_EXISTING_ROWS",
        )
        existing = {str(item.get("row_key")): dict(item) for item in (getattr(existing_result, "data", None) or []) if isinstance(item, dict)}

        ctx = RunContext(db=self.db, request_id=request_id, run_id=run_id, batch=normalized_batch)
        new_rows: list[dict[str, Any]] = []
        retry_rows: list[dict[str, Any]] = []
        for index, row in enumerate(normalized_rows):
            key = _row_key(row, index)
            identity = _identity_payload(row)
            identity_hash = _identity_hash(row)
            prior = existing.get(key)
            if prior is not None and str(prior.get("identity_hash")) != identity_hash:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "RUN_ROW_IDENTITY_CONFLICT",
                        "request_id": request_id,
                        "row_key": key,
                        "can_execute": False,
                    },
                )
            base = dict(prior or {})
            base.update({
                "run_id": run_id,
                "row_key": key,
                "identity_hash": identity_hash,
                **identity,
                "can_execute": False,
            })
            if prior is None:
                base.update({
                    "current_stage": "INGESTED",
                    "stage_seq": 0,
                    "retry_count": 0,
                    "terminal_status": "PENDING",
                    "durable_status": "INGESTED",
                    "model_evaluated": False,
                    "probability_publishable": False,
                    "rank_eligible": False,
                    "updated_at": _now(),
                })
                new_rows.append(base)
            else:
                base["retry_count"] = int(prior.get("retry_count") or 0) + 1
                base["updated_at"] = _now()
                retry_rows.append(base)
            ctx.rows[key] = base
            ctx.row_models[key] = row

        _execute(
            self.db.table(RUN_TABLE).upsert({
                "run_id": run_id,
                "request_id": request_id,
                "source_kind": "PROP_BOARD",
                "run_status": "RUNNING",
                "total_rows": len(normalized_rows),
                "pending_rows": len(normalized_rows),
                "can_execute": False,
                "updated_at": _now(),
            }, on_conflict="run_id"),
            operation="UPSERT_RUN",
        )
        if new_rows:
            _execute(self.db.table(ROW_TABLE).upsert(new_rows, on_conflict="run_id,row_key"), operation="SEED_ROWS")
            transitions = [{
                "transition_id": _transition_id(run_id, record["row_key"], 0),
                "run_id": run_id,
                "row_key": record["row_key"],
                "from_stage": None,
                "to_stage": "INGESTED",
                "stage_seq": 0,
                "terminal_status": "PENDING",
                "metadata": {"source_type": record.get("source_type")},
                "can_execute": False,
            } for record in new_rows]
            _execute(self.db.table(TRANSITION_TABLE).upsert(transitions, on_conflict="transition_id"), operation="SEED_TRANSITIONS")
        if retry_rows:
            _execute(self.db.table(ROW_TABLE).upsert(retry_rows, on_conflict="run_id,row_key"), operation="INCREMENT_RETRY_ROWS")
        return ctx

    def _persist_row(self, ctx: RunContext, row_key: str, *, operation: str, outcome: dict[str, Any] | None = None) -> None:
        record = ctx.rows[row_key]
        record["durable_status"] = _durable_status(record)
        record["updated_at"] = _now()
        record["can_execute"] = False
        _execute(
            self.db.table(ROW_TABLE).upsert(record, on_conflict="run_id,row_key"),
            operation=operation,
            row_key=row_key,
            outcome=outcome,
        )

    def advance(self, ctx: RunContext, row_key: str, stage: str, *, metadata: dict[str, Any] | None = None) -> None:
        if stage not in STAGE_SEQ or row_key not in ctx.rows:
            return
        record = ctx.rows[row_key]
        target = STAGE_SEQ[stage]
        current = int(record.get("stage_seq") or 0)
        if target <= current:
            return
        # Record every skipped transition so process-death recovery can identify
        # exactly which milestones were proven, even when one hook advances more
        # than one state at once.
        for seq in range(current + 1, target + 1):
            to_stage = STAGES[seq]
            from_stage = STAGES[seq - 1]
            record["current_stage"] = to_stage
            record["stage_seq"] = seq
            self._persist_row(ctx, row_key, operation=f"ADVANCE_{to_stage}")
            _execute(
                self.db.table(TRANSITION_TABLE).upsert({
                    "transition_id": _transition_id(ctx.run_id, row_key, seq),
                    "run_id": ctx.run_id,
                    "row_key": row_key,
                    "from_stage": from_stage,
                    "to_stage": to_stage,
                    "stage_seq": seq,
                    "terminal_status": record.get("terminal_status"),
                    "terminal_code": record.get("terminal_code"),
                    "metadata": metadata or {},
                    "can_execute": False,
                }, on_conflict="transition_id"),
                operation=f"TRANSITION_{to_stage}",
                row_key=row_key,
            )

    def record_outcome(self, ctx: RunContext, row_key: str, outcome: dict[str, Any]) -> None:
        if row_key not in ctx.rows:
            return
        record = ctx.rows[row_key]
        model_evaluated = outcome.get("model_evaluated") is True
        prediction_id = _prediction_id(outcome)
        if model_evaluated:
            self.advance(ctx, row_key, "MODEL_COMPUTED")
        if prediction_id:
            record["prediction_id"] = prediction_id
            self.advance(ctx, row_key, "RECEIPT_PERSISTED")
        record.update({
            "terminal_status": str(outcome.get("terminal_status") or "PENDING"),
            "terminal_code": str(outcome.get("code") or "") or None,
            "failure_domain": _failure_domain(outcome),
            "model_evaluated": model_evaluated,
            "probability_publishable": outcome.get("probability_publishable") is True,
            "rank_eligible": outcome.get("rank_eligible") is True,
            "prediction_id": prediction_id or record.get("prediction_id"),
            "source_snapshot_id": _source_snapshot_id(outcome) or record.get("source_snapshot_id"),
            "outcome": deepcopy(outcome),
        })
        self._persist_row(ctx, row_key, operation="PERSIST_ROW_OUTCOME", outcome=outcome)

    def finalize(self, ctx: RunContext, outcomes: list[dict[str, Any]]) -> dict[str, Any]:
        by_key = {str(item.get("row_key")): item for item in outcomes if isinstance(item, dict)}
        for row_key, outcome in by_key.items():
            if row_key not in ctx.rows:
                continue
            self.record_outcome(ctx, row_key, outcome)
            if outcome.get("model_evaluated") is True:
                self.advance(ctx, row_key, "GOVERNANCE_AUDITED")
                if outcome.get("probability_publishable") is True:
                    self.advance(ctx, row_key, "PUBLICATION_AUTHORIZED")
                # Persist again because portfolio/governance may have mutated the
                # outcome after MODEL_COMPUTED was first written.
                ctx.rows[row_key]["outcome"] = deepcopy(outcome)
                ctx.rows[row_key]["probability_publishable"] = outcome.get("probability_publishable") is True
                ctx.rows[row_key]["rank_eligible"] = outcome.get("rank_eligible") is True
                self._persist_row(ctx, row_key, operation="PERSIST_GOVERNED_OUTCOME", outcome=outcome)

        records = list(ctx.rows.values())
        completed = sum(item.get("terminal_status") == "COMPLETED" for item in records)
        held = sum(item.get("terminal_status") == "HELD" for item in records)
        rejected = sum(item.get("terminal_status") == "REJECTED" for item in records)
        pending = sum(item.get("terminal_status") == "PENDING" for item in records)
        unresolved = sum(
            item.get("terminal_status") == "PENDING"
            or (item.get("model_evaluated") is True and int(item.get("stage_seq") or 0) < STAGE_SEQ["RECEIPT_PERSISTED"])
            for item in records
        )
        if pending:
            run_status = "RUNNING"
        elif completed == len(records):
            run_status = "COMPLETE"
        elif completed:
            run_status = "DEGRADED"
        else:
            run_status = "BLOCKED"
        manifest = {
            "run_id": ctx.run_id,
            "request_id": ctx.request_id,
            "run_status": run_status,
            "total_rows": len(records),
            "completed_rows": completed,
            "held_rows": held,
            "rejected_rows": rejected,
            "pending_rows": pending,
            "unresolved_rows": unresolved,
            "resumed_rows": len(ctx.resumed_rows),
            "can_execute": False,
            "updated_at": _now(),
        }
        _execute(self.db.table(RUN_TABLE).upsert(manifest, on_conflict="run_id"), operation="FINALIZE_RUN")
        return manifest


def current_context() -> RunContext | None:
    return _ACTIVE.get()


def record_inputs_ready(row: Any) -> None:
    ctx = current_context()
    if ctx is None:
        return
    key = str(getattr(row, "row_key", None) or "")
    if key not in ctx.rows:
        return
    store = PickRequestStateStore(ctx.db)
    store.advance(ctx, key, "IDENTITY_VERIFIED")
    store.advance(ctx, key, "MODEL_INPUTS_READY")


def record_model_outcome(outcome: dict[str, Any]) -> None:
    ctx = current_context()
    if ctx is None:
        return
    key = str(outcome.get("row_key") or "")
    if key not in ctx.rows:
        return
    PickRequestStateStore(ctx.db).record_outcome(ctx, key, outcome)


def record_terminal_outcome(outcome: dict[str, Any]) -> None:
    record_model_outcome(outcome)


def _persistence_http_error(exc: PickRequestStatePersistenceError, *, request_id: str | None = None) -> HTTPException:
    detail: dict[str, Any] = {
        "code": "RECEIPT_SERVICE_UNREACHABLE",
        "failure_domain": "RECEIPT_SERVICE_UNREACHABLE",
        "operation": exc.operation,
        "request_id": request_id,
        "row_key": exc.row_key,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }
    if exc.outcome and exc.outcome.get("model_evaluated") is True:
        degraded = deepcopy(exc.outcome)
        degraded["probability_publishable"] = False
        degraded["rank_eligible"] = False
        degraded["durable_status"] = "MODEL_COMPUTED_RESEARCH_ONLY"
        degraded["can_execute"] = False
        detail["model_computed_research_only"] = degraded
    return HTTPException(status_code=503, detail=detail)


def _full_outcomes(ctx: RunContext, source_rows: list[Any], inner: dict[str, Any] | None) -> list[dict[str, Any]]:
    inner_rows = {
        str(item.get("row_key")): item
        for item in ((inner or {}).get("rows") or [])
        if isinstance(item, dict)
    }
    outcomes: list[dict[str, Any]] = []
    for index, row in enumerate(source_rows):
        key = _row_key(row, index)
        record = ctx.rows.get(key) or {}
        durable = record.get("outcome")
        if isinstance(durable, dict):
            out = deepcopy(durable)
            if key in ctx.resumed_rows:
                out["resumed_from_durable_receipt"] = True
            outcomes.append(out)
            continue
        if isinstance(inner_rows.get(key), dict):
            outcomes.append(deepcopy(inner_rows[key]))
    return outcomes


def _reapply_portfolio_governance(request_id: str, source_rows: list[Any], outcomes: list[dict[str, Any]]) -> None:
    by_key = {str(item.get("row_key")): item for item in outcomes}
    scored_legs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for index, row in enumerate(source_rows):
        key = _row_key(row, index)
        outcome = by_key.get(key)
        if not isinstance(outcome, dict) or outcome.get("terminal_status") != "COMPLETED":
            continue
        result = outcome.get("result")
        if not isinstance(result, dict):
            continue
        canonical_stat = pick_runtime._canonical_stat(row.sport, row.stat_type)
        scored_legs.append((pick_runtime._portfolio_leg(key, row, canonical_stat, result), outcome))
    if scored_legs:
        pick_runtime._apply_portfolio_governance(request_id, scored_legs)


def _merged_response(ctx: RunContext, inner: dict[str, Any] | None) -> dict[str, Any]:
    source_rows = list(ctx.batch.rows)
    outcomes = _full_outcomes(ctx, source_rows, inner)
    _reapply_portfolio_governance(ctx.request_id, source_rows, outcomes)

    rows_in = len(source_rows)
    completed = sum(item.get("terminal_status") == "COMPLETED" for item in outcomes)
    held = sum(item.get("terminal_status") == "HELD" for item in outcomes)
    rejected = sum(item.get("terminal_status") == "REJECTED" for item in outcomes)
    if completed == rows_in:
        run_controller_status = "COMPLETE"
    elif completed:
        run_controller_status = "DEGRADED"
    else:
        run_controller_status = "BLOCKED"

    response = dict(inner or {})
    response.update({
        "ok": completed > 0,
        "request_id": ctx.request_id,
        "run_controller_status": run_controller_status,
        "rows_in": rows_in,
        "rows_completed": completed,
        "rows_held": held,
        "rows_rejected": rejected,
        "pick_rejected_count": sum(item.get("pick_rejected") is True for item in outcomes),
        "infrastructure_blocked_count": sum(item.get("infrastructure_blocked") is True for item in outcomes),
        "reconciliation_pass": rows_in == len(outcomes) == completed + held + rejected,
        "telemetry": pick_runtime._telemetry(outcomes),
        "specialist_utilization_summary": pick_runtime._specialist_utilization_summary(outcomes),
        "response_mode": ctx.batch.response_mode,
        "rows": outcomes,
        "probability_objective": "GOVERNED_MODEL_ONLY",
        "durable_resume": {
            "status": "ACTIVE",
            "resumed_rows": len(ctx.resumed_rows),
            "retry_contract": "REUSE_EXACT_REQUEST_ID_AND_ROW_KEY",
            "can_execute": False,
        },
        "can_execute": False,
    })
    response = enforce_top10_completion(response, source_rows)
    if ctx.batch.response_mode == "COMPACT":
        response["rows"] = [pick_runtime._compact_pick_outcome(item) for item in outcomes]
        response["detail_retrieval"] = {
            "mode": "IMMUTABLE_RECEIPT_LOOKUP",
            "operation_id": "lookupWowV17PredictionReceipts",
            "durable_resume": "REUSE_EXACT_REQUEST_ID_AND_ROW_KEY",
        }
    return response


def install_pick_request_state_wrapper(app: Any, *, db_client_fn: Callable[[], Any]) -> bool:
    if getattr(app.state, _STATE_KEY, False):
        return True
    captured_route = next((route for route in app.router.routes if getattr(route, "path", None) == "/score-pick-request" and "POST" in (getattr(route, "methods", set()) or set())), None)
    if captured_route is None or not callable(getattr(captured_route, "endpoint", None)):
        return False

    captured_endpoint = captured_route.endpoint
    dependencies = list(getattr(captured_route, "dependencies", None) or [])
    operation_id = str(getattr(captured_route, "operation_id", None) or "scoreWowPickRequest")
    app.router.routes[:] = [route for route in app.router.routes if route is not captured_route]

    def score_pick_request_durable(
        batch: pick_runtime.PickRequestBatch,
        x_wow_model_identity: Optional[str] = Header(default=None, alias="X-WOW-Model-Identity"),
    ) -> dict[str, Any]:
        try:
            store = PickRequestStateStore(db_client_fn())
            ctx = store.begin(batch)
        except PickRequestStatePersistenceError as exc:
            raise _persistence_http_error(exc, request_id=effective_request_id(batch)) from exc

        token: Token[RunContext | None] = _ACTIVE.set(ctx)
        try:
            fresh_rows: list[Any] = []
            for index, row in enumerate(ctx.batch.rows):
                key = _row_key(row, index)
                if ctx.reusable_outcome(key) is None:
                    fresh_rows.append(row)
            inner: dict[str, Any] | None = None
            if fresh_rows:
                fresh_batch = ctx.batch.model_copy(update={"rows": fresh_rows})
                inner = captured_endpoint(fresh_batch, x_wow_model_identity)
                if not isinstance(inner, dict):
                    raise HTTPException(status_code=500, detail={"code": "PICK_REQUEST_RESPONSE_INVALID", "can_execute": False})

            response = _merged_response(ctx, inner)
            manifest = store.finalize(ctx, list(response.get("rows") or []) if ctx.batch.response_mode == "FULL" else _full_outcomes(ctx, list(ctx.batch.rows), inner))
            response["durable_run_manifest"] = manifest
            response["can_execute"] = False
            LOGGER.warning(
                "WOW_V17_PICK_REQUEST_STATE status=%s request_id=%s run_id=%s rows=%s resumed=%s unresolved=%s can_execute=false",
                manifest["run_status"], ctx.request_id, ctx.run_id, manifest["total_rows"], manifest["resumed_rows"], manifest["unresolved_rows"],
            )
            return response
        except PickRequestStatePersistenceError as exc:
            raise _persistence_http_error(exc, request_id=ctx.request_id) from exc
        finally:
            _ACTIVE.reset(token)

    app.post("/score-pick-request", dependencies=dependencies, operation_id=operation_id)(score_pick_request_durable)
    setattr(app.state, _STATE_KEY, True)
    return True


def schedule_pick_request_state_install(app: Any, *, db_client_fn: Callable[[], Any]) -> None:
    scheduled_key = f"{_STATE_KEY}_scheduled"
    if getattr(app.state, scheduled_key, False):
        return

    @app.on_event("startup")
    async def _install_pick_request_state() -> None:
        installed = install_pick_request_state_wrapper(app, db_client_fn=db_client_fn)
        LOGGER.warning(
            "WOW_V17_PICK_REQUEST_STATE_WRAPPER status=%s can_execute=false",
            "INSTALLED" if installed else "NOT_INSTALLED_ROUTE_UNAVAILABLE",
        )

    setattr(app.state, scheduled_key, True)


__all__ = [
    "CAN_EXECUTE",
    "PickRequestStatePersistenceError",
    "PickRequestStateStore",
    "STAGES",
    "current_context",
    "effective_request_id",
    "install_pick_request_state_wrapper",
    "record_inputs_ready",
    "record_model_outcome",
    "record_terminal_outcome",
    "schedule_pick_request_state_install",
]
