-- WOW Agent Runtime V1 orchestration schema. ADDITIVE ONLY.
-- Production application requires a separately approved migration.
create schema if not exists wow;
revoke all on schema wow from anon, authenticated;

create table if not exists wow.runs (
  run_id uuid primary key,
  idempotency_key text not null,
  request_hash text not null,
  request_payload jsonb not null,
  run_type text not null,
  requested_as_of timestamptz not null,
  user_timezone text not null,
  status text not null,
  stage text not null,
  can_execute boolean not null default false check (can_execute = false),
  dry_run_only boolean not null default true check (dry_run_only = true),
  governance_version text not null,
  rows_in integer not null default 0 check (rows_in >= 0),
  rows_completed integer not null default 0 check (rows_completed >= 0),
  rows_held integer not null default 0 check (rows_held >= 0),
  rows_rejected integer not null default 0 check (rows_rejected >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  completed_at timestamptz,
  unique (idempotency_key, request_hash)
);

create table if not exists wow.run_candidates (
  candidate_id uuid primary key,
  run_id uuid not null references wow.runs(run_id),
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
  terminal_label text,
  terminal_ceiling text,
  blockers jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  unique (run_id, canonical_key)
);

create table if not exists wow.worker_registry (
  worker_id text not null,
  worker_version text not null,
  contract_version text not null,
  implementation_type text not null check (implementation_type in ('DETERMINISTIC','FITTED_MODEL','RESEARCH_AGENT')),
  authority_ceiling text not null,
  configuration jsonb not null,
  enabled boolean not null default false,
  created_at timestamptz not null default now(),
  primary key (worker_id, worker_version)
);

create table if not exists wow.agent_jobs (
  job_id uuid primary key,
  run_id uuid not null references wow.runs(run_id),
  candidate_id uuid references wow.run_candidates(candidate_id),
  worker_id text not null,
  worker_version text not null,
  idempotency_key text not null unique,
  status text not null,
  attempt integer not null default 0 check (attempt >= 0),
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
  error_detail_redacted jsonb
);

create table if not exists wow.evidence_snapshots (
  evidence_snapshot_id uuid primary key,
  run_id uuid not null references wow.runs(run_id),
  candidate_id uuid not null references wow.run_candidates(candidate_id),
  as_of timestamptz not null,
  event_start_utc timestamptz not null,
  payload jsonb not null,
  provenance jsonb not null,
  missing_fields jsonb not null default '[]'::jsonb,
  source_conflicts jsonb not null default '[]'::jsonb,
  payload_hash text not null,
  sealed_at timestamptz not null default now(),
  unique (candidate_id, payload_hash)
);

alter table wow.run_candidates drop constraint if exists fk_wow_run_candidates_evidence;
alter table wow.run_candidates add constraint fk_wow_run_candidates_evidence foreign key (evidence_snapshot_id) references wow.evidence_snapshots(evidence_snapshot_id) deferrable initially deferred;

create table if not exists wow.agent_outputs (
  output_id uuid primary key,
  job_id uuid not null unique references wow.agent_jobs(job_id),
  run_id uuid not null references wow.runs(run_id),
  candidate_id uuid references wow.run_candidates(candidate_id),
  worker_id text not null,
  worker_version text not null,
  evidence_snapshot_id uuid references wow.evidence_snapshots(evidence_snapshot_id),
  contract_version text not null,
  output jsonb not null,
  output_hash text not null,
  created_at timestamptz not null default now()
);

create table if not exists wow.model_artifacts (
  artifact_id uuid primary key,
  provider_id text not null,
  model_family text not null,
  model_version text not null,
  feature_schema_version text not null,
  storage_uri text not null,
  sha256 text not null,
  training_cutoff timestamptz not null,
  evaluation_summary jsonb not null,
  certification_status text not null,
  certified_at timestamptz,
  retired_at timestamptz,
  unique(provider_id, model_version)
);

create table if not exists wow.capability_registry (
  capability_id uuid primary key,
  sport text not null,
  market_family text not null,
  stat_family text,
  period text not null,
  provider_id text not null,
  model_family text not null,
  artifact_id uuid references wow.model_artifacts(artifact_id),
  calibrator_id uuid,
  status text not null check (status in ('AVAILABLE','DEGRADED','UNAVAILABLE')),
  valid_from timestamptz not null,
  valid_to timestamptz,
  created_at timestamptz not null default now()
);

create table if not exists wow.terminal_decisions (
  decision_id uuid primary key,
  run_id uuid not null references wow.runs(run_id),
  candidate_id uuid not null unique references wow.run_candidates(candidate_id),
  final_terminal_ceiling text not null,
  terminal_label text not null,
  controlling_worker_id text,
  probability_publishable boolean not null default false,
  blockers jsonb not null,
  reducer_version text not null,
  decision_hash text not null,
  created_at timestamptz not null default now()
);

create table if not exists wow.audit_events (
  audit_event_id bigint generated always as identity primary key,
  run_id uuid,
  candidate_id uuid,
  job_id uuid,
  event_type text not null,
  actor text not null,
  detail_redacted jsonb not null,
  created_at timestamptz not null default now()
);

create index if not exists ix_wow_runs_status_created on wow.runs(status, created_at desc);
create index if not exists ix_wow_candidates_run_terminal on wow.run_candidates(run_id, terminal_label);
create index if not exists ix_wow_jobs_pending on wow.agent_jobs(status, queued_at) where status in ('QUEUED','RETRY_PENDING');
create index if not exists ix_wow_jobs_run_candidate_required on wow.agent_jobs(run_id,candidate_id,required);
create index if not exists ix_wow_outputs_run_candidate_worker on wow.agent_outputs(run_id,candidate_id,worker_id);
create index if not exists ix_wow_evidence_run_candidate on wow.evidence_snapshots(run_id,candidate_id);
create index if not exists ix_wow_capability_route on wow.capability_registry(sport,market_family,stat_family,period,status);
create index if not exists ix_wow_audit_run_created on wow.audit_events(run_id,created_at);

revoke all on all tables in schema wow from anon, authenticated;
revoke all on all sequences in schema wow from anon, authenticated;
