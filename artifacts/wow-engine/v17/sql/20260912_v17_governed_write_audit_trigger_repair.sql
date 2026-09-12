-- WOW V17 governed-write audit trigger + LLP bridge contract repair
-- Root cause 1: trigger function referenced NEW.recommendation_record_id while
-- executing on wow_event_predictions. PL/pgSQL RECORD field resolution raised
-- 42703 before the LLP governance bridge could complete.
-- Root cause 2: the Python V17 runtime requires postmodel_gates_status and
-- final_gates_status, but the probability-only bridge did not emit them.
--
-- Preserve fail-closed behavior and can_execute=false. No probability, ranking,
-- publication, or execution gate is weakened.

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

-- Keep the canonical public wrapper aligned with the Python runtime contract.
-- These aggregate status fields summarize already-computed governed gates; they
-- do not bypass or replace the underlying probability audit, calibration,
-- decision, final-refresh, publication, or terminal reducer.
create or replace function public.wow_v17_mlb_team_event_governance_bridge(
  p_score_snapshot_id uuid,
  p_research_run_id text,
  p_event_key text,
  p_requested_timezone text,
  p_candidate_family text,
  p_decision_intent text
)
returns jsonb
language plpgsql
set search_path to ''
as $function$
declare
  v_result jsonb;
  v_postmodel text;
  v_final text;
begin
  if upper(coalesce(p_decision_intent,'')) in ('WINNER','BEST_SIDE') then
    v_result := public.wow_v17_mlb_probability_only_governance_bridge(
      p_score_snapshot_id,
      p_research_run_id,
      p_event_key,
      p_requested_timezone,
      p_candidate_family,
      p_decision_intent
    );

    v_postmodel := case
      when coalesce(v_result->>'probability_audit_result','') = 'PASS_PROBABILITY_AUDIT'
       and coalesce(v_result->>'calibration_health_status','') = 'PASS'
       and coalesce(v_result->>'event_mutex_status','') = 'PASS'
      then 'PASS' else 'HOLD' end;

    v_final := case
      when coalesce(v_result->>'final_refresh_status','') = 'PASS'
       and coalesce(v_result->>'global_terminal_reducer','') = 'V17_TERMINAL_REDUCER'
       and coalesce((v_result->>'can_execute')::boolean,false) = false
      then 'PASS' else 'HOLD' end;

    return v_result || jsonb_build_object(
      'postmodel_gates_status', v_postmodel,
      'final_gates_status', v_final
    );
  end if;

  return public.wow_v17_mlb_team_event_governance_bridge_legacy(
    p_score_snapshot_id,
    p_research_run_id,
    p_event_key,
    p_requested_timezone,
    p_candidate_family,
    p_decision_intent
  );
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
