"""The durable Celery task: claim -> execute -> complete -> coordinate.

Scout + Research enforcement is activated here so every durable governed run
uses the mandatory pre-specialist barrier. Execution remains permanently off.
"""
from __future__ import annotations

from typing import Any

from billiard.exceptions import SoftTimeLimitExceeded

from agent_runtime import repository
from agent_runtime.queue import celery_app
from agent_runtime.registry import worker_spec
from agent_runtime.runner_scout_research import TRANSIENT_CODES, _terminal_output, execute_envelope
from agent_runtime.schemas import WorkerJobEnvelope
from agent_runtime.state_machine import JOB_TERMINAL_STATES


def run_durable_body(client: Any, envelope: dict) -> tuple[dict, Exception | None]:
    try:
        env = WorkerJobEnvelope.model_validate(envelope)
    except Exception as exc:
        return {"status": "BLOCKED", "error_code": "WORKER_CONTRACT_INVALID", "error": type(exc).__name__, "can_execute": False}, None

    try:
        spec = worker_spec(env.worker_id)
        lease_seconds = max(60, spec.timeout_seconds * 2 + 15)
    except Exception:
        spec = None
        lease_seconds = 180

    if not repository.claim_job(client, env.job_id, lease_seconds=lease_seconds):
        row = repository.get_job(client, env.job_id)
        if row is None:
            return {"status": "BLOCKED", "error_code": "JOB_NOT_FOUND", "job_id": env.job_id, "can_execute": False}, None
        if row["status"] in JOB_TERMINAL_STATES:
            return {"status": "DUPLICATE_DELIVERY_IGNORED", "job_id": env.job_id, "terminal_status": row["status"], "can_execute": False}, None
        return {}, RuntimeError("JOB_NOT_CLAIMABLE_YET")

    from agent_runtime.coordinator_scout_research import Coordinator
    coordinator = Coordinator(client)
    try:
        coordinator.on_job_started(env.worker_id, env.run_id)
    except Exception:
        pass

    try:
        if spec is None:
            out = _terminal_output(env, "DEAD_LETTERED", "RESEARCH_INTEREST", ["UNKNOWN_WORKER"], {"error": "UNKNOWN_WORKER"})
        else:
            out = execute_envelope(env)
    except SoftTimeLimitExceeded:
        out = _terminal_output(env, "TIMED_OUT", "RESEARCH_INTEREST", ["WORKER_TIMED_OUT"], {})
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        if spec is not None and code in TRANSIENT_CODES:
            if not repository.mark_retry_pending(client, env.job_id, error_code=code, error_detail={"error": type(exc).__name__}):
                out = _terminal_output(env, "DEAD_LETTERED", "RESEARCH_INTEREST", ["RETRY_STATE_TRANSITION_FAILED"], {"error": type(exc).__name__})
            else:
                return {}, exc
        else:
            out = _terminal_output(env, "DEAD_LETTERED", "RESEARCH_INTEREST", ["WORKER_DEAD_LETTERED"], {"error": type(exc).__name__, "code": code})

    payload = out.model_dump(mode="json")
    applied = repository.complete_job(
        client, job_id=env.job_id, run_id=env.run_id, candidate_id=env.candidate_id,
        worker_id=env.worker_id, worker_version=env.worker_version, contract_version=payload.get("contract_version", "wow.agent-output.v1"),
        evidence_snapshot_id=env.evidence_snapshot_id, output=payload.get("output") or {}, output_hash=payload["output_hash"],
        status=payload["status"], ceiling=payload.get("ceiling"), blockers=payload.get("blockers") or [],
        error_code=payload.get("error_code"),
    )
    if applied:
        try:
            coordinator.on_job_terminal(env, payload)
        except Exception as exc:
            run = repository.get_run(client, env.run_id) or {}
            current = str(run.get("status") or "")
            if current not in {"COMPLETED", "COMPLETED_WITH_BLOCKERS", "FAILED", "CANCELED", "RECONCILING"}:
                try:
                    repository.transition_run(client, env.run_id, expected_status=current, next_status="FAILED", stage=f"CONTINUATION_FAILED:{type(exc).__name__}")
                except Exception:
                    pass
            return {**payload, "continuation_status": "FAILED_CLOSED", "continuation_error": type(exc).__name__, "can_execute": False}, None
    return payload, None


@celery_app.task(bind=True, name="wow.agent_runtime.execute_durable", acks_late=True)
def execute_durable(self, envelope: dict):
    from ledger import get_client

    client = get_client()
    payload, retry_exc = run_durable_body(client, envelope)
    if retry_exc is not None:
        raise self.retry(exc=retry_exc, countdown=min(60, 2 ** self.request.retries), max_retries=1000)
    return payload
