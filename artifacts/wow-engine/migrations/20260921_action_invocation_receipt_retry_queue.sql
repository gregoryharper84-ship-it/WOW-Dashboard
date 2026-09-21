-- V17 Action invocation receipt durability repair.
--
-- The application now supplies a stable invocation_id before its first insert.
-- If the client cannot confirm persistence after bounded idempotent retries, the
-- same non-sensitive receipt envelope is dead-lettered here.  pg_cron retries the
-- canonical insert by invocation_id, so a late first insert and a retry cannot
-- create duplicate invocation receipts.  This is telemetry only and never changes
-- scoring, terminal semantics, probability publication, or can_execute=false.

create table if not exists public.wow_action_invocation_receipt_dead_letters (
    invocation_id uuid primary key,
    first_failed_at timestamptz not null default now(),
    last_failed_at timestamptz not null default now(),
    attempt_count integer not null check (attempt_count >= 1),
    last_error_type text not null,
    receipt_payload jsonb not null,
    recovered_at timestamptz,
    can_execute boolean not null default false check (can_execute = false)
);

create index if not exists wow_action_invocation_receipt_dlq_pending_idx
    on public.wow_action_invocation_receipt_dead_letters (last_failed_at, invocation_id)
    where recovered_at is null;

alter table public.wow_action_invocation_receipt_dead_letters enable row level security;
revoke all on table public.wow_action_invocation_receipt_dead_letters from public, anon, authenticated;
grant select, insert, update on table public.wow_action_invocation_receipt_dead_letters to service_role;

create or replace function public.wow_recover_action_invocation_receipt_dead_letters(
    p_limit integer default 100
)
returns jsonb
language plpgsql
security definer
set search_path to ''
as $function$
declare
    r public.wow_action_invocation_receipt_dead_letters%rowtype;
    recovered integer := 0;
    failed integer := 0;
begin
    for r in
        select *
        from public.wow_action_invocation_receipt_dead_letters
        where recovered_at is null
        order by last_failed_at, invocation_id
        limit greatest(1, least(coalesce(p_limit,100),500))
        for update skip locked
    loop
        begin
            insert into public.wow_action_invocation_receipts (
                invocation_id, occurred_at, route, action_operation_id, http_method,
                http_status, auth_scheme, caller_class, caller_user_agent, request_id,
                rows_in, duration_ms, can_execute
            ) values (
                r.invocation_id,
                coalesce((r.receipt_payload->>'occurred_at')::timestamptz, r.first_failed_at),
                r.receipt_payload->>'route',
                r.receipt_payload->>'action_operation_id',
                r.receipt_payload->>'http_method',
                (r.receipt_payload->>'http_status')::integer,
                coalesce(r.receipt_payload->>'auth_scheme','NONE'),
                coalesce(r.receipt_payload->>'caller_class','UNKNOWN'),
                nullif(r.receipt_payload->>'caller_user_agent',''),
                nullif(r.receipt_payload->>'request_id',''),
                nullif(r.receipt_payload->>'rows_in','')::integer,
                nullif(r.receipt_payload->>'duration_ms','')::numeric,
                false
            )
            on conflict (invocation_id) do nothing;

            update public.wow_action_invocation_receipt_dead_letters
            set recovered_at = now(), last_failed_at = now()
            where invocation_id = r.invocation_id;
            recovered := recovered + 1;
        exception when others then
            update public.wow_action_invocation_receipt_dead_letters
            set last_failed_at = now(),
                attempt_count = attempt_count + 1,
                last_error_type = sqlstate
            where invocation_id = r.invocation_id;
            failed := failed + 1;
        end;
    end loop;

    return jsonb_build_object(
        'status','PASS',
        'recovered',recovered,
        'failed',failed,
        'can_execute',false
    );
end;
$function$;

revoke all on function public.wow_recover_action_invocation_receipt_dead_letters(integer) from public, anon, authenticated;
grant execute on function public.wow_recover_action_invocation_receipt_dead_letters(integer) to service_role;

comment on table public.wow_action_invocation_receipt_dead_letters is
'Durable retry/dead-letter queue for certification-independent WOW Action invocation telemetry. Payload is restricted to the same non-sensitive request-envelope fields as wow_action_invocation_receipts. Never probability or execution authority.';

-- Idempotently install the recovery cadence. pg_cron already governs other WOW
-- maintenance jobs in this project; this job only replays telemetry by stable id.
do $do$
begin
    if not exists (select 1 from cron.job where jobname = 'wow-action-invocation-receipt-recovery') then
        perform cron.schedule(
            'wow-action-invocation-receipt-recovery',
            '* * * * *',
            'select public.wow_recover_action_invocation_receipt_dead_letters(100);'
        );
    end if;
end;
$do$;
