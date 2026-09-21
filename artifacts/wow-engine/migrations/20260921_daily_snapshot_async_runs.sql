-- Durable asynchronous completion ledger for the public V17 Daily Action.
--
-- The public Action returns immediately after this submission receipt is
-- committed. Long-running Daily scoring continues off the HTTP response path.
-- A process crash cannot erase accepted work: queued/retryable/expired-running
-- rows remain recoverable from this server-only table.
--
-- This table carries no execution authority. Every row is hard-locked to
-- can_execute=false and the scoring result remains subject to the existing
-- V17_TERMINAL_REDUCER inside run_daily_snapshot.

create table if not exists public.wow_v17_daily_async_runs (
    run_id text primary key,
    submission_receipt_id uuid not null default gen_random_uuid() unique,
    idempotency_key text not null,
    request_hash text not null,
    request_payload jsonb not null,
    status text not null default 'QUEUED'
        check (status in ('QUEUED','RUNNING','RETRY_PENDING','COMPLETED','COMPLETED_WITH_BLOCKERS','FAILED')),
    attempt_count integer not null default 0 check (attempt_count >= 0),
    lease_owner text,
    lease_expires_at timestamptz,
    execution_run_id text,
    result_payload jsonb,
    last_error_type text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    completed_at timestamptz,
    can_execute boolean not null default false check (can_execute = false),
    constraint wow_v17_daily_async_runs_request_object
        check (jsonb_typeof(request_payload) = 'object'),
    constraint wow_v17_daily_async_runs_result_object
        check (result_payload is null or jsonb_typeof(result_payload) = 'object'),
    constraint wow_v17_daily_async_runs_idempotency unique (idempotency_key, request_hash)
);

create index if not exists wow_v17_daily_async_runs_status_updated_idx
    on public.wow_v17_daily_async_runs (status, updated_at);

create index if not exists wow_v17_daily_async_runs_lease_idx
    on public.wow_v17_daily_async_runs (status, lease_expires_at)
    where status = 'RUNNING';

alter table public.wow_v17_daily_async_runs enable row level security;
revoke all on table public.wow_v17_daily_async_runs from anon, authenticated;
grant select, insert, update, delete on table public.wow_v17_daily_async_runs to service_role;

comment on table public.wow_v17_daily_async_runs is
    'Server-only durable submission/lease/result ledger for asynchronous V17 Daily scoring; can_execute=false.';
