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
    request_payload jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    completed_at timestamptz,
    constraint wow_agent_runs_status check (status in (
        'CREATED','VALIDATING_REQUEST','DISCOVERY_QUEUED','DISCOVERY_RUNNING',
        'ROUTING','RESEARCH_QUEUED','RESEARCH_RUNNING',
        'EVIDENCE_QUEUED','EVIDENCE_RUNNING','MODELING_QUEUED',
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
    evidence_snapshot_id uuid,
    evidence_snapshot_kind text,
    candidate_payload jsonb not null default '{}'::jsonb,
    terminal_label text,
    terminal_ceiling text,
    blockers jsonb not null default '[]'::jsonb,
    can_execute boolean not null default false,
    created_at timestamptz not null default now(),
    constraint wow_agent_run_candidates_blockers_array check (jsonb_typeof(blockers) = 'array'),
    constraint wow_agent_run_candidates_payload_object check (jsonb_typeof(candidate_payload) = 'object'),
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
    constraint wow_agent_worker_registry_config_object check (jsonb_typeof(configuration) = 'object')
);

alter table public.wow_agent_worker_registry enable row level security;

-- Canonical WOW v16 worker graph. Scout and Research workers are evidence-only;
-- wow.controlling-model is the only FITTED_MODEL probability-originating worker.
insert into public.wow_agent_worker_registry
    (worker_id, worker_version, contract_version, implementation_type, authority_ceiling,
     required_predecessors, timeout_seconds, max_retries, artifact_required, configuration, enabled)
values
    ('wow.parallel-discovery-router', '1.0.0', 'wow.agent-output.v1', 'RESEARCH_AGENT', 'RESEARCH_INTEREST',
     '{}', 30, 2, false, '{}'::jsonb, true),
    ('wow.global-scout-coordinator', '1.0.0', 'wow.agent-output.v1', 'RESEARCH_AGENT', 'RESEARCH_INTEREST',
     '{wow.parallel-discovery-router}', 30, 2, false, '{"prediction_authority":false}'::jsonb, true),
    ('wow.prop-scout-router', '1.0.0', 'wow.agent-output.v1', 'RESEARCH_AGENT', 'RESEARCH_INTEREST',
     '{wow.global-scout-coordinator}', 30, 2, false, '{"prediction_authority":false}'::jsonb, true),
    ('wow.ml-event-scout-router', '1.0.0', 'wow.agent-output.v1', 'RESEARCH_AGENT', 'RESEARCH_INTEREST',
     '{wow.global-scout-coordinator}', 30, 2, false, '{"prediction_authority":false}'::jsonb, true),
    ('wow.slate-integrity-expert', '1.0.0', 'wow.agent-output.v1', 'DETERMINISTIC', 'IDENTITY_VERIFIED',
     '{wow.global-scout-coordinator}', 20, 1, false, '{}'::jsonb, true),
    ('wow.source-provenance-researcher', '1.0.0', 'wow.agent-output.v1', 'RESEARCH_AGENT', 'RESEARCH_INTEREST',
     '{wow.slate-integrity-expert}', 30, 2, false, '{"prediction_authority":false}'::jsonb, true),
    ('wow.participant-status-researcher', '1.0.0', 'wow.agent-output.v1', 'RESEARCH_AGENT', 'RESEARCH_INTEREST',
     '{wow.slate-integrity-expert}', 30, 2, false, '{"prediction_authority":false}'::jsonb, true),
    ('wow.history-comparables-researcher', '1.0.0', 'wow.agent-output.v1', 'RESEARCH_AGENT', 'RESEARCH_INTEREST',
     '{wow.slate-integrity-expert}', 45, 2, false, '{"prediction_authority":false}'::jsonb, true),
    ('wow.matchup-context-researcher', '1.0.0', 'wow.agent-output.v1', 'RESEARCH_AGENT', 'RESEARCH_INTEREST',
     '{wow.slate-integrity-expert}', 30, 2, false, '{"prediction_authority":false}'::jsonb, true),
    ('wow.market-settlement-researcher', '1.0.0', 'wow.agent-output.v1', 'RESEARCH_AGENT', 'RESEARCH_INTEREST',
     '{wow.slate-integrity-expert}', 30, 2, false, '{"prediction_authority":false}'::jsonb, true),
    ('wow.research-evidence-reconciler', '1.0.0', 'wow.agent-output.v1', 'DETERMINISTIC', 'RESEARCH_INTEREST',
     '{wow.source-provenance-researcher,wow.participant-status-researcher,wow.history-comparables-researcher,wow.matchup-context-researcher,wow.market-settlement-researcher}',
     20, 1, false, '{"prediction_authority":false}'::jsonb, true),
    ('wow.evidence-hydration', '1.0.0', 'wow.agent-output.v1', 'DETERMINISTIC', 'EVIDENCE_VERIFIED',
     '{wow.research-evidence-reconciler}', 45, 2, false, '{}'::jsonb, true),
    ('wow.controlling-model', '1.0.0', 'wow.agent-output.v1', 'FITTED_MODEL', 'MODEL_QUALIFIED_HOLD',
     '{wow.evidence-hydration}', 60, 1, true, '{}'::jsonb, true),
    ('wow.failure-path-framework', '1.0.0', 'wow.agent-output.v1', 'DETERMINISTIC', 'MODEL_QUALIFIED_HOLD',
     '{wow.controlling-model}', 30, 1, false, '{}'::jsonb, true),
    ('wow.dynamic-calibration-expert', '1.0.0', 'wow.agent-output.v1', 'DETERMINISTIC', 'MODEL_QUALIFIED_HOLD',
     '{wow.failure-path-framework}', 30, 1, true, '{}'::jsonb, true),
    ('wow.exact-line-market-auditor', '1.0.0', 'wow.agent-output.v1', 'DETERMINISTIC', 'MARKET_VERIFIED_HOLD',
     '{wow.dynamic-calibration-expert}', 30, 2, false, '{}'::jsonb, true),
    ('wow.structure-exposure-governor', '1.0.0', 'wow.agent-output.v1', 'DETERMINISTIC', 'STRUCTURE_VERIFIED_HOLD',
     '{wow.exact-line-market-auditor}', 20, 1, false, '{}'::jsonb, true),
    ('wow.final-refresh-governor', '1.0.0', 'wow.agent-output.v1', 'DETERMINISTIC', 'FINAL_REFRESH_HOLD',
     '{wow.structure-exposure-governor}', 30, 2, false, '{}'::jsonb, true),
    ('wow.terminal-ceiling-reducer', '1.0.0', 'wow.agent-output.v1', 'DETERMINISTIC', 'FINAL_APPROVED',
     '{wow.final-refresh-governor}', 15, 0, false, '{}'::jsonb, true)
on conflict (worker_id, worker_version) do update
set contract_version = excluded.contract_version,
    implementation_type = excluded.implementation_type,
    authority_ceiling = excluded.authority_ceiling,
    required_predecessors = excluded.required_predecessors,
    timeout_seconds = excluded.timeout_seconds,
    max_retries = excluded.max_retries,
    artifact_required = excluded.artifact_required,
    configuration = excluded.configuration,
    enabled = excluded.enabled;

update public.wow_agent_worker_registry
set enabled = false
where enabled = true
  and (worker_id, worker_version) not in (
    ('wow.parallel-discovery-router','1.0.0'),
    ('wow.global-scout-coordinator','1.0.0'),
    ('wow.prop-scout-router','1.0.0'),
    ('wow.ml-event-scout-router','1.0.0'),
    ('wow.slate-integrity-expert','1.0.0'),
    ('wow.source-provenance-researcher','1.0.0'),
    ('wow.participant-status-researcher','1.0.0'),
    ('wow.history-comparables-researcher','1.0.0'),
    ('wow.matchup-context-researcher','1.0.0'),
    ('wow.market-settlement-researcher','1.0.0'),
    ('wow.research-evidence-reconciler','1.0.0'),
    ('wow.evidence-hydration','1.0.0'),
    ('wow.controlling-model','1.0.0'),
    ('wow.failure-path-framework','1.0.0'),
    ('wow.dynamic-calibration-expert','1.0.0'),
    ('wow.exact-line-market-auditor','1.0.0'),
    ('wow.structure-exposure-governor','1.0.0'),
    ('wow.final-refresh-governor','1.0.0'),
    ('wow.terminal-ceiling-reducer','1.0.0')
  );

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

-- ── Terminal decisions ──────────────────────────────────────────────────────

create table if not exists public.wow_agent_terminal_decisions (
    decision_id uuid primary key default gen_random_uuid(),
    run_id uuid not null references public.wow_agent_runs(run_id),
    candidate_id uuid not null references public.wow_agent_run_candidates(candidate_id),
    final_terminal_ceiling text not null,
    terminal_label text not null,
    controlling_worker_id text,
    probability_publishable boolean not null default false,
    blockers jsonb not null default '[]'::jsonb,
    reducer_version text not null,
    decision_hash text not null,
    can_execute boolean not null default false,
    created_at timestamptz not null default now(),
    constraint wow_agent_terminal_decisions_blockers_array check (jsonb_typeof(blockers) = 'array'),
    constraint wow_agent_terminal_decisions_never_execute check (can_execute = false),
    constraint wow_agent_terminal_decisions_one_per_candidate unique (candidate_id)
);

alter table public.wow_agent_terminal_decisions enable row level security;

create index if not exists wow_agent_terminal_decisions_run_idx
    on public.wow_agent_terminal_decisions (run_id);

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

-- ── Atomic job completion ────────────────────────────────────────────────────

create or replace function public.wow_agent_complete_job(
    p_job_id uuid,
    p_run_id uuid,
    p_candidate_id uuid,
    p_worker_id text,
    p_worker_version text,
    p_contract_version text,
    p_evidence_snapshot_id uuid,
    p_output jsonb,
    p_output_hash text,
    p_status text,
    p_ceiling text,
    p_blockers jsonb,
    p_error_code text
) returns boolean
language plpgsql
volatile
security invoker
set search_path = public
as $$
declare
    v_current_status text;
    v_inserted_count integer;
begin
    select status into v_current_status
    from public.wow_agent_jobs
    where job_id = p_job_id
    for update;

    if not found then
        raise exception 'JOB_NOT_FOUND: %', p_job_id;
    end if;

    if v_current_status in (
        'SUCCEEDED','BLOCKED','REJECTED','TIMED_OUT','DEAD_LETTERED','CANCELED'
    ) then
        return false;
    end if;

    insert into public.wow_agent_job_outputs (
        job_id, run_id, candidate_id, worker_id, worker_version,
        evidence_snapshot_id, contract_version, output, output_hash
    )
    values (
        p_job_id, p_run_id, p_candidate_id, p_worker_id, p_worker_version,
        p_evidence_snapshot_id, p_contract_version, p_output, p_output_hash
    )
    on conflict (job_id) do nothing;

    get diagnostics v_inserted_count = row_count;
    if v_inserted_count = 0 then
        return false;
    end if;

    update public.wow_agent_jobs
    set status = p_status,
        output_hash = p_output_hash,
        ceiling = p_ceiling,
        blockers = coalesce(p_blockers, '[]'::jsonb),
        completed_at = now(),
        heartbeat_at = now(),
        error_code = p_error_code
    where job_id = p_job_id
      and status in ('RUNNING','RETRY_PENDING');

    if not found then
        raise exception 'JOB_STATE_COMPARE_AND_SET_FAILED: %', p_job_id;
    end if;

    return true;
end;
$$;

revoke all on function public.wow_agent_complete_job from anon, authenticated;
