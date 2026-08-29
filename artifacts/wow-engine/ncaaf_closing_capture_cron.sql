-- WOW v16 Clean Core — NCAAF automatic closing-line capture scheduler
--
-- Supabase pg_cron invokes the authenticated Render research boundary every
-- five minutes. The Render boundary fetches an approved read-only exact-event
-- two-way moneyline quote and writes only calibration/CLV evidence.
--
-- Required Supabase Vault secrets (created out of band; never committed):
--   wow_render_service_url   e.g. https://<service>.onrender.com
--   wow_action_api_key       existing WOW internal API credential
--
-- Required Render env configuration (out of band):
--   WOW_NCAAF_MARKET_FEED_URL
--   WOW_NCAAF_MARKET_FEED_TOKEN (optional)
--
-- Missing secrets or feed configuration fails closed. No synthetic close is
-- generated. No execution path exists.

create extension if not exists pg_net with schema extensions;
create extension if not exists pg_cron with schema pg_catalog;

create or replace function public.wow_ncaaf_trigger_closing_capture()
returns jsonb
language plpgsql
security definer
set search_path to ''
as $function$
declare
  v_base_url text;
  v_api_key text;
  v_request_id bigint;
begin
  select decrypted_secret
    into v_base_url
  from vault.decrypted_secrets
  where name = 'wow_render_service_url'
  order by created_at desc
  limit 1;

  select decrypted_secret
    into v_api_key
  from vault.decrypted_secrets
  where name = 'wow_action_api_key'
  order by created_at desc
  limit 1;

  if nullif(trim(v_base_url), '') is null then
    return jsonb_build_object(
      'status','BLOCKED',
      'reason','WOW_RENDER_SERVICE_URL_SECRET_MISSING',
      'probability_publishable',false,
      'can_execute',false
    );
  end if;

  if nullif(trim(v_api_key), '') is null then
    return jsonb_build_object(
      'status','BLOCKED',
      'reason','WOW_ACTION_API_KEY_SECRET_MISSING',
      'probability_publishable',false,
      'can_execute',false
    );
  end if;

  v_request_id := net.http_post(
    url := rtrim(v_base_url, '/') || '/internal/ncaaf/capture-closing-lines',
    headers := jsonb_build_object(
      'Content-Type','application/json',
      'Authorization','Bearer ' || v_api_key
    ),
    body := '{}'::jsonb,
    timeout_milliseconds := 10000
  );

  return jsonb_build_object(
    'status','DISPATCHED',
    'request_id',v_request_id,
    'research_only',true,
    'probability_publishable',false,
    'can_execute',false
  );
end;
$function$;

-- Job names are stable; scheduling the same name replaces its definition.
select cron.schedule(
  'wow-ncaaf-closing-line-capture',
  '*/5 * * * *',
  $$select public.wow_ncaaf_trigger_closing_capture();$$
);

comment on function public.wow_ncaaf_trigger_closing_capture() is
  'Research-only NCAAF closing-line cron dispatch. No trading or execution authority.';
