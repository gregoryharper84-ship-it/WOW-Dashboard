"""Agent Runtime V1 API routes (packet section 9), Phase 1.

Registers directly onto api.py's shared FastAPI `app` object — api.py imports
this module last (see the comment at the bottom of api.py), so these routes
attach after every existing route is already registered. api_g11.py and
api_prod.py both copy `app`'s routes wholesale except the specific paths they
each override (/score-event, /governance, /score-prop), so anything
registered here propagates through both wrapper layers automatically without
either wrapper file changing.

Phase 1 provides the durable run ledger, idempotent creation, and polling
contract only. No real discovery/evidence/model worker exists yet (Phases
2-4), so a run created here has no path to a real terminal decision from
production request handling alone — it can reach CREATED, FAILED (contract
error), or CANCELED (administrative), never COMPLETED. The synchronous
fake-worker path proving the ledger + state machine + reducer +
reconciliation work end-to-end together lives in
test_agent_runtime_integration.py, not in this module.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import Header, HTTPException

from api import app, get_client
from agent_runtime import idempotency, repository, schemas
from agent_runtime.state_machine import RUN_TERMINAL_STATES

GOVERNANCE_VERSION = "WOW-AGENT-RUNTIME-V1-PHASE1"

_REQUEST_HASH_FIELDS = (
    "run_type", "as_of", "user_timezone", "lanes", "sports",
    "candidate_inputs", "discovery_enabled",
)

_JOB_STATUS_TO_MANIFEST_BUCKET = {
    "QUEUED": "queued", "RETRY_PENDING": "queued", "RUNNING": "running",
    "BLOCKED": "blocked", "TIMED_OUT": "timed_out",
}


@app.get("/health/live")
def health_live() -> dict[str, Any]:
    """Process is alive; no dependency calls (packet section 9)."""
    return {"status": "ok", "can_execute": False}


@app.get("/health/ready")
def health_ready() -> dict[str, Any]:
    """Database usable. Queue and worker-registry checks are added in Phase 2
    once a queue exists — reporting them ready today would be a fabricated
    positive, so this deliberately covers database reachability only."""
    try:
        get_client().table("wow_agent_runs").select("run_id").limit(1).execute()
        database_ok = True
    except Exception:
        database_ok = False

    body = {
        "status": "ok" if database_ok else "not_ready",
        "database": "ok" if database_ok else "unreachable",
        "queue": "NOT_YET_IMPLEMENTED_PHASE_2",
        "worker_registry": "NOT_YET_IMPLEMENTED_PHASE_2",
        "can_execute": False,
    }
    if not database_ok:
        raise HTTPException(status_code=503, detail=body)
    return body


@app.post("/wow/runs", status_code=202)
def create_run(
    req: schemas.RunCreateRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    if req.can_execute:
        raise HTTPException(
            status_code=422,
            detail={"code": "EXECUTION_PROHIBITED", "can_execute": False},
        )
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(
            status_code=400,
            detail={"code": "IDEMPOTENCY_KEY_REQUIRED", "message": "Idempotency-Key header is required."},
        )

    request_hash = idempotency.compute_request_hash(
        {field: getattr(req, field) for field in _REQUEST_HASH_FIELDS}
    )

    client = get_client()
    run_row, reused = repository.create_run(
        client,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        run_type=req.run_type,
        requested_as_of=req.as_of,
        user_timezone=req.user_timezone,
        governance_version=GOVERNANCE_VERSION,
    )
    if not reused:
        repository.record_audit_event(
            client, event_type="RUN_CREATED", actor="wow.agent-runtime-api",
            run_id=run_row["run_id"],
        )

    status = run_row["status"]
    return {
        "ok": True,
        "run_id": run_row["run_id"],
        "status": status,
        "terminal": status in RUN_TERMINAL_STATES,
        "poll_url": f"/wow/runs/{run_row['run_id']}/manifest",
        "reused": reused,
        "can_execute": False,
    }


@app.get("/wow/runs/{run_id}")
def get_run_state(run_id: str) -> dict[str, Any]:
    client = get_client()
    run_row = repository.get_run(client, run_id)
    if run_row is None:
        raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND"})
    return {
        "run_id": run_row["run_id"],
        "status": run_row["status"],
        "stage": run_row["stage"],
        "terminal": run_row["status"] in RUN_TERMINAL_STATES,
        "can_execute": False,
    }


@app.get("/wow/runs/{run_id}/manifest")
def get_manifest(run_id: str) -> dict[str, Any]:
    client = get_client()
    run_row = repository.get_run(client, run_id)
    if run_row is None:
        raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND"})

    status = run_row["status"]
    candidates = repository.list_run_candidates(client, run_id)

    if status not in RUN_TERMINAL_STATES:
        jobs = repository.list_required_jobs(client, run_id)
        counts = {"queued": 0, "running": 0, "blocked": 0, "timed_out": 0}
        for job in jobs:
            bucket = _JOB_STATUS_TO_MANIFEST_BUCKET.get(job["status"])
            if bucket:
                counts[bucket] += 1
        terminal_candidates = [c for c in candidates if c.get("terminal_label")]
        return {
            "run_id": run_id,
            "status": status,
            "terminal": False,
            "stage": run_row["stage"],
            "rows_discovered": len(candidates),
            "rows_terminal": len(terminal_candidates),
            "rows_pending": len(candidates) - len(terminal_candidates),
            "jobs": counts,
            "can_execute": False,
        }

    balanced = run_row["rows_in"] == (
        run_row["rows_completed"] + run_row["rows_held"] + run_row["rows_rejected"]
    )
    return {
        "run_id": run_id,
        "status": status,
        "terminal": True,
        "reconciliation": {
            "rows_in": run_row["rows_in"],
            "rows_completed": run_row["rows_completed"],
            "rows_held": run_row["rows_held"],
            "rows_rejected": run_row["rows_rejected"],
            "balanced": balanced,
        },
        "candidates": candidates,
        "can_execute": False,
    }


@app.get("/wow/runs/{run_id}/audit")
def get_audit(run_id: str) -> dict[str, Any]:
    client = get_client()
    result = (
        client.table("wow_agent_audit_events")
        .select("*")
        .eq("run_id", run_id)
        .order("created_at")
        .execute()
    )
    return {"run_id": run_id, "events": result.data or [], "can_execute": False}


@app.post("/wow/runs/{run_id}/cancel")
def cancel_run(run_id: str) -> dict[str, Any]:
    """Administrative dry-run cancellation only (packet section 9)."""
    client = get_client()
    run_row = repository.get_run(client, run_id)
    if run_row is None:
        raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND"})
    if run_row["status"] in RUN_TERMINAL_STATES:
        return {"run_id": run_id, "status": run_row["status"], "terminal": True, "can_execute": False}

    result = repository.transition_run(
        client, run_id, expected_status=run_row["status"], next_status="CANCELED", stage="CANCELED",
    )
    if not result.applied:
        # Lost a race with another transition; report actual current state
        # rather than claim a cancel that didn't happen.
        current = repository.get_run(client, run_id)
        return {
            "run_id": run_id, "status": current["status"],
            "terminal": current["status"] in RUN_TERMINAL_STATES, "can_execute": False,
        }

    repository.record_audit_event(client, event_type="RUN_CANCELED", actor="wow.agent-runtime-api", run_id=run_id)
    return {"run_id": run_id, "status": "CANCELED", "terminal": True, "can_execute": False}
