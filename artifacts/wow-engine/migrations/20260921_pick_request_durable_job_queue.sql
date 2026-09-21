-- V17 durable large-board scoring queue.
-- The HTTP Action enqueues work; a DB-leased worker executes canonical scoring
-- independently of the client connection. can_execute remains permanently false.

create table if not exists public.wow_pick_request_jobs (
    job_id uuid primary key default gen_random_uuid(),
    run_id text not null references public.wow_pick_request_runs(run_id) on delete cascade,
    request_id text not null unique,
    status text not null default 'QUEUED' check (status in (
        'QUEUED','RUNNING','RETRY_WAIT','COMPLETED','STOPPED','BLOCKED'
    )),
    response_mode text not null default 'COMPACT' check (response_mode in ('COMPACT','FULL')),
    batch_size integer not null default 10 check (batch_size between 1 and 50),
    model_identity text,
    lease_owner text,
    lease_expires_at timestamptz,
    next_attempt_at timestamptz,
    consecutive_failures integer not null default 0 check (consecutive_failures >= 0),
    total_batches_completed integer not null default 0 check (total_batches_completed >= 0),
    total_rows_attempted integer not null default 0 check (total_rows_attempted >= 0),
    receipt_recovered_count integer not null default 0 check (receipt_recovered_count >= 0),
    last_error jsonb,
    can_execute boolean not null default false check (can_execute = false),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists wow_pick_request_jobs_claim_idx
    on public.wow_pick_request_jobs(status, next_attempt_at, lease_expires_at, updated_at);

alter table public.wow_pick_request_jobs enable row level security;
revoke all on table public.wow_pick_request_jobs from public, anon, authenticated;
grant select, insert, update on table public.wow_pick_request_jobs to service_role;

create or replace function public.wow_claim_pick_request_job(
    p_worker_id text,
    p_lease_seconds integer default 1800
)
returns setof public.wow_pick_request_jobs
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
begin
    if coalesce(trim(p_worker_id), '') = '' then
        raise exception 'worker id required';
    end if;

    return query
    with candidate as (
        select j.job_id
        from public.wow_pick_request_jobs j
        join public.wow_pick_request_runs r on r.run_id = j.run_id
        where j.status in ('QUEUED','RETRY_WAIT','RUNNING')
          and r.run_status not like 'STOPPED\_%' escape '\\'
          and r.run_status not like 'CLOSED%'
          and (j.next_attempt_at is null or j.next_attempt_at <= now())
          and (
              j.status <> 'RUNNING'
              or j.lease_expires_at is null
              or j.lease_expires_at <= now()
          )
        order by j.updated_at asc, j.created_at asc
        for update of j skip locked
        limit 1
    )
    update public.wow_pick_request_jobs j
       set status = 'RUNNING',
           lease_owner = p_worker_id,
           lease_expires_at = now() + make_interval(secs => greatest(60, least(coalesce(p_lease_seconds, 1800), 7200))),
           updated_at = now()
      from candidate c
     where j.job_id = c.job_id
    returning j.*;
end;
$$;

revoke all on function public.wow_claim_pick_request_job(text, integer) from public, anon, authenticated;
grant execute on function public.wow_claim_pick_request_job(text, integer) to service_role;

comment on table public.wow_pick_request_jobs is
    'V17 durable exact-once prop-board worker queue. It never grants wager execution authority.';
comment on function public.wow_claim_pick_request_job(text, integer) is
    'Atomically leases one resumable V17 pick-request job using FOR UPDATE SKIP LOCKED.';
