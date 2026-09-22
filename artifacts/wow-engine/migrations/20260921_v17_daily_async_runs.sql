-- WOW V17 durable asynchronous Daily run queue.
--
-- Submission is fast and durable: an Action receives run_id immediately, while
-- a Render worker claims the request later.  A lease token prevents an expired
-- worker from overwriting a newer attempt.  Terminal result payloads are write-once.
-- This queue never authorizes wager execution; can_execute is permanently false.

create table if not exists public.wow_v17_daily_async_runs (
    run_id text primary key,
    idempotency_key text,
    request_hash text not null,
    request_payload jsonb not null,
    run_status text not null default 'QUEUED'
        check (run_status in ('QUEUED','RUNNING','COMPLETED','FAILED')),
    submitted_at timestamptz not null default now(),
    started_at timestamptz,
    completed_at timestamptz,
    lease_expires_at timestamptz,
    lease_token uuid,
    attempt_count integer not null default 0 check (attempt_count >= 0),
    result_run_id text,
    result_payload jsonb,
    last_error_code text,
    can_execute boolean not null default false check (can_execute = false),
    check ((run_status = 'COMPLETED') = (result_payload is not null)),
    check (run_status <> 'COMPLETED' or completed_at is not null)
);

-- Forward-compatible hardening if an earlier preview of this additive table exists.
alter table public.wow_v17_daily_async_runs add column if not exists idempotency_key text;
alter table public.wow_v17_daily_async_runs add column if not exists request_hash text;
update public.wow_v17_daily_async_runs
set request_hash = encode(digest(request_payload::text, 'sha256'), 'hex')
where request_hash is null;
alter table public.wow_v17_daily_async_runs alter column request_hash set not null;

create unique index if not exists wow_v17_daily_async_runs_idempotency_key_uidx
    on public.wow_v17_daily_async_runs (idempotency_key)
    where idempotency_key is not null;

create index if not exists wow_v17_daily_async_runs_claim_idx
    on public.wow_v17_daily_async_runs (run_status, submitted_at)
    where run_status in ('QUEUED','RUNNING');

alter table public.wow_v17_daily_async_runs enable row level security;
revoke all on table public.wow_v17_daily_async_runs from public, anon, authenticated;
grant select, insert, update on table public.wow_v17_daily_async_runs to service_role;

create or replace function public.wow_claim_v17_daily_async_run(
    p_lease_seconds integer default 900
)
returns jsonb
language plpgsql
security definer
set search_path to ''
as $function$
declare
    r public.wow_v17_daily_async_runs%rowtype;
    token uuid := gen_random_uuid();
    bounded_lease integer := greatest(60, least(coalesce(p_lease_seconds,900),3600));
begin
    select * into r
    from public.wow_v17_daily_async_runs
    where run_status = 'QUEUED'
       or (run_status = 'RUNNING' and lease_expires_at is not null and lease_expires_at <= now())
    order by submitted_at, run_id
    limit 1
    for update skip locked;

    if not found then
        return jsonb_build_object('status','EMPTY','can_execute',false);
    end if;

    update public.wow_v17_daily_async_runs
    set run_status = 'RUNNING',
        started_at = coalesce(started_at, now()),
        lease_expires_at = now() + make_interval(secs => bounded_lease),
        lease_token = token,
        attempt_count = attempt_count + 1,
        last_error_code = null
    where run_id = r.run_id;

    return jsonb_build_object(
        'status','CLAIMED',
        'run_id',r.run_id,
        'request_payload',r.request_payload,
        'lease_token',token,
        'lease_seconds',bounded_lease,
        'attempt_count',r.attempt_count + 1,
        'can_execute',false
    );
end;
$function$;

create or replace function public.wow_complete_v17_daily_async_run(
    p_run_id text,
    p_lease_token uuid,
    p_result_run_id text,
    p_result_payload jsonb
)
returns jsonb
language plpgsql
security definer
set search_path to ''
as $function$
declare
    changed integer := 0;
begin
    if p_result_payload is null then
        return jsonb_build_object('status','REJECTED','code','RESULT_PAYLOAD_REQUIRED','can_execute',false);
    end if;

    update public.wow_v17_daily_async_runs
    set run_status = 'COMPLETED',
        completed_at = now(),
        result_run_id = nullif(p_result_run_id,''),
        result_payload = p_result_payload,
        lease_expires_at = null,
        lease_token = null,
        last_error_code = null
    where run_id = p_run_id
      and run_status = 'RUNNING'
      and lease_token = p_lease_token;
    get diagnostics changed = row_count;

    return jsonb_build_object(
        'status',case when changed = 1 then 'COMPLETED' else 'STALE_LEASE' end,
        'run_id',p_run_id,
        'can_execute',false
    );
end;
$function$;

create or replace function public.wow_fail_v17_daily_async_run(
    p_run_id text,
    p_lease_token uuid,
    p_error_code text,
    p_max_attempts integer default 3
)
returns jsonb
language plpgsql
security definer
set search_path to ''
as $function$
declare
    r public.wow_v17_daily_async_runs%rowtype;
    next_status text;
begin
    select * into r
    from public.wow_v17_daily_async_runs
    where run_id = p_run_id
      and run_status = 'RUNNING'
      and lease_token = p_lease_token
    for update;

    if not found then
        return jsonb_build_object('status','STALE_LEASE','run_id',p_run_id,'can_execute',false);
    end if;

    next_status := case
        when r.attempt_count >= greatest(1, least(coalesce(p_max_attempts,3),10)) then 'FAILED'
        else 'QUEUED'
    end;

    update public.wow_v17_daily_async_runs
    set run_status = next_status,
        completed_at = case when next_status = 'FAILED' then now() else null end,
        lease_expires_at = null,
        lease_token = null,
        last_error_code = left(coalesce(nullif(p_error_code,''),'ASYNC_WORKER_FAILED'),256)
    where run_id = p_run_id;

    return jsonb_build_object(
        'status',next_status,
        'run_id',p_run_id,
        'attempt_count',r.attempt_count,
        'can_execute',false
    );
end;
$function$;

create or replace function public.wow_v17_daily_async_terminal_immutable()
returns trigger
language plpgsql
set search_path to ''
as $function$
begin
    if old.run_status in ('COMPLETED','FAILED') then
        if new.run_status is distinct from old.run_status
           or new.completed_at is distinct from old.completed_at
           or new.result_run_id is distinct from old.result_run_id
           or new.result_payload is distinct from old.result_payload
           or new.last_error_code is distinct from old.last_error_code then
            raise exception 'V17_DAILY_ASYNC_TERMINAL_IMMUTABLE';
        end if;
    end if;
    return new;
end;
$function$;

drop trigger if exists wow_v17_daily_async_terminal_immutable_trg
    on public.wow_v17_daily_async_runs;
create trigger wow_v17_daily_async_terminal_immutable_trg
before update on public.wow_v17_daily_async_runs
for each row execute function public.wow_v17_daily_async_terminal_immutable();

revoke all on function public.wow_claim_v17_daily_async_run(integer) from public, anon, authenticated;
revoke all on function public.wow_complete_v17_daily_async_run(text,uuid,text,jsonb) from public, anon, authenticated;
revoke all on function public.wow_fail_v17_daily_async_run(text,uuid,text,integer) from public, anon, authenticated;
grant execute on function public.wow_claim_v17_daily_async_run(integer) to service_role;
grant execute on function public.wow_complete_v17_daily_async_run(text,uuid,text,jsonb) to service_role;
grant execute on function public.wow_fail_v17_daily_async_run(text,uuid,text,integer) to service_role;

comment on table public.wow_v17_daily_async_runs is
'Durable V17 Daily submission/worker queue. Terminal result payload is immutable. Analytical only; can_execute=false.';
