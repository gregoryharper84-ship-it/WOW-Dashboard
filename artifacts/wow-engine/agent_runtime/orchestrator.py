"""Durable job queuing and run start (packet section 5, 9). Ported from
PR #33 (feature/wow-agent-runtime-v1) during the convergence pass, adapted to
this module's repository (explicit client, public schema) instead of
PR #33's raw-psycopg JobRepository.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from agent_runtime import repository
from agent_runtime.idempotency import input_hash as canonical_hash
from agent_runtime.registry import worker_spec
from agent_runtime.schemas import WorkerJobEnvelope


class Orchestrator:
    """Durable WOW scheduler. Queueing is idempotent; execution remains
    dry-run only — can_execute is hardcoded False on every envelope."""

    def __init__(self, client: Any):
        self.client = client

    def queue_worker(
        self, *, run_id: str, candidate_id: Optional[str], worker_id: str,
        evidence_snapshot_id: Optional[str], as_of: datetime, payload: dict[str, Any],
        required: bool = True,
    ) -> dict[str, Any]:
        spec = worker_spec(worker_id)
        job_input_hash = canonical_hash({
            "evidence_snapshot_id": evidence_snapshot_id, "payload": payload, "worker_version": spec.worker_version,
        })
        job, created = repository.enqueue_job(
            self.client, run_id=run_id, candidate_id=candidate_id, worker_id=worker_id,
            worker_version=spec.worker_version, idempotency_key=job_input_hash,
            required=required, input_hash=job_input_hash,
        )
        # Only dispatch to Celery when this call actually created the job row
        # — otherwise a retried queue_worker() call (or two coordinator
        # instances racing) would double-dispatch the same job onto the broker.
        if created:
            from agent_runtime.durable_runner import execute_durable

            envelope = WorkerJobEnvelope(
                run_id=run_id, job_id=str(job["job_id"]), candidate_id=candidate_id,
                worker_id=worker_id, worker_version=spec.worker_version, required=required,
                evidence_snapshot_id=evidence_snapshot_id,
                as_of=as_of.isoformat() if isinstance(as_of, datetime) else str(as_of),
                input_hash=job_input_hash, payload=payload,
            )
            execute_durable.apply_async(
                args=[envelope.model_dump(mode="json")],
                task_id=str(job["job_id"]),
                queue="wow-agent",
                soft_time_limit=spec.timeout_seconds,
                time_limit=spec.timeout_seconds + 5,
            )
        return {**job, "can_execute": False}

    def start_run(self, *, run: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        """Start a CREATED run exactly once and enqueue the discovery boundary."""
        current = str(run.get("status"))
        if current != "CREATED":
            return {"run": run, "job": None, "started": False, "can_execute": False}
        run_id = str(run["run_id"])
        try:
            result = repository.transition_run(self.client, run_id, expected_status="CREATED", next_status="VALIDATING_REQUEST", stage="VALIDATING_REQUEST")
            if not result.applied:
                latest = repository.get_run(self.client, run_id) or run
                return {"run": latest, "job": None, "started": False, "can_execute": False}
            run = result.row or run

            # Transition to DISCOVERY_QUEUED *before* dispatching the job, not
            # after: under Celery's eager test mode (and, in principle, an
            # extremely fast real worker) apply_async() below can run the
            # entire task — including the coordinator's own
            # DISCOVERY_QUEUED->DISCOVERY_RUNNING transition — synchronously,
            # before this function's next line would otherwise get a chance
            # to run. The run must already be in the state the coordinator
            # expects before any worker can possibly observe it.
            result = repository.transition_run(self.client, run_id, expected_status="VALIDATING_REQUEST", next_status="DISCOVERY_QUEUED", stage="DISCOVERY_QUEUED")
            if not result.applied:
                latest = repository.get_run(self.client, run_id) or run
                return {"run": latest, "job": None, "started": False, "can_execute": False}
            run = result.row or run

            as_of_raw = request.get("as_of")
            as_of = as_of_raw if isinstance(as_of_raw, datetime) else datetime.fromisoformat(str(as_of_raw).replace("Z", "+00:00"))
            job = self.queue_worker(
                run_id=run_id, candidate_id=None, worker_id="wow.parallel-discovery-router",
                evidence_snapshot_id=None, as_of=as_of,
                payload={
                    "rows": request.get("candidate_inputs") or [],
                    "discovery_enabled": bool(request.get("discovery_enabled", True)),
                    "lanes": request.get("lanes") or [],
                    "sports": request.get("sports") or [],
                },
                required=True,
            )
            latest = repository.get_run(self.client, run_id) or run
            return {"run": latest, "job": job, "started": True, "can_execute": False}
        except Exception:
            latest = repository.get_run(self.client, run_id)
            if latest and latest.get("status") == "VALIDATING_REQUEST":
                try:
                    repository.transition_run(self.client, run_id, expected_status="VALIDATING_REQUEST", next_status="FAILED", stage="DISCOVERY_QUEUE_FAILED")
                except Exception:
                    pass
            raise
