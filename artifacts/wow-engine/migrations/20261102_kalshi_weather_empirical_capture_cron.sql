-- Kalshi Weather V2 — authenticated empirical capture trigger (Supabase Cron).
--
-- Root cause repaired here: the previously installed job
-- `kalshi-weather-v2-shadow-read-only-wake` issued an unauthenticated GET to
-- /health. That endpoint proves the FastAPI process and the Supabase read path
-- are reachable; it never calls the collection controller. The isolated runtime
-- deliberately runs with WOW_KALSHI_WEATHER_EMPIRICAL_COHORT_ENABLED=0 (the
-- shared 512-MiB WOW web process must not capture), so the authenticated
-- trigger was installed and never invoked: cron receipts succeeded while the
-- newest prediction stayed frozen.
--
-- This migration makes the authorized collection endpoint the scheduled work
-- and keeps the health GET as a separate, clearly non-collecting wake.
--
-- Prerequisite (owner action, not performed by code):
--   Store the existing Render `WOW_KALSHI_WEATHER_COHORT_KEY` value in Supabase
--   Vault under the name `kalshi_weather_cohort_key`. This migration fails
--   closed until that secret exists; it never falls back to an unauthenticated
--   wake and never embeds a key.
--
-- pg_net retains the queued request (including its headers) until dispatch.
-- Do not grant anon/authenticated access to net.* or vault.*; only the cron job
-- owner may execute the function below.
--
-- No probability publication, capability promotion, order placement or trading
-- surface is introduced.

create extension if not exists pg_cron with schema pg_catalog;
create extension if not exists pg_net;
create schema if not exists private;

create or replace function private.kalshi_weather_empirical_capture_tick()
returns bigint
language plpgsql
security definer
set search_path = ''
as $fn$
declare
  cohort_key text;
  request_id bigint;
begin
  select secret.decrypted_secret
    into cohort_key
    from vault.decrypted_secrets secret
   where secret.name = 'kalshi_weather_cohort_key'
   limit 1;

  if cohort_key is null or length(btrim(cohort_key)) = 0 then
    raise exception 'KALSHI_WEATHER_COHORT_KEY_MISSING_FROM_VAULT'
      using errcode = '55000',
            hint = 'Save the existing Render WOW_KALSHI_WEATHER_COHORT_KEY value in Supabase Vault as kalshi_weather_cohort_key.';
  end if;

  select net.http_post(
    url := 'https://wow-kalshi-weather-v2.onrender.com/internal/kalshi-weather/v17/empirical-cohort/cron-run',
    headers := jsonb_build_object(
      'Accept', 'application/json',
      'Content-Type', 'application/json',
      'User-Agent', 'kalshi-supabase-cron-capture/1',
      'X-WOW-Weather-Cohort-Key', btrim(cohort_key)
    ),
    body := '{}'::jsonb,
    timeout_milliseconds := 120000
  )
  into request_id;

  return request_id;
end;
$fn$;

revoke all on function private.kalshi_weather_empirical_capture_tick() from public;
grant execute on function private.kalshi_weather_empirical_capture_tick() to postgres;

-- Idempotent reschedule. The old read-only job name is removed so two writers
-- can never be scheduled by accident; the new capture job owns collection.
do $reschedule$
begin
  perform cron.unschedule(job.jobid)
     from cron.job as job
    where job.jobname in (
      'kalshi-weather-v2-shadow-read-only-wake',
      'kalshi-weather-v2-empirical-capture'
    );
end;
$reschedule$;

select cron.schedule(
  'kalshi-weather-v2-empirical-capture',
  '*/30 * * * *',
  $job$
    SELECT private.kalshi_weather_empirical_capture_tick();
  $job$
);

-- Non-collecting wake retained for observability and Free-tier warm-up. It is
-- explicitly not a data-acquisition path.
select cron.schedule(
  'kalshi-weather-v2-shadow-read-only-wake',
  '15,45 * * * *',
  $job$
    SELECT net.http_get(
      url := 'https://wow-kalshi-weather-v2.onrender.com/health',
      headers := '{"Accept":"application/json","User-Agent":"kalshi-supabase-cron-wake/1"}'::jsonb,
      timeout_milliseconds := 90000
    );
  $job$
);
