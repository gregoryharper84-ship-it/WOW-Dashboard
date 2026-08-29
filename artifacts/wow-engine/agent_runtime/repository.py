"""Supabase-backed persistence for the agent runtime (packet section 8 tables,
minus everything Phase 0's overlap audit found already existing elsewhere).

Every function takes an explicit `client` argument rather than reaching for a
module-level global — callers get a real client from ledger.get_client() in
production and a fake PostgREST-shaped client in tests (see
test_agent_runtime_repository.py). This mirrors the injectable-seam pattern
already used throughout wow-engine (set_fitted_params_provider, set_persist_fn)
without adding another global to reset between tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from agent_runtime.state_machine import assert_job_transition, assert_run_transition


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class CasTransitionResult:
    """Result of a compare-and-set job-state transition attempt."""
    applied: bool
    row: Optional[dict[str, Any]]


# ── Runs ─────────────────────────────────────────────────────────────────────

def find_existing_run(client: Any, *, idempotency_key: str, request_hash: str) -> Optional[dict[str, Any]]:
    result = (
        client.table("wow_agent_runs")
        .select("*")
        .eq("idempotency_key", idempotency_key)
        .eq("request_hash", request_hash)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return dict(rows[0]) if rows else None


def create_run(
    client: Any,
    *,
    idempotency_key: str,
    request_hash: str,
    run_type: str,
    requested_as_of: str,
    user_timezone: str,
    governance_version: str,
) -> tuple[dict[str, Any], bool]:
    """Returns (run_row, reused). Idempotent: a repeated call with the same
    (idempotency_key, request_hash) returns the existing run rather than
    inserting a duplicate, whether found before or after a losing race on the
    database's unique constraint."""
    existing = find_existing_run(client, idempotency_key=idempotency_key, request_hash=request_hash)
    if existing is not None:
        return existing, True

    try:
        result = (
            client.table("wow_agent_runs")
            .insert({
                "idempotency_key": idempotency_key,
                "request_hash": request_hash,
                "run_type": run_type,
                "requested_as_of": requested_as_of,
                "user_timezone": user_timezone,
                "status": "CREATED",
                "stage": "INTAKE",
                "governance_version": governance_version,
            })
            .execute()
        )
        rows = result.data or []
        if not rows:
            raise RuntimeError("wow_agent_runs insert returned no row")
        return dict(rows[0]), False
    except Exception:
        # Likely a losing race against the unique (idempotency_key,
        # request_hash) constraint — the winner's row is now readable.
        existing = find_existing_run(client, idempotency_key=idempotency_key, request_hash=request_hash)
        if existing is not None:
            return existing, True
        raise


def get_run(client: Any, run_id: str) -> Optional[dict[str, Any]]:
    result = client.table("wow_agent_runs").select("*").eq("run_id", run_id).limit(1).execute()
    rows = result.data or []
    return dict(rows[0]) if rows else None


def transition_run(client: Any, run_id: str, *, expected_status: str, next_status: str, stage: str) -> CasTransitionResult:
    assert_run_transition(expected_status, next_status)
    fields: dict[str, Any] = {"status": next_status, "stage": stage, "updated_at": _now_iso()}
    if next_status in ("COMPLETED", "COMPLETED_WITH_BLOCKERS", "FAILED", "CANCELED"):
        fields["completed_at"] = _now_iso()
    result = (
        client.table("wow_agent_runs")
        .update(fields)
        .eq("run_id", run_id)
        .eq("status", expected_status)
        .execute()
    )
    rows = result.data or []
    return CasTransitionResult(applied=bool(rows), row=dict(rows[0]) if rows else None)


def set_run_row_counts(client: Any, run_id: str, *, rows_in: int, rows_completed: int, rows_held: int, rows_rejected: int, reconciliation_status: str) -> None:
    client.table("wow_agent_runs").update({
        "rows_in": rows_in,
        "rows_completed": rows_completed,
        "rows_held": rows_held,
        "rows_rejected": rows_rejected,
        "reconciliation_status": reconciliation_status,
        "updated_at": _now_iso(),
    }).eq("run_id", run_id).execute()


# ── Candidates ───────────────────────────────────────────────────────────────

def insert_candidate(client: Any, *, run_id: str, canonical_key: str, sport: str, participant: str, market_family: str, period: str, **extra: Any) -> dict[str, Any]:
    payload = {
        "run_id": run_id, "canonical_key": canonical_key, "sport": sport,
        "participant": participant, "market_family": market_family, "period": period,
        **extra,
    }
    result = client.table("wow_agent_run_candidates").insert(payload).execute()
    rows = result.data or []
    if not rows:
        raise RuntimeError("wow_agent_run_candidates insert returned no row")
    return dict(rows[0])


def set_candidate_terminal(client: Any, candidate_id: str, *, terminal_label: str, terminal_ceiling: str, blockers: list[str], controlling_worker_id: Optional[str] = None) -> None:
    fields: dict[str, Any] = {
        "terminal_label": terminal_label,
        "terminal_ceiling": terminal_ceiling,
        "blockers": blockers,
    }
    if controlling_worker_id is not None:
        fields["controlling_worker_id"] = controlling_worker_id
    client.table("wow_agent_run_candidates").update(fields).eq("candidate_id", candidate_id).execute()


def list_run_candidates(client: Any, run_id: str) -> list[dict[str, Any]]:
    result = client.table("wow_agent_run_candidates").select("*").eq("run_id", run_id).execute()
    return [dict(row) for row in (result.data or [])]


# ── Jobs ─────────────────────────────────────────────────────────────────────

def enqueue_job(client: Any, *, run_id: str, candidate_id: Optional[str], worker_id: str, worker_version: str, idempotency_key: str, required: bool, input_hash: str) -> dict[str, Any]:
    result = (
        client.table("wow_agent_jobs")
        .insert({
            "run_id": run_id, "candidate_id": candidate_id, "worker_id": worker_id,
            "worker_version": worker_version, "idempotency_key": idempotency_key,
            "status": "QUEUED", "required": required, "input_hash": input_hash,
        })
        .execute()
    )
    rows = result.data or []
    if not rows:
        raise RuntimeError("wow_agent_jobs insert returned no row")
    return dict(rows[0])


def try_transition_job(client: Any, job_id: str, *, expected_status: str, next_status: str, ceiling: Optional[str] = None, blockers: Optional[list[str]] = None, output_hash: Optional[str] = None, error_code: Optional[str] = None) -> CasTransitionResult:
    """Compare-and-set: exactly one updated row means the transition was
    ours; zero means a duplicate/racing worker already moved this job past
    the expected state, or it never was in that state. Callers must treat
    applied=False as "not mine to act on", never retry it as an error."""
    assert_job_transition(expected_status, next_status)
    fields: dict[str, Any] = {"status": next_status}
    if next_status == "RUNNING":
        fields["started_at"] = _now_iso()
        fields["heartbeat_at"] = _now_iso()
    if next_status in ("SUCCEEDED", "BLOCKED", "REJECTED", "TIMED_OUT", "DEAD_LETTERED", "CANCELED"):
        fields["completed_at"] = _now_iso()
    if ceiling is not None:
        fields["ceiling"] = ceiling
    if blockers is not None:
        fields["blockers"] = blockers
    if output_hash is not None:
        fields["output_hash"] = output_hash
    if error_code is not None:
        fields["error_code"] = error_code

    result = (
        client.table("wow_agent_jobs")
        .update(fields)
        .eq("job_id", job_id)
        .eq("status", expected_status)
        .execute()
    )
    rows = result.data or []
    return CasTransitionResult(applied=bool(rows), row=dict(rows[0]) if rows else None)


def list_required_jobs(client: Any, run_id: str, candidate_id: Optional[str] = None) -> list[dict[str, Any]]:
    query = client.table("wow_agent_jobs").select("*").eq("run_id", run_id).eq("required", True)
    if candidate_id is not None:
        query = query.eq("candidate_id", candidate_id)
    result = query.execute()
    return [dict(row) for row in (result.data or [])]


# ── Outputs and audit ────────────────────────────────────────────────────────

def record_job_output(client: Any, *, job_id: str, run_id: str, candidate_id: Optional[str], worker_id: str, worker_version: str, contract_version: str, output: dict[str, Any], output_hash: str, evidence_snapshot_id: Optional[str] = None) -> dict[str, Any]:
    result = (
        client.table("wow_agent_job_outputs")
        .insert({
            "job_id": job_id, "run_id": run_id, "candidate_id": candidate_id,
            "worker_id": worker_id, "worker_version": worker_version,
            "contract_version": contract_version, "output": output,
            "output_hash": output_hash, "evidence_snapshot_id": evidence_snapshot_id,
        })
        .execute()
    )
    rows = result.data or []
    if not rows:
        raise RuntimeError("wow_agent_job_outputs insert returned no row")
    return dict(rows[0])


def record_audit_event(client: Any, *, event_type: str, actor: str, run_id: Optional[str] = None, candidate_id: Optional[str] = None, job_id: Optional[str] = None, detail_redacted: Optional[dict[str, Any]] = None) -> None:
    client.table("wow_agent_audit_events").insert({
        "event_type": event_type, "actor": actor, "run_id": run_id,
        "candidate_id": candidate_id, "job_id": job_id,
        "detail_redacted": detail_redacted or {},
    }).execute()
