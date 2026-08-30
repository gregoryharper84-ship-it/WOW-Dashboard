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

from agent_runtime.registry import WORKERS
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


def reconcile_run(client: Any, run_id: str) -> dict[str, Any]:
    """Recompute and persist a run's row counts from its candidates' current
    terminal_ceiling, and report rows_pending (candidates with no terminal
    ceiling yet) so the coordinator knows whether the run can finalize.
    Adopted from PR #33's job_store.py::reconcile_run() during the
    convergence pass, built on this module's own reconciliation classifier
    instead of duplicating the bucket logic in SQL."""
    from agent_runtime.reconciliation import classify_ceiling

    candidates = list_run_candidates(client, run_id)
    completed = held = rejected = pending = 0
    for candidate in candidates:
        ceiling = candidate.get("terminal_ceiling")
        if ceiling is None:
            pending += 1
            continue
        bucket = classify_ceiling(ceiling)
        if bucket == "completed":
            completed += 1
        elif bucket == "rejected":
            rejected += 1
        else:
            held += 1

    rows_in = len(candidates)
    balanced = rows_in == completed + held + rejected + pending
    set_run_row_counts(
        client, run_id, rows_in=rows_in, rows_completed=completed, rows_held=held,
        rows_rejected=rejected, reconciliation_status="BALANCED" if balanced else "UNBALANCED",
    )
    return {
        "rows_in": rows_in, "rows_completed": completed, "rows_held": held,
        "rows_rejected": rejected, "rows_pending": pending, "balanced": balanced,
    }


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


def set_candidate_terminal(client: Any, candidate_id: str, *, terminal_label: str, terminal_ceiling: str, blockers: list[str], controlling_worker_id: Optional[str] = None) -> bool:
    """Compare-and-set: only applies if the candidate has no terminal_label
    yet. Returns False, not an error, if it was already set — Celery's
    at-least-once delivery means the coordinator can be asked to terminalize
    the same candidate more than once, and the first decision must win, not
    the last (packet section 1: no downstream result erases an upstream
    block)."""
    fields: dict[str, Any] = {
        "terminal_label": terminal_label,
        "terminal_ceiling": terminal_ceiling,
        "blockers": blockers,
    }
    if controlling_worker_id is not None:
        fields["controlling_worker_id"] = controlling_worker_id
    result = (
        client.table("wow_agent_run_candidates")
        .update(fields)
        .eq("candidate_id", candidate_id)
        .is_("terminal_label", None)
        .execute()
    )
    return bool(result.data)


def list_run_candidates(client: Any, run_id: str) -> list[dict[str, Any]]:
    result = client.table("wow_agent_run_candidates").select("*").eq("run_id", run_id).execute()
    return [dict(row) for row in (result.data or [])]


def get_candidate(client: Any, candidate_id: str) -> Optional[dict[str, Any]]:
    result = client.table("wow_agent_run_candidates").select("*").eq("candidate_id", candidate_id).limit(1).execute()
    rows = result.data or []
    return dict(rows[0]) if rows else None


def upsert_candidates(client: Any, run_id: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Insert-or-update by (run_id, canonical_key) — a repeated canonical key
    within one run updates candidate_payload rather than erroring, matching
    the unique constraint on wow_agent_run_candidates."""
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        canonical_key = str(candidate.get("canonical_key") or "")
        payload = {
            "run_id": run_id,
            "canonical_key": canonical_key,
            "sport": str(candidate.get("sport") or "").upper(),
            "league": candidate.get("league"),
            "official_event_id": candidate.get("official_event_id"),
            "participant": candidate.get("participant"),
            "opponent": candidate.get("opponent"),
            "market_family": str(candidate.get("market_family") or "").upper(),
            "stat_family": candidate.get("stat_family"),
            "period": str(candidate.get("period") or "FULL_GAME").upper(),
            "exact_line": candidate.get("exact_line"),
            "side": candidate.get("side"),
            "settlement_operator": candidate.get("settlement_operator"),
            "controlling_worker_id": candidate.get("controlling_worker_id") or "wow.controlling-model",
            "candidate_payload": candidate,
        }
        result = (
            client.table("wow_agent_run_candidates")
            .upsert(payload, on_conflict="run_id,canonical_key")
            .execute()
        )
        row_data = result.data or []
        if row_data:
            rows.append(dict(row_data[0]))
    return rows


# ── Jobs ─────────────────────────────────────────────────────────────────────

def get_job(client: Any, job_id: str) -> Optional[dict[str, Any]]:
    result = client.table("wow_agent_jobs").select("*").eq("job_id", job_id).limit(1).execute()
    rows = result.data or []
    return dict(rows[0]) if rows else None


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def claim_job(client: Any, job_id: str, *, lease_seconds: int = 180) -> bool:
    """Lease-based claim: a QUEUED/RETRY_PENDING job is always claimable; a
    RUNNING job is claimable only if its heartbeat predates now() by more
    than lease_seconds (a worker that died mid-job without heartbeating).
    Adopted from PR #33's job_store.py::claim() during the convergence pass,
    as a read-then-CAS-update pair rather than one raw SQL statement with an
    OR condition — the CAS update's own eq(status, <value just read>) still
    guarantees at most one caller wins even if two callers both consider the
    same stale RUNNING job claimable at the same instant."""
    row = get_job(client, job_id)
    if row is None:
        return False
    status = row.get("status")
    if status not in ("QUEUED", "RETRY_PENDING"):
        if status != "RUNNING":
            return False
        heartbeat = _parse_ts(row.get("heartbeat_at") or row.get("started_at") or row.get("queued_at"))
        if heartbeat is None or (datetime.now(timezone.utc) - heartbeat).total_seconds() < lease_seconds:
            return False

    result = (
        client.table("wow_agent_jobs")
        .update({
            "status": "RUNNING",
            "started_at": row.get("started_at") or _now_iso(),
            "heartbeat_at": _now_iso(),
            "attempt": int(row.get("attempt") or 0) + 1,
        })
        .eq("job_id", job_id)
        .eq("status", status)
        .execute()
    )
    return bool(result.data)


def heartbeat_job(client: Any, job_id: str) -> bool:
    result = (
        client.table("wow_agent_jobs")
        .update({"heartbeat_at": _now_iso()})
        .eq("job_id", job_id)
        .eq("status", "RUNNING")
        .execute()
    )
    return bool(result.data)


def mark_retry_pending(client: Any, job_id: str, *, error_code: str, error_detail: Optional[dict[str, Any]] = None) -> bool:
    result = (
        client.table("wow_agent_jobs")
        .update({"status": "RETRY_PENDING", "error_code": error_code, "error_detail_redacted": error_detail or {}, "heartbeat_at": _now_iso()})
        .eq("job_id", job_id)
        .eq("status", "RUNNING")
        .execute()
    )
    return bool(result.data)


def find_existing_job(client: Any, idempotency_key: str) -> Optional[dict[str, Any]]:
    result = client.table("wow_agent_jobs").select("*").eq("idempotency_key", idempotency_key).limit(1).execute()
    rows = result.data or []
    return dict(rows[0]) if rows else None


def enqueue_job(client: Any, *, run_id: str, candidate_id: Optional[str], worker_id: str, worker_version: str, idempotency_key: str, required: bool, input_hash: str) -> tuple[dict[str, Any], bool]:
    """Returns (job_row, created). Idempotent on idempotency_key, same
    select-then-insert-with-race-recovery pattern as create_run() — a
    repeated call (a retried orchestrator step, or a race between two
    coordinator instances) returns the existing job rather than creating a
    duplicate or hitting the unique-constraint violation a plain insert
    would raise on the real database."""
    existing = find_existing_job(client, idempotency_key)
    if existing is not None:
        return existing, False

    try:
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
        return dict(rows[0]), True
    except Exception:
        existing = find_existing_job(client, idempotency_key)
        if existing is not None:
            return existing, False
        raise


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


def list_jobs(client: Any, run_id: str, *, candidate_id: Optional[str] = None, worker_id: Optional[str] = None) -> list[dict[str, Any]]:
    """Unfiltered-by-required job listing, for the coordinator's per-stage
    all-terminal checks (which need to see every job for a worker_id, not
    only required ones)."""
    query = client.table("wow_agent_jobs").select("*").eq("run_id", run_id)
    if candidate_id is not None:
        query = query.eq("candidate_id", candidate_id)
    if worker_id is not None:
        query = query.eq("worker_id", worker_id)
    result = query.execute()
    return [dict(row) for row in (result.data or [])]


# ── Outputs and audit ────────────────────────────────────────────────────────

def get_output(client: Any, job_id: str) -> Optional[dict[str, Any]]:
    result = client.table("wow_agent_job_outputs").select("*").eq("job_id", job_id).limit(1).execute()
    rows = result.data or []
    return dict(rows[0]) if rows else None


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


def record_terminal_decision(
    client: Any, *, run_id: str, candidate_id: str, final_terminal_ceiling: str, terminal_label: str,
    controlling_worker_id: Optional[str], probability_publishable: bool, blockers: list[str],
    reducer_version: str, decision_hash: str,
) -> None:
    """Append-only immutable decision record — separate from the
    terminal_label/terminal_ceiling columns on wow_agent_run_candidates,
    which a later UPDATE could in principle overwrite. unique(candidate_id)
    on wow_agent_terminal_decisions means a second call for the same
    candidate is a harmless no-op-on-conflict at the database layer; callers
    should only reach this after set_candidate_terminal()'s own CAS
    succeeded, so in practice this is always a first write."""
    client.table("wow_agent_terminal_decisions").insert({
        "run_id": run_id, "candidate_id": candidate_id, "final_terminal_ceiling": final_terminal_ceiling,
        "terminal_label": terminal_label, "controlling_worker_id": controlling_worker_id,
        "probability_publishable": probability_publishable, "blockers": blockers,
        "reducer_version": reducer_version, "decision_hash": decision_hash,
    }).execute()


def complete_job(
    client: Any, *, job_id: str, run_id: str, candidate_id: Optional[str],
    worker_id: str, worker_version: str, contract_version: str,
    evidence_snapshot_id: Optional[str], output: dict[str, Any], output_hash: str,
    status: str, ceiling: Optional[str], blockers: list[str], error_code: Optional[str] = None,
) -> bool:
    """Atomically record a job's terminal output and transition its status via
    the wow_agent_complete_job RPC (agent_runtime_schema.sql) — one Postgres
    transaction instead of the separate record_job_output()+try_transition_job()
    calls, closing the crash-between-writes gap those would leave open. Adopted
    from PR #33's job_store.py::complete() (there a raw psycopg transaction)
    during the convergence pass, reimplemented as a Postgres function so this
    stays on the same PostgREST client every other module in this service uses
    rather than adding psycopg/SUPABASE_DB_URL as a second access pattern.

    Returns False, not an error, if the job was already terminal — a
    duplicate delivery, per packet section 15's idempotency rule."""
    result = client.rpc("wow_agent_complete_job", {
        "p_job_id": job_id, "p_run_id": run_id, "p_candidate_id": candidate_id,
        "p_worker_id": worker_id, "p_worker_version": worker_version,
        "p_contract_version": contract_version, "p_evidence_snapshot_id": evidence_snapshot_id,
        "p_output": output, "p_output_hash": output_hash, "p_status": status,
        "p_ceiling": ceiling, "p_blockers": blockers, "p_error_code": error_code,
    }).execute()
    return bool(result.data)


def registry_matches(client: Any) -> bool:
    """Code<->DB worker-registry parity check, surfaced at /health/ready
    (packet section 9). Fails closed: an unreachable table can't be proven to
    match, so callers must treat any exception here as not-ready. Adopted
    from PR #33 during the convergence pass — Phase 1 had no code-side
    registry to check the DB against."""
    expected = {
        (spec.worker_id, spec.worker_version, spec.contract_version, spec.implementation_type, spec.authority_ceiling)
        for spec in WORKERS.values()
    }
    result = (
        client.table("wow_agent_worker_registry")
        .select("worker_id,worker_version,contract_version,implementation_type,authority_ceiling")
        .eq("enabled", True)
        .execute()
    )
    actual = {
        (row["worker_id"], row["worker_version"], row["contract_version"], row["implementation_type"], row["authority_ceiling"])
        for row in (result.data or [])
    }
    return actual == expected
