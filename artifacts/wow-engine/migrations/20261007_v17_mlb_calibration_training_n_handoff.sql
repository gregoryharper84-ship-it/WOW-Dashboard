-- Bind exact immutable MLB calibrator training-N before V17 calibration audit.
-- Class B provenance handoff only. No probability/calibration recomputation.

create or replace function public.wow_v17_bind_mlb_score_calibration_metadata(
  p_event_prediction_id uuid,
  p_score_snapshot_id uuid
)
returns jsonb
language plpgsql
set search_path to ''
as $function$
declare
  r public.wow_event_predictions%rowtype;
  s public.wow_mlb_forward_score_snapshots%rowtype;
  c public.wow_mlb_v2d_intercept_calibration%rowtype;
begin
  select * into r
  from public.wow_event_predictions
  where event_prediction_id=p_event_prediction_id
  for update;

  if not found then
    return jsonb_build_object(
      'status','HOLD','code','EVENT_PREDICTION_NOT_FOUND',
      'calibration_metadata_bound',false,'can_execute',false
    );
  end if;

  select * into s
  from public.wow_mlb_forward_score_snapshots
  where score_snapshot_id=p_score_snapshot_id;

  if not found
     or r.scoring_snapshot_id is distinct from p_score_snapshot_id
     or s.calibration_id is null
     or nullif(btrim(s.calibration_method),'') is null then
    return jsonb_build_object(
      'status','HOLD','code','MLB_SCORE_CALIBRATION_IDENTITY_NOT_PROVEN',
      'calibration_metadata_bound',false,'can_execute',false
    );
  end if;

  select * into c
  from public.wow_mlb_v2d_intercept_calibration
  where calibration_id=s.calibration_id;

  if not found
     or c.method is distinct from s.calibration_method
     or r.calibration_version is distinct from c.calibration_id::text
     or r.calibration_method is distinct from c.method
     or c.prior_games is null
     or c.prior_games <= 0 then
    return jsonb_build_object(
      'status','HOLD','code','MLB_CALIBRATION_METADATA_MISMATCH',
      'calibration_metadata_bound',false,'can_execute',false
    );
  end if;

  update public.wow_event_predictions
  set calibration_training_n=c.prior_games
  where event_prediction_id=p_event_prediction_id;

  return jsonb_build_object(
    'status','PASS',
    'code','MLB_IMMUTABLE_CALIBRATION_METADATA_BOUND',
    'calibration_id',c.calibration_id,
    'calibration_method',c.method,
    'calibration_training_n',c.prior_games,
    'probabilities_recomputed',false,
    'calibration_recomputed',false,
    'calibration_metadata_bound',true,
    'can_execute',false
  );
exception
  when others then
    return jsonb_build_object(
      'status','HOLD',
      'code','MLB_CALIBRATION_METADATA_BIND_UNAVAILABLE',
      'error_type',sqlstate,
      'calibration_metadata_bound',false,
      'can_execute',false
    );
end;
$function$;

do $patch$
declare
  fn_oid oid;
  ddl text;
  needle text := 'audit:=public.wow_v17_audit_probability_only_event(event_id); calibration:=public.wow_v17_assess_mlb_event_calibration_health(event_id);';
  replacement text := 'perform public.wow_v17_bind_mlb_score_calibration_metadata(event_id,p_score_snapshot_id); audit:=public.wow_v17_audit_probability_only_event(event_id); calibration:=public.wow_v17_assess_mlb_event_calibration_health(event_id);';
begin
  fn_oid := to_regprocedure(
    'public.wow_v17_mlb_probability_only_governance_bridge(uuid,text,text,text,text,text)'
  )::oid;
  if fn_oid is null then
    raise exception 'V17_REPAIR_TARGET_MISSING: wow_v17_mlb_probability_only_governance_bridge';
  end if;

  ddl := pg_get_functiondef(fn_oid);

  if position('wow_v17_bind_mlb_score_calibration_metadata' in ddl) > 0 then
    null;
  elsif position(needle in ddl) > 0 then
    ddl := replace(ddl,needle,replacement);
    execute ddl;
  else
    raise exception 'V17_REPAIR_SIGNATURE_MISMATCH: audit/calibration seam not found';
  end if;
end;
$patch$;

do $verify$
declare
  helper_ddl text;
  bridge_ddl text;
begin
  helper_ddl := pg_get_functiondef(
    'public.wow_v17_bind_mlb_score_calibration_metadata(uuid,uuid)'::regprocedure
  );
  bridge_ddl := pg_get_functiondef(
    'public.wow_v17_mlb_probability_only_governance_bridge(uuid,text,text,text,text,text)'::regprocedure
  );

  if position('calibration_training_n=c.prior_games' in helper_ddl) = 0
     or position('probabilities_recomputed' in helper_ddl) = 0
     or position('calibration_recomputed' in helper_ddl) = 0 then
    raise exception 'V17_REPAIR_VERIFY_FAILED: immutable calibration bind contract missing';
  end if;
  if position('wow_v17_bind_mlb_score_calibration_metadata' in bridge_ddl) = 0 then
    raise exception 'V17_REPAIR_VERIFY_FAILED: bridge missing calibration metadata bind';
  end if;
end;
$verify$;
