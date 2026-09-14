-- Return only forward-shadow predictions that still need settlement.
-- Service-role only; this prevents the runtime from repeatedly scanning an
-- ever-growing prefix of already-settled rows.

create or replace function public.wow_mlb_1ip_bf_shadow_unsettled(
  p_artifact_checksum text,
  p_now timestamptz,
  p_limit integer default 500
)
returns setof public.wow_mlb_1ip_bf_shadow_predictions
language sql
stable
set search_path = ''
as $function$
  select p.*
  from public.wow_mlb_1ip_bf_shadow_predictions p
  where p.artifact_checksum = p_artifact_checksum
    and p.event_start_time < p_now
    and not exists (
      select 1
      from public.wow_mlb_1ip_bf_shadow_outcomes o
      where o.prediction_id = p.prediction_id
    )
  order by p.event_start_time, p.prediction_id
  limit greatest(1, least(coalesce(p_limit, 500), 5000));
$function$;

revoke all on function public.wow_mlb_1ip_bf_shadow_unsettled(text,timestamptz,integer) from public;
revoke all on function public.wow_mlb_1ip_bf_shadow_unsettled(text,timestamptz,integer) from anon;
revoke all on function public.wow_mlb_1ip_bf_shadow_unsettled(text,timestamptz,integer) from authenticated;
grant execute on function public.wow_mlb_1ip_bf_shadow_unsettled(text,timestamptz,integer) to service_role;
