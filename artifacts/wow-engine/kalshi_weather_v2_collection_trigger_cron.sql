-- WOW V17 — Kalshi Weather authenticated empirical-cohort collection trigger
--
-- Replaces the health-only read-only wake. The previous Supabase pg_cron job
-- (kalshi-weather-v2-shadow-read-only-wake) issued an unauthenticated GET to
-- the weather service's /health route, which can never start collection. The
-- only collection entrypoint is the authenticated POST below, so the empirical
-- cohort stayed at status=DISABLED and produced no new immutable shadow rows
-- regardless of how reliably the wake fired.
--
-- Required Supabase Vault secrets (created out of band; never committed):
--   wow_kalshi_weather_service_url   e.g. https://wow-kalshi-weather-v2.onrender.com
--   wow_kalshi_weather_cohort_key    must equal the isolated Render value of
--                                    WOW_KALSHI_WEATHER_COHORT_KEY
--
-- Required isolated Render configuration (out of band):
--   WOW_KALSHI_WEATHER_ISOLATED_CAPTURE_OWNER=1   installs the trigger route
--   WOW_KALSHI_WEATHER_COHORT_KEY=<secret>        must match the Vault secret
--   WOW_KALSHI_WEATHER_EMPIRICAL_COHORT_ENABLED=0 in-process loop stays off;
--                                                  this scheduler is the writer
--
-- Missing secrets fail closed: the function returns BLOCKED and dispatches no
-- HTTP request. This job can collect calibration evidence only. It cannot
-- certify calibration, publish a probability, or execute a trade.

create extension if not exists pg_net with schema extensions;
create extension if not exists pg_cron with schema pg_catalog;

create or replace function public.wow_kalshi_weather_trigger_collection()
returns jsonb
language plpgsql
security invoker
set search_path to ''
as $function$
declare
  v_base_url text;
  v_cohort_key text;
  v_request_id bigint;
begin
  select decrypted_secret
    into v_base_url
  from vault.decrypted_secrets
  where name = 'wow_kalshi_weather_service_url'
  order by created_at desc
  limit 1;

  select decrypted_secret
    into v_cohort_key
  from vault.decrypted_secrets
  where name = 'wow_kalshi_weather_cohort_key'
  order by created_at desc
  limit 1;

  if nullif(trim(v_base_url), '') is null then
    return jsonb_build_object(
      'status','BLOCKED',
      'reason','KALSHI_WEATHER_SERVICE_URL_SECRET_MISSING',
      'probability_publishable',false,
      'can_execute',false
    );
  end if;

  if nullif(trim(v_cohort_key), '') is null then
    return jsonb_build_object(
      'status','BLOCKED',
      'reason','KALSHI_WEATHER_COHORT_KEY_SECRET_MISSING',
      'probability_publishable',false,
      'can_execute',false
    );
  end if;

  v_request_id := net.http_post(
    url := rtrim(v_base_url, '/') || '/internal/kalshi-weather/v17/empirical-cohort/cron-run',
    headers := jsonb_build_object(
      'Content-Type','application/json',
      'Accept','application/json',
      'User-Agent','kalshi-supabase-cron-collection/1',
      'X-WOW-Weather-Cohort-Key', trim(v_cohort_key)
    ),
    body := '{}'::jsonb,
    timeout_milliseconds := 90000
  );

  return jsonb_build_object(
    'status','DISPATCHED',
    'request_id',v_request_id,
    'collection_mode','BOUNDED_ROTATING_CAPTURE_ONLY_SHADOW',
    'probability_publishable',false,
    'can_execute',false
  );
end;
$function$;

-- Internal maintenance only. Do not expose this RPC through Data API roles.
-- pg_cron executes the scheduled SQL as its scheduling database role.
revoke all privileges on function public.wow_kalshi_weather_trigger_collection()
  from public, anon, authenticated, service_role;

comment on function public.wow_kalshi_weather_trigger_collection() is
  'Research-only Kalshi Weather empirical-cohort collection dispatch. SECURITY INVOKER; not Data API callable; no probability publication or trading authority.';

-- Retire the health-only wake. It cannot start collection, so keeping it would
-- only add unauthenticated read traffic against the isolated service.
do $retire$
begin
  if exists (
    select 1 from cron.job where jobname = 'kalshi-weather-v2-shadow-read-only-wake'
  ) then
    perform cron.unschedule('kalshi-weather-v2-shadow-read-only-wake');
  end if;
end;
$retire$;

-- Same cost-constrained cadence as the job it replaces. Kept at */30 rather
-- than tightened so this change cannot increase the isolated service's shared
-- Render instance-hour consumption.
select cron.schedule(
  'kalshi-weather-v2-authenticated-collection-trigger',
  '*/30 * * * *',
  $$select public.wow_kalshi_weather_trigger_collection();$$
);
