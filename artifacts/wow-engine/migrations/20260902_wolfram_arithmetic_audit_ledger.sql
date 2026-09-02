-- Immutable WolframAlpha arithmetic-verification receipts for WOW V17.
-- This ledger verifies deterministic transformations and economics only.  It
-- is structurally unable to block or replace the fitted sporting model.

create table if not exists public.wow_wolfram_arithmetic_audits (
    arithmetic_audit_id uuid primary key default gen_random_uuid(),
    prediction_id uuid not null references public.wow_predictions(prediction_id),
    created_at timestamptz not null default now(),
    audited_at timestamptz not null,
    provider text not null check (provider = 'WOLFRAM_ALPHA'),
    verdict text not null check (verdict in (
        'PASS',
        'WOLFRAM_AUDIT_INPUT_INVALID',
        'WOLFRAM_AUDIT_UNAVAILABLE',
        'WOLFRAM_OUTPUT_INVALID',
        'WOLFRAM_CALCULATION_MISMATCH'
    )),
    audit_required boolean not null check (audit_required = true),
    claim_count integer not null check (claim_count >= 0),
    receipts jsonb not null default '[]'::jsonb check (jsonb_typeof(receipts) = 'array'),
    audit_payload_hash text not null check (audit_payload_hash ~ '^[0-9a-f]{64}$'),
    blocks_model_probability boolean not null default false check (blocks_model_probability = false),
    can_execute boolean not null default false check (can_execute = false),
    unique (prediction_id, audit_payload_hash)
);

create index if not exists idx_wow_wolfram_arithmetic_audits_prediction
    on public.wow_wolfram_arithmetic_audits (prediction_id, created_at);

create or replace function public.wow_block_wolfram_arithmetic_audit_mutation()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    raise exception 'WOW Wolfram arithmetic audit receipts are immutable';
end;
$$;

revoke all on function public.wow_block_wolfram_arithmetic_audit_mutation()
    from public, anon, authenticated;

drop trigger if exists trg_wow_wolfram_arithmetic_audits_no_update
    on public.wow_wolfram_arithmetic_audits;
create trigger trg_wow_wolfram_arithmetic_audits_no_update
before update on public.wow_wolfram_arithmetic_audits
for each row execute function public.wow_block_wolfram_arithmetic_audit_mutation();

drop trigger if exists trg_wow_wolfram_arithmetic_audits_no_delete
    on public.wow_wolfram_arithmetic_audits;
create trigger trg_wow_wolfram_arithmetic_audits_no_delete
before delete on public.wow_wolfram_arithmetic_audits
for each row execute function public.wow_block_wolfram_arithmetic_audit_mutation();

alter table public.wow_wolfram_arithmetic_audits enable row level security;
revoke all on public.wow_wolfram_arithmetic_audits from anon, authenticated;
grant select, insert on public.wow_wolfram_arithmetic_audits to service_role;
revoke update, delete, truncate on public.wow_wolfram_arithmetic_audits from service_role;

comment on table public.wow_wolfram_arithmetic_audits is
'Append-only WolframAlpha verification of deterministic probability transformations and payout arithmetic. Never a sporting probability source; can_execute is always false.';
