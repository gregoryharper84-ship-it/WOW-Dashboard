-- Durable recovery queue for V17 Action invocation receipts.
--
-- This table stores only the same non-secret request-envelope telemetry already
-- allowed in wow_action_invocation_receipts. It contains no request body,
-- player, line, price, probability, bearer token, API key, or model artifact.
-- It is server-side only and never changes scoring or execution authority.

create table if not exists public.wow_action_invocation_receipt_recovery (
    invocation_id uuid primary key,
    enqueued_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    attempt_count integer not null default 0 check (attempt_count >= 0),
    state text not null default 'PENDING'
        check (state in ('PENDING', 'DEAD_LETTER')),
    last_error_type text,
    receipt jsonb not null,
    can_execute boolean not null default false check (can_execute = false)
);

create index if not exists wow_action_invocation_receipt_recovery_state_updated_idx
    on public.wow_action_invocation_receipt_recovery (state, updated_at);

alter table public.wow_action_invocation_receipt_recovery enable row level security;

revoke all on table public.wow_action_invocation_receipt_recovery from anon, authenticated;
grant select, insert, update, delete on table public.wow_action_invocation_receipt_recovery to service_role;

comment on table public.wow_action_invocation_receipt_recovery is
    'Server-only durable retry/dead-letter queue for non-secret WOW V17 Action invocation receipts; can_execute=false.';
