-- WOW Agent Runtime V1 — durable run ledger, job queue, and worker registry.
--
-- Phase 1 of WOW-AGENT-RUNTIME-V1. New tables only: this migration deliberately
-- does not touch wow_predictions, wow_event_predictions, wow_prop_evidence_snapshots,
-- wow_event_evidence, wow_calibrators, wow_prop_fitted_model_artifacts,
-- wow_mlb_research_model_artifacts, wow_specialist_registry, or
-- wow_runtime_capabilities — Phase 0's overlap audit found those already cover the
-- packet's evidence/calibrator/model-artifact/capability-routing/immutable-ledger
-- concepts. This file adds only what nothing existing covers: an async run ledger,
-- a candidate ledger, a job/queue-state table, a job-output table, a worker
-- (task-execution) registry distinct from the existing specialist *routing*
-- registry, and an audit-event log.
--
-- Fail-closed, non-execution: every table carries can_execute boolean not null
-- default false with a hard CHECK, matching every other table in this schema.
-- RLS is enabled with no policies on every table (service-role/Render backend
-- only; anon/authenticated get nothing) — matching the pattern applied to
-- wow_prop_evidence_snapshots in harden_prop_evidence_rls.

-- ── Run ledger ──────────────────────────────────────────────────────────────

create table if not exists public.wow_agent_runs (
    run_id uuid primary key default gen_random_uuid(),
    idempotency_key text not null,
    request_hash text not null,
    run_type text not null,
    requested_as_of timestamptz not null,
    user_timezone text not null,
    status text not null,
    stage text not null,
    can_execute boolean not null default false,
    dry_run_only boolean not null default true,
    governance_version text not null,
    rows_in integer not null default 0,
    rows_completed integer not null default 0,
    rows_held integer not null default 0,
    rows_rejected integer not null default 0,
    reconciliation_status text not null default 'NOT_EVALUATED',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    completed_at timestamptz,
    constraint wow_agent_runs_status check (status in (
        'CREATED','VALIDATING_REQUEST','DISCOVERY_QUEUED','DISCOVERY_RUNNING',
        'ROUTING','EVIDENCE_QUEUED','EVIDENCE_RUNNING','MODELING_QUEUED',
        'MODELING_RUNNING','AUDIT_QUEUED','AUDIT_RUNNING','FINAL_REFRESH',
        'RECONCILING','COMPLETED','COMPLETED_WITH_BLOCKERS','FAILED','CANCELED'
    )),
    constraint wow_agent_runs_reconciliation_status check (reconciliation_status in (
        'NOT_EVALUATED','BALANCED','UNBALANCED'
    )),
    constraint wow_agent_runs_rows_nonneg check (
        rows_in >= 0 and rows_completed >= 0 and rows_held >= 0 and rows_rejected >= 0
    ),
    constraint wow_agent_runs_never_execute check (can_execute = false),
    constraint wow_agent_runs_dry_run_only check (dry_run_only = true),
    constraint wow_agent_runs_idempotency unique (idempotency_key, request_hash)
);

alter table public.wow_agent_runs enable row level security;

create index if not exists wow_agent_runs_status_idx
    on public.wow_agent_runs (status, created_at desc);

-- ── Candidate ledger ────────────────────────────────────────────────────────

create table if not exists public.wow_agent_run_candidates (
    candidate_id uuid primary key default gen_random_uuid(),
    run_id uuid not null references public.wow_agent_runs(run_id),
    canonical_key text not null,
    sport text not null,
    league text,
    official_event_id text,
    participant text not null,
    opponent text,
    market_family text not null,
    stat_family text,
    period text not null,
    exact_line numeric,
    side text,
    settlement_operator text,
    controlling_worker_id text,
    -- Evidence already lives in wow_prop_evidence_snapshots (props) or
    -- wow_event_evidence/wow_event_source_snapshots (events) — no new evidence
    -- table. evidence_snapshot_kind discriminates which existing table
    -- evidence_snapshot_id points into; enforced at the application layer,
    -- since a single FK can't target either table conditionally.
    evidence_snapshot_id uuid,
    evidence_snapshot_kind text,
    terminal_label text,
    terminal_ceiling text,
    blockers jsonb not null default '[]'::jsonb,
    can_execute boolean not null default false,
    created_at timestamptz not null default now(),
    constraint wow_agent_run_candidates_blockers_array check (jsonb_typeof(blockers) = 'array'),
    constraint wow_agent_run_candidates_evidence_kind check (
        evidence_snapshot_kind is null or evidence_snapshot_kind in ('PROP','EVENT')
    ),
    constraint wow_agent_run_candidates_never_execute check (can_execute = false),
    constraint wow_agent_run_candidates_canonical_key unique (run_id, canonical_key)
);

alter table public.wow_agent_run_candidates enable row level security;

create index if not exists wow_agent_run_candidates_terminal_idx
    on public.wow_agent_run_candidates (run_id, terminal_label);

-- ── Worker (task-execution) registry ───────────────────────────────────────
-- Distinct from wow_specialist_registry (routing: which specialist governs a
-- sport/market pair) and wow_runtime_capabilities (lane-level AVAILABLE/
-- UNAVAILABLE). This registry describes queue-task execution contracts:
-- timeout/retry policy, authority ceiling, and whether a worker may originate
-- a controlling probability (FITTED_MODEL) or only validate/transform/research.

create table if not exists public.wow_agent_worker_registry (
    worker_id text not null,
    worker_version text not null,
    contract_version text not null,
    enabled boolean not null default false,
    implementation_type text not null,
    authority_ceiling text not null,
    supported_sports text[] not null default '{}'::text[],
    supported_market_families text[] not null default '{}'::text[],
    required_capabilities text[] not null default '{}'::text[],
    required_predecessors text[] not null default '{}'::text[],
    timeout_seconds integer not null,
    max_retries integer not null default 0,
    artifact_required boolean not null default false,
    configuration jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    primary key (worker_id, worker_version),
    constraint wow_agent_worker_registry_impl_type check (implementation_type in (
        'DETERMINISTIC','FITTED_MODEL','RESEARCH_AGENT'
    )),
    constraint wow_agent_worker_registry_timeout_positive check (timeout_seconds > 0),
    constraint wow_agent_worker_registry_retries_nonneg check (max_retries >= 0),
    -- Only a FITTED_MODEL implementation may originate a controlling
    -- probability distribution (packet section 6); enforced again in code.
    constraint wow_agent_worker_registry_config_object check (jsonb_typeof(configuration) = 'object')
);

alter table public.wow_agent_worker_registry enable row level security;

-- ── Job / queue state ───────────────────────────────────────────────────────

create table if not exists public.wow_agent_jobs (
    job_id uuid primary key default gen_random_uuid(),
    run_id uuid not null references public.wow_agent_runs(run_id),
    candidate_id uuid references public.wow_agent_run_candidates(candidate_id),
    worker_id text not null,
    worker_version text not null,
    idempotency_key text not null,
    status text not null,
    attempt integer not null default 0,
    required boolean not null,
    input_hash text not null,
    output_hash text,
    ceiling text,
    blockers jsonb not null default '[]'::jsonb,
    queued_at timestamptz not null default now(),
    started_at timestamptz,
    completed_at timestamptz,
    heartbeat_at timestamptz,
    error_code text,
    error_detail_redacted jsonb,
    can_execute boolean not null default false,
    constraint wow_agent_jobs_status check (status in (
        'QUEUED','RUNNING','SUCCEEDED','BLOCKED','REJECTED','TIMED_OUT',
        'RETRY_PENDING','DEAD_LETTERED','CANCELED'
    )),
    constraint wow_agent_jobs_attempt_nonneg check (attempt >= 0),
    constraint wow_agent_jobs_blockers_array check (jsonb_typeof(blockers) = 'array'),
    constraint wow_agent_jobs_never_execute check (can_execute = false),
    constraint wow_agent_jobs_idempotency_key unique (idempotency_key)
);

alter table public.wow_agent_jobs enable row level security;

create index if not exists wow_agent_jobs_queue_idx
    on public.wow_agent_jobs (status, queued_at)
    where status in ('QUEUED','RETRY_PENDING');

create index if not exists wow_agent_jobs_candidate_idx
    on public.wow_agent_jobs (run_id, candidate_id, required);

-- ── Job outputs ─────────────────────────────────────────────────────────────

create table if not exists public.wow_agent_job_outputs (
    output_id uuid primary key default gen_random_uuid(),
    job_id uuid not null references public.wow_agent_jobs(job_id),
    run_id uuid not null references public.wow_agent_runs(run_id),
    candidate_id uuid references public.wow_agent_run_candidates(candidate_id),
    worker_id text not null,
    worker_version text not null,
    evidence_snapshot_id uuid,
    contract_version text not null,
    output jsonb not null,
    output_hash text not null,
    can_execute boolean not null default false,
    created_at timestamptz not null default now(),
    constraint wow_agent_job_outputs_job_unique unique (job_id),
    constraint wow_agent_job_outputs_never_execute check (can_execute = false)
);

alter table public.wow_agent_job_outputs enable row level security;

create index if not exists wow_agent_job_outputs_lookup_idx
    on public.wow_agent_job_outputs (run_id, candidate_id, worker_id);

-- ── Audit log ────────────────────────────────────────────────────────────────

create table if not exists public.wow_agent_audit_events (
    audit_event_id bigint generated always as identity primary key,
    run_id uuid,
    candidate_id uuid,
    job_id uuid,
    event_type text not null,
    actor text not null,
    detail_redacted jsonb not null default '{}'::jsonb,
    can_execute boolean not null default false,
    created_at timestamptz not null default now(),
    constraint wow_agent_audit_events_never_execute check (can_execute = false)
);

alter table public.wow_agent_audit_events enable row level security;

create index if not exists wow_agent_audit_events_run_idx
    on public.wow_agent_audit_events (run_id, created_at);

-- Compare-and-set job transitions (packet section 5) are performed directly
-- from agent_runtime/repository.py via PostgREST: an UPDATE ... WHERE job_id
-- = :id AND status = :expected, with the updated row(s) selected back. Exactly
-- one row in the response means the transition was ours; zero means a
-- duplicate/racing worker already moved the job past the expected state. No
-- separate RPC is needed for this — see CasTransitionResult in
-- agent_runtime/repository.py.
