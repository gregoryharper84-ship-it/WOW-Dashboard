-- WOW MLB V2D governed promotion machinery — REVIEW/APPLY SEPARATELY.
-- No probability is published by installing these functions. They only replace
-- undocumented/manual table edits with server-only, fail-closed transitions.

begin;

create or replace function public.wow_mlb_promote_runtime_capability_if_eligible(p_spec_id uuid)
returns jsonb
language plpgsql
set search_path to ''
as $function$
declare
  fs public.wow_mlb_v2d_frozen_spec%rowtype;
  h public.wow_mlb_v2d_calibration_health%rowtype;
  v_gate_n integer;
  v_gate_pass_n integer;
  v_g11 text;
begin
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('wow_mlb_v2d_promote:'||p_spec_id::text,0));

  select * into fs from public.wow_mlb_v2d_frozen_spec where spec_id=p_spec_id and status='RESEARCH_FROZEN';
  if not found or coalesce(fs.spec_sha256,'')='' or coalesce(fs.training_data_sha256,'')='' or fs.calibration_id is null then
    return jsonb_build_object('status','BLOCKED','code','FROZEN_SPEC_IDENTITY_INCOMPLETE','probability_publishable',false,'can_execute',false);
  end if;

  select * into h from public.wow_mlb_v2d_calibration_health where spec_id=p_spec_id order by assessed_at desc limit 1;
  if not found
     or h.calibration_health_status<>'PASS'
     or h.forward_shadow_status<>'SUFFICIENT_FOR_REVIEW'
     or coalesce(h.pending_shadow_n,0)<>0
     or coalesce(h.eligible_shadow_n,0)<=0
     or h.timestamped_pregame_provenance_status<>'AVAILABLE' then
    return jsonb_build_object(
      'status','BLOCKED','code','CALIBRATION_HEALTH_NOT_PROMOTABLE',
      'calibration_health_status',coalesce(h.calibration_health_status,'UNAVAILABLE'),
      'forward_shadow_status',coalesce(h.forward_shadow_status,'UNAVAILABLE'),
      'pending_shadow_n',h.pending_shadow_n,
      'probability_publishable',false,'can_execute',false
    );
  end if;

  select count(*),count(*) filter(where status='PASS') into v_gate_n,v_gate_pass_n from public.wow_governed_deployment_gates;
  select reason into v_g11 from public.wow_governed_deployment_gates where gate_id='G11';
  if v_gate_n<>11 or v_gate_pass_n<>11 or coalesce(v_g11,'')='' then
    return jsonb_build_object('status','BLOCKED','code','DEPLOYMENT_GATES_NOT_PASS','gate_count',v_gate_n,'pass_count',v_gate_pass_n,'probability_publishable',false,'can_execute',false);
  end if;

  update public.wow_runtime_capabilities
  set capability_status='AVAILABLE',updated_at=clock_timestamp(),can_execute=false,
      evidence=jsonb_build_object(
        'promotion','WOW_MLB_V2D_GOVERNED_HEALTH_PROMOTION',
        'spec_id',fs.spec_id,'spec_version',fs.spec_version,'spec_sha256',fs.spec_sha256,
        'training_data_sha256',fs.training_data_sha256,'calibration_id',fs.calibration_id,
        'calibration_health_assessed_at',h.assessed_at,'graded_shadow_n',h.graded_shadow_n,
        'eligible_shadow_n',h.eligible_shadow_n,'g11_reason',v_g11
      )
  where capability_key='MLB_EVENT_PROBABILITY';
  if not found then
    return jsonb_build_object('status','BLOCKED','code','RUNTIME_CAPABILITY_ROW_MISSING','probability_publishable',false,'can_execute',false);
  end if;

  return jsonb_build_object(
    'status','CAPABILITY_AVAILABLE_FOR_RATIFICATION',
    'spec_id',fs.spec_id,'calibration_health_assessed_at',h.assessed_at,
    'runtime_capability_status','AVAILABLE',
    'probability_publishable',false,'can_execute',false
  );
end;
$function$;

create or replace function public.wow_mlb_ratify_publication_if_eligible(p_spec_id uuid,p_evidence jsonb)
returns jsonb
language plpgsql
set search_path to ''
as $function$
declare
  h public.wow_mlb_v2d_calibration_health%rowtype;
  fs public.wow_mlb_v2d_frozen_spec%rowtype;
  v_cap text;
  v_ratification_id uuid;
  v_state jsonb;
begin
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('wow_mlb_v2d_ratify:'||p_spec_id::text,0));
  if jsonb_typeof(p_evidence) is distinct from 'object' or p_evidence='{}'::jsonb then
    return jsonb_build_object('status','BLOCKED','code','RATIFICATION_EVIDENCE_REQUIRED','probability_publishable',false,'can_execute',false);
  end if;

  select * into fs from public.wow_mlb_v2d_frozen_spec where spec_id=p_spec_id and status='RESEARCH_FROZEN';
  if not found then
    return jsonb_build_object('status','BLOCKED','code','FROZEN_SPEC_UNAVAILABLE','probability_publishable',false,'can_execute',false);
  end if;
  select * into h from public.wow_mlb_v2d_calibration_health where spec_id=p_spec_id order by assessed_at desc limit 1;
  select capability_status into v_cap from public.wow_runtime_capabilities where capability_key='MLB_EVENT_PROBABILITY';
  if h.calibration_health_status is distinct from 'PASS' or v_cap is distinct from 'AVAILABLE' then
    return jsonb_build_object('status','BLOCKED','code','PROMOTION_PREREQUISITES_NOT_PASS','calibration_health_status',h.calibration_health_status,'runtime_capability_status',v_cap,'probability_publishable',false,'can_execute',false);
  end if;

  insert into public.wow_mlb_publication_ratification(
    spec_id,decision,production_feature_ready,probability_publishable,calibration_health_assessed_at,evidence,can_execute
  ) values (
    p_spec_id,'RATIFIED',true,true,h.assessed_at,
    p_evidence || jsonb_build_object(
      'spec_version',fs.spec_version,'spec_sha256',fs.spec_sha256,
      'training_data_sha256',fs.training_data_sha256,'calibration_id',fs.calibration_id,
      'ratification_path','WOW_MLB_V2D_GOVERNED_RPC'
    ),false
  ) returning ratification_id into v_ratification_id;

  v_state:=public.wow_governed_deployment_state();
  if coalesce(v_state->>'governed_probability_capability','UNAVAILABLE')<>'AVAILABLE'
     or not coalesce((v_state->>'probability_publishable')::boolean,false) then
    raise exception 'ratification inserted but governed deployment state did not become publishable';
  end if;

  return jsonb_build_object(
    'status','RATIFIED','ratification_id',v_ratification_id,'spec_id',p_spec_id,
    'governed_probability_capability',v_state->>'governed_probability_capability',
    'probability_publishable',true,'can_execute',false
  );
end;
$function$;

revoke all on function public.wow_mlb_promote_runtime_capability_if_eligible(uuid) from public,anon,authenticated;
revoke all on function public.wow_mlb_ratify_publication_if_eligible(uuid,jsonb) from public,anon,authenticated;
grant execute on function public.wow_mlb_promote_runtime_capability_if_eligible(uuid) to service_role;
grant execute on function public.wow_mlb_ratify_publication_if_eligible(uuid,jsonb) to service_role;

commit;
