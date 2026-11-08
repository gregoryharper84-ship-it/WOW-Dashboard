-- WOW V17 — Kalshi Weather durable empirical-cohort dispatch queue
--
-- The former pg_net POST attached a cohort credential to a redirect-following
-- transport. pg_net v0.12.0 enables CURLOPT_FOLLOWLOCATION for POST and cannot
-- disable it per request; custom headers can therefore reach a redirect target.
-- This migration removes that network hop entirely. pg_cron writes one
-- non-secret, idempotent dispatch row. The isolated Render service claims it
-- with a service-role RPC using FOR UPDATE SKIP LOCKED and runs the existing
-- bounded capture-only cycle locally. No URL, cohort key, bearer token, or
-- provider credential leaves Postgres.
--
-- Required isolated Render configuration (out of band):
--   WOW_KALSHI_WEATHER_ISOLATED_CAPTURE_OWNER=1
--   WOW_KALSHI_WEATHER_EMPIRICAL_DISPATCH_QUEUE_ENABLED=1
--   WOW_KALSHI_WEATHER_EMPIRICAL_COHORT_ENABLED=0
--   WOW_KALSHI_WEATHER_EMPIRICAL_DISPATCH_POLL_SECONDS=60 (optional; 15-300)
-- The queue consumer and legacy in-process loop are mutually exclusive.
--
-- This is research-only dispatch. It cannot certify calibration, publish a
-- probability, or execute a trade. The queue is durable; claims are atomic;
-- abandoned claims are retried at most three times.
--
-- Status: IMPLEMENTED_IN_PR. Applying this file is a separate authorized step.
-- Until applied and a completed queue row is read back, production is unchanged.

create extension if not exists pg_cron with schema pg_catalog;

create table if not exists public.wow_kalshi_weather_collection_dispatch_queue (
  id bigint generated always as identity primary key,
  scheduled_slot timestamptz not null unique,
  status text not null default 'PENDING'
    check (status in ('PENDING', 'CLAIMED', 'COMPLETED', 'FAILED')),
  attempts integer not null default 0 check (attempts >= 0),
  created_at timestamptz not null default clock_timestamp(),
  claimed_at timestamptz,
  completed_at timestamptz,
  last_error_code text,
  result jsonb
);

create index if not exists wow_kalshi_weather_dispatch_queue_claim_idx
  on public.wow_kalshi_weather_collection_dispatch_queue (status, scheduled_slot);

alter table public.wow_kalshi_weather_collection_dispatch_queue
  enable row level security;

revoke all privileges on table public.wow_kalshi_weather_collection_dispatch_queue
  from public, anon, authenticated;
grant select, update on table public.wow_kalshi_weather_collection_dispatch_queue
  to service_role;

comment on table public.wow_kalshi_weather_collection_dispatch_queue is
  'Durable, non-secret research-only collection dispatch queue. Service-role consumer claims rows atomically; no HTTP credential is stored.';

create or replace function public.claim_wow_kalshi_weather_collection_dispatch()
returns table(dispatch_id bigint, scheduled_slot timestamptz, attempts integer)
language plpgsql
security invoker
set search_path to ''
as $function$
begin
  -- A worker crash cannot strand a row forever. After three abandoned claims,
  -- stop retrying and leave a typed terminal record for operator review.
  update public.wow_kalshi_weather_collection_dispatch_queue
  set status = 'FAILED',
      completed_at = clock_timestamp(),
      last_error_code = 'DISPATCH_MAX_ATTEMPTS_EXCEEDED'
  where attempts >= 3
    and (
      status = 'PENDING'
      or (
        status = 'CLAIMED'
        and claimed_at < clock_timestamp() - interval '15 minutes'
      )
    );

  return query
  with candidate as (
    select q.id
    from public.wow_kalshi_weather_collection_dispatch_queue as q
    where q.attempts < 3
      and (
        q.status = 'PENDING'
        or (
          q.status = 'CLAIMED'
          and q.claimed_at < clock_timestamp() - interval '15 minutes'
        )
      )
    order by q.scheduled_slot, q.id
    for update skip locked
    limit 1
  )
  update public.wow_kalshi_weather_collection_dispatch_queue as q
  set status = 'CLAIMED',
      claimed_at = clock_timestamp(),
      attempts = q.attempts + 1,
      completed_at = null,
      last_error_code = null,
      result = null
  from candidate
  where q.id = candidate.id
  returning q.id, q.scheduled_slot, q.attempts;
end;
$function$;

revoke all privileges on function public.claim_wow_kalshi_weather_collection_dispatch()
  from public, anon, authenticated;
grant execute on function public.claim_wow_kalshi_weather_collection_dispatch()
  to service_role;

comment on function public.claim_wow_kalshi_weather_collection_dispatch() is
  'Atomically claims one durable Kalshi Weather shadow dispatch for the isolated service-role consumer; no publication or execution authority.';

create or replace function public.wow_kalshi_weather_trigger_collection()
returns jsonb
language plpgsql
security invoker
set search_path to ''
as $function$
declare
  v_now timestamptz := clock_timestamp();
  v_slot timestamptz;
  v_dispatch_id bigint;
  v_status text;
begin
  -- Coalesce retries within the same half-hour schedule slot.
  v_slot := date_trunc('hour', v_now)
    + make_interval(mins => (extract(minute from v_now)::integer / 30) * 30);

  insert into public.wow_kalshi_weather_collection_dispatch_queue (scheduled_slot)
  values (v_slot)
  on conflict (scheduled_slot) do nothing
  returning id into v_dispatch_id;

  if v_dispatch_id is null then
    select q.id
      into v_dispatch_id
    from public.wow_kalshi_weather_collection_dispatch_queue as q
    where q.scheduled_slot = v_slot;
    v_status := 'ALREADY_QUEUED';
  else
    v_status := 'QUEUED';
  end if;

  return jsonb_build_object(
    'status', v_status,
    'dispatch_id', v_dispatch_id,
    'scheduled_slot', v_slot,
    'dispatch_transport', 'DURABLE_DATABASE_QUEUE',
    'probability_publishable', false,
    'can_execute', false
  );
end;
$function$;

-- Internal maintenance only. The cron job runs as the database role that
-- installed this migration; no Data API role may enqueue dispatches directly.
revoke all privileges on function public.wow_kalshi_weather_trigger_collection()
  from public, anon, authenticated, service_role;

comment on function public.wow_kalshi_weather_trigger_collection() is
  'Idempotently enqueues one non-secret, research-only Kalshi Weather collection dispatch per half-hour slot. No outbound HTTP, probability publication, or trading authority.';

-- Retire both former network wakes and deduplicate the durable queue schedule.
do $dedupe$
declare
  v_job_id bigint;
begin
  for v_job_id in
    select jobid
    from cron.job
    where jobname in (
      'kalshi-weather-v2-shadow-read-only-wake',
      'kalshi-weather-v2-authenticated-collection-trigger',
      'kalshi-weather-v2-durable-collection-dispatch'
    )
  loop
    perform cron.unschedule(v_job_id);
  end loop;
end;
$dedupe$;

select cron.schedule(
  'kalshi-weather-v2-durable-collection-dispatch',
  '*/30 * * * *',
  $$select public.wow_kalshi_weather_trigger_collection();$$
);
