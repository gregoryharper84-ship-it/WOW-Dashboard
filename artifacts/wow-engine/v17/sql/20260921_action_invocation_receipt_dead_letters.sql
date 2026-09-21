-- V17 Action invocation telemetry recovery ledger.
-- Telemetry only: never probability, certification, terminal, or execution authority.

create table if not exists public.wow_action_invocation_receipt_dead_letters (
    dead_letter_id uuid primary key default gen_random_uuid(),
    invocation_id uuid not null unique,
    receipt jsonb not null,
    attempt_count integer not null check (attempt_count >= 1),
    last_error text not null,
    dead_lettered_at timestamptz not null default now(),
    recovered_at timestamptz null,
    can_execute boolean not null default false,
    constraint wow_action_invocation_receipt_dead_letters_can_execute_false check (can_execute = false)
);

alter table public.wow_action_invocation_receipt_dead_letters enable row level security;
revoke all on table public.wow_action_invocation_receipt_dead_letters from anon, authenticated;
grant select, insert, update on table public.wow_action_invocation_receipt_dead_letters to service_role;

create index if not exists wow_action_invocation_receipt_dead_letters_unrecovered_idx
    on public.wow_action_invocation_receipt_dead_letters (dead_lettered_at)
    where recovered_at is null;

comment on table public.wow_action_invocation_receipt_dead_letters is
'V17 dead-letter recovery ledger for Action invocation telemetry that exhausted bounded async retries. Telemetry only: never probability, certification, terminal, or execution authority; can_execute is permanently false.';
