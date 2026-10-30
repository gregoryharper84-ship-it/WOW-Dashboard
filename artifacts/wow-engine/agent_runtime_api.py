"""Agent Runtime V1 API routes (packet section 9), Phase 1.

Registers directly onto api.py's shared FastAPI `app` object — api.py imports
this module last (see the comment at the bottom of api.py), so these routes
attach after every existing route is already registered. api_g11.py and
api_prod.py both copy `app`'s routes wholesale except the specific paths they
each override (/score-event, /governance, /score-prop), so anything
registered here propagates through both wrapper layers automatically without
either wrapper file changing.

POST /wow/runs starts real orchestration (Orchestrator.start_run) as of the
convergence pass with PR #33 (feature/wow-agent-runtime-v1): discovery
through the terminal reducer runs via the Celery worker
(agent_runtime/durable_runner.py) for any candidate whose lane isn't wired
to a real fitted model yet, terminalizing as MODEL_UNAVAILABLE rather than
inventing a probability — see agent_runtime/coordinator.py and
runner.py::_run_controlling_model.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from functools import lru_cache
from uuid import UUID
from typing import Any, Optional

from fastapi import Depends, Header, HTTPException

from api import _require_agent_runtime_api_key, app, get_client
from agent_runtime import idempotency, repository, schemas
from agent_runtime.orchestrator import Orchestrator
from agent_runtime.state_machine import RUN_TERMINAL_STATES
from ml_research_readiness import get_ml_research_readiness

GOVERNANCE_VERSION = "WOW-AGENT-RUNTIME-V1-PHASE1"
MAX_RUN_CANDIDATES = 32
MAX_IDEMPOTENCY_KEY_LENGTH = 128
MAX_RUN_CANDIDATE_JSON_BYTES = 65536
MAX_RUN_CANDIDATE_DEPTH = 12
MAX_RUN_CANDIDATE_NODES = 4096
MAX_RUN_ADMISSIONS_PER_MINUTE = 60
_SAFE_IDEMPOTENCY_KEY_RE = re.compile(r"\A[A-Za-z0-9._:-]+\Z")

_REQUEST_HASH_FIELDS = (
    "run_type", "as_of", "user_timezone", "lanes", "sports",
    "candidate_inputs", "discovery_enabled",
)

_JOB_STATUS_TO_MANIFEST_BUCKET = {
    "QUEUED": "queued", "RETRY_PENDING": "queued", "RUNNING": "running",
    "BLOCKED": "blocked", "TIMED_OUT": "timed_out",
}


def _validated_run_id(run_id: str) -> str:
    """Canonical UUID validation before any service-role query."""
    try:
        return str(UUID(str(run_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=422, detail={"code": "RUN_ID_INVALID", "can_execute": False}) from exc


def _candidate_shape_bounded(value: Any) -> bool:
    """Iterative pre-serialization depth/node budget; no recursive traversal."""
    stack = [(value, 0)]
    visited = 0
    while stack:
        item, depth = stack.pop()
        visited += 1
        if visited > MAX_RUN_CANDIDATE_NODES or depth > MAX_RUN_CANDIDATE_DEPTH:
            return False
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
    return True


_ADMISSION_WINDOW_MS = 60_000
_ADMISSION_REDIS_KEY = "wow:agent-runtime:admissions:sliding-v1"
# Deployment-wide bound for ONE independently authenticated runtime principal.
# This API does not identify multiple callers behind the same bearer; never
# claim per-caller isolation. Do not distribute the bearer to unrelated tenants.
# Redis restart/eviction resets this volatile window; it is not a durable quota.
# One atomic Redis operation: use server time, enforce a rolling 60-second
# window, and count a repeated (idempotency key, request hash) only once.
# No payload, raw key or bearer is stored in Redis.
_ADMISSION_LUA = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local window = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
if redis.call('ZSCORE', KEYS[1], ARGV[3]) then return 2 end
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[1]) then return 0 end
redis.call('ZADD', KEYS[1], now, ARGV[3])
redis.call('PEXPIRE', KEYS[1], window * 2)
return 1
"""


@lru_cache(maxsize=1)
def _admission_redis(redis_url: str) -> Any:
    """Reuse a process-local pool keyed by REDIS_URL; rotation changes cache key.

    One deployment/one scoped principal; no cross-tenant pool multiplexing.
    """
    import redis
    return redis.Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)


def _check_admission_rate_limit(*, idempotency_key: str, request_hash: str) -> None:
    """Fail-closed global sliding-window admission on the existing Redis."""
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        raise HTTPException(status_code=503, detail={"code": "AGENT_RUNTIME_ADMISSION_GATE_UNAVAILABLE", "can_execute": False})
    member = hashlib.sha256((idempotency_key + "\x00" + request_hash).encode("utf-8")).hexdigest()
    try:
        allowed = _admission_redis(redis_url).eval(
            _ADMISSION_LUA, 1, _ADMISSION_REDIS_KEY,
            MAX_RUN_ADMISSIONS_PER_MINUTE, _ADMISSION_WINDOW_MS, member,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"code": "AGENT_RUNTIME_ADMISSION_GATE_UNAVAILABLE", "can_execute": False}) from exc
    if int(allowed) == 0:
        raise HTTPException(status_code=429, detail={"code": "AGENT_RUNTIME_ADMISSION_RATE_LIMITED", "can_execute": False})


@app.get("/health/live")
def health_live() -> dict[str, Any]:
    """Process is alive; no dependency calls (packet section 9)."""
    return {"status": "ok", "can_execute": False}


@app.get("/health/ready")
def health_ready() -> dict[str, Any]:
    """Database, queue, and code<->DB worker-registry parity — packet
    section 9. All three fail closed: an unreachable dependency or a
    registry mismatch means not ready, never a partial or optimistic pass."""
    client = None
    try:
        client = get_client()
        client.table("wow_agent_runs").select("run_id").limit(1).execute()
        database_ok = True
    except Exception:
        database_ok = False

    registry_ok = False
    if database_ok and client is not None:
        try:
            registry_ok = repository.registry_matches(client)
        except Exception:
            registry_ok = False

    queue_ok = False
    try:
        import redis

        redis_url = os.environ.get("REDIS_URL")
        if redis_url:
            queue_ok = bool(redis.Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1).ping())
    except Exception:
        queue_ok = False

    ready = database_ok and queue_ok and registry_ok
    body = {
        "status": "ok" if ready else "not_ready",
        "database": "ok" if database_ok else "unreachable",
        "queue": "ok" if queue_ok else "unreachable",
        "worker_registry": "ok" if registry_ok else "mismatch_or_unreachable",
        "can_execute": False,
    }
    if not ready:
        raise HTTPException(status_code=503, detail=body)
    return body


@app.get("/ml-research-readiness")
def ml_research_readiness() -> dict[str, Any]:
    """Return the six-sport v16 ML research/model-readiness registry.

    This is a transparency/read-only endpoint. It is deliberately outside
    terminal reduction and cannot publish a probability or authorize action.
    """
    return get_ml_research_readiness()


@app.get("/ml-research-readiness/{sport}")
def ml_research_readiness_for_sport(sport: str) -> dict[str, Any]:
    """Return one supported ML readiness profile, or 404 for unsupported."""
    try:
        return get_ml_research_readiness(sport)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "ML_RESEARCH_READINESS_SPORT_UNSUPPORTED",
                "sport": sport,
                "readiness_is_terminal_gate": False,
                "probability_publishable_from_readiness": False,
                "can_execute": False,
            },
        )


@app.post("/wow/runs", status_code=202, dependencies=[Depends(_require_agent_runtime_api_key)])
def create_run(
    req: schemas.RunCreateRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    if req.can_execute:
        raise HTTPException(
            status_code=422,
            detail={"code": "EXECUTION_PROHIBITED", "can_execute": False},
        )
    # Pydantic enforces list[dict] for HTTP calls; also fail closed if a
    # direct/internal caller bypasses request-model validation.
    if not isinstance(req.candidate_inputs, list) or len(req.candidate_inputs) > MAX_RUN_CANDIDATES:
        raise HTTPException(
            status_code=422,
            detail={"code": "RUN_BATCH_LIMIT_EXCEEDED", "max_candidates": MAX_RUN_CANDIDATES, "can_execute": False},
        )
    # Bound nesting before serialization to avoid adversarial recursion.
    if not _candidate_shape_bounded(req.candidate_inputs):
        raise HTTPException(status_code=422, detail={"code": "RUN_CANDIDATE_COMPLEXITY_EXCEEDED", "can_execute": False})
    # Bound each admitted batch as well as its row count. Never send a
    # malformed or oversized candidate payload to the service-role DB/Celery.
    try:
        candidate_bytes = len(json.dumps(
            req.candidate_inputs, ensure_ascii=False, allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8"))
    except (TypeError, ValueError, OverflowError, RecursionError):
        candidate_bytes = MAX_RUN_CANDIDATE_JSON_BYTES + 1
    if candidate_bytes > MAX_RUN_CANDIDATE_JSON_BYTES:
        raise HTTPException(
            status_code=422,
            detail={"code": "RUN_CANDIDATE_PAYLOAD_TOO_LARGE",
                    "max_bytes": MAX_RUN_CANDIDATE_JSON_BYTES, "can_execute": False},
        )
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(
            status_code=400,
            detail={"code": "IDEMPOTENCY_KEY_REQUIRED", "message": "Idempotency-Key header is required."},
        )

    if len(idempotency_key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise HTTPException(
            status_code=422,
            detail={"code": "IDEMPOTENCY_KEY_TOO_LONG", "max_length": MAX_IDEMPOTENCY_KEY_LENGTH, "can_execute": False},
        )

    # Reject control/whitespace and non-ASCII header values before persistence.
    if _SAFE_IDEMPOTENCY_KEY_RE.fullmatch(idempotency_key) is None:
        raise HTTPException(
            status_code=422,
            detail={"code": "IDEMPOTENCY_KEY_INVALID_CHARACTERS", "can_execute": False},
        )

    request_hash = idempotency.compute_request_hash(
        {field: getattr(req, field) for field in _REQUEST_HASH_FIELDS}
    )

    _check_admission_rate_limit(idempotency_key=idempotency_key, request_hash=request_hash)
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
        try:
            started = Orchestrator(client).start_run(
                run=run_row,
                request={field: getattr(req, field) for field in _REQUEST_HASH_FIELDS},
            )
            run_row = started["run"]
        except Exception as exc:
            latest = repository.get_run(client, str(run_row["run_id"])) or run_row
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "AGENT_RUNTIME_START_FAILED", "run_id": str(run_row["run_id"]),
                    "status": latest.get("status"), "error": type(exc).__name__,
                    "probability_publishable": False, "can_execute": False,
                },
            ) from exc

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


@app.get("/wow/runs/{run_id}", dependencies=[Depends(_require_agent_runtime_api_key)])
def get_run_state(run_id: str) -> dict[str, Any]:
    run_id = _validated_run_id(run_id)
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


@app.get("/wow/runs/{run_id}/manifest", dependencies=[Depends(_require_agent_runtime_api_key)])
def get_manifest(run_id: str) -> dict[str, Any]:
    run_id = _validated_run_id(run_id)
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


@app.get("/wow/runs/{run_id}/audit", dependencies=[Depends(_require_agent_runtime_api_key)])
def get_audit(run_id: str) -> dict[str, Any]:
    run_id = _validated_run_id(run_id)
    client = get_client()
    result = (
        client.table("wow_agent_audit_events")
        .select("*")
        .eq("run_id", run_id)
        .order("created_at")
        .execute()
    )
    return {"run_id": run_id, "events": result.data or [], "can_execute": False}


@app.post("/wow/runs/{run_id}/cancel", dependencies=[Depends(_require_agent_runtime_api_key)])
def cancel_run(run_id: str) -> dict[str, Any]:
    """Administrative dry-run cancellation only (packet section 9)."""
    run_id = _validated_run_id(run_id)
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
