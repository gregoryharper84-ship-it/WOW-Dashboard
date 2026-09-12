-- WOW V17 governed-write audit trigger repair
-- Root cause: trigger function referenced NEW.recommendation_record_id while
-- executing on wow_event_predictions. PL/pgSQL RECORD field resolution raised
-- 42703 before the LLP governance bridge could complete.
--
-- Preserve fail-closed behavior and can_execute=false. This change only makes
-- row identity extraction table-safe by reading NEW through jsonb.

create or replace function public.wow_log_governed_write()
returns trigger
language plpgsql
set search_path to ''
as $function$
declare
  v_new jsonb;
  v_row_pk text;
  v_research_run_id text;
begin
  v_new := to_jsonb(NEW);

  if TG_TABLE_NAME = 'wow_event_predictions' then
    v_row_pk := v_new->>'event_prediction_id';
    v_research_run_id := v_new->>'research_run_id';
  elsif TG_TABLE_NAME = 'wow_recommendation_records' then
    v_row_pk := v_new->>'recommendation_record_id';
    v_research_run_id := v_new->>'research_run_id';
  else
    v_row_pk := null;
    v_research_run_id := v_new->>'research_run_id';
  end if;

  insert into public.wow_governed_write_audit(table_name, op, row_pk, research_run_id)
  values (TG_TABLE_NAME, TG_OP, v_row_pk, v_research_run_id);

  return NEW;
end;
$function$;

-- Non-writing bridge preflight contract. The canonical bridge must exist and be
-- executable by service_role. A blank-identity probe is expected to fail closed
-- with V17_EVENT_REQUEST_IDENTITY_INCOMPLETE before any event-ledger mutation.
do $preflight$
declare
  v_bridge regprocedure;
begin
  v_bridge := to_regprocedure(
    'public.wow_v17_mlb_team_event_governance_bridge(uuid,text,text,text,text,text)'
  );

  if v_bridge is null then
    raise exception 'V17_LLP_GOVERNANCE_BRIDGE_MISSING';
  end if;

  if not has_function_privilege('service_role', v_bridge, 'EXECUTE') then
    raise exception 'V17_LLP_GOVERNANCE_BRIDGE_SERVICE_ROLE_EXECUTE_MISSING';
  end if;
end;
$preflight$;
