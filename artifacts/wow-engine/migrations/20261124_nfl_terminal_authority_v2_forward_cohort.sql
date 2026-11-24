-- PROTECTED Class C | Incident #1939 | migration candidate, NOT APPLIED
-- Owner decision 2026-11-24 (Greg): NFL forward calibration health governs on the
-- V2 LATEST_VALID_PREKICKOFF_V2 verified cohort. Identical to 20261122 except:
--   exact-cohort reads use public.wow_nfl_forward_verified_grades_v2 (2 places);
--   the health receipt must be schema NFL_FORWARD_CALIBRATION_HEALTH_V3.
-- Thresholds, certification checks and every other blocker are unchanged.
-- Rollback: re-apply 20261122_nfl_independent_certified_terminal_authority.sql.
-- Apply only with Greg's approval of the exact head; can_execute stays false.
create or replace function public.wow_v17_reduce_nfl_event_terminal_label(
  p_score_snapshot_id uuid
)
returns jsonb
language plpgsql
set search_path to ''
as $function$
declare
  r public.wow_nfl_event_predictions%rowtype;
  a public.wow_nfl_event_fitted_model_artifacts%rowtype;
  c public.wow_calibrators%rowtype;
  v_blockers text[] := '{}';
  v_label text := 'MODEL_QUALIFIED_HOLD';
  v_cert jsonb;
  v_certified boolean := false;
  v_certified_at timestamptz;
  v_calibration_sha text;
  v_health public.wow_nfl_forward_calibration_health%rowtype;
  v_exact_grades integer := 0;
  v_distinct_games integer := 0;
  v_exact_brier double precision;
  v_exact_log_loss double precision;
  v_exact_ece double precision;
  v_exact_abs_bias double precision;
  v_exact_lower_gap double precision;
  v_exact_quality boolean := false;
begin
  select * into r from public.wow_nfl_event_predictions where score_snapshot_id=p_score_snapshot_id;
  if not found then
    return jsonb_build_object('status','HOLD','terminal_label','MODEL_QUALIFIED_HOLD','blockers',jsonb_build_array('NFL_SCORE_SNAPSHOT_NOT_FOUND'),'global_terminal_reducer','V17_TERMINAL_REDUCER','probability_publishable',false,'rank_eligible',false,'can_execute',false);
  end if;
  select * into a from public.wow_nfl_event_fitted_model_artifacts where artifact_id=r.model_artifact_id;
  select * into c from public.wow_calibrators where calibrator_id=a.calibrator_id;
  -- The fitted registry stays non-publishing. Authority comes only from
  -- a pre-existing independently signed, immutable specialist certificate.
  v_cert := public.wow_v17_active_team_event_certification('NFL','NFL');
  v_calibration_sha := c.fit_metrics_json->>'calibration_artifact_sha256';
  begin
    v_certified_at := nullif(v_cert->>'certified_at','')::timestamptz;
  exception when others then v_certified_at := null;
  end;
  select * into v_health from public.wow_nfl_forward_calibration_health
    order by generated_at desc,health_id desc limit 1;
  select count(*)::int,count(distinct official_event_id)::int
    into v_exact_grades,v_distinct_games
    from public.wow_nfl_forward_verified_grades_v2
   where model_version=r.model_version
     and model_artifact_id=r.model_artifact_id
     and artifact_checksum=a.artifact_checksum
     and can_execute is false
     and prediction_created_at < event_start_time_utc;
  -- Global health can describe another model. Recalculate the unchanged V2
  -- quality thresholds on the SAME immutable, verified artifact/version cohort.
  -- A direct SQL reducer caller must not borrow global PASS from another cohort.
  with exact_cohort as (
    select g.calibrated_probability::double precision as p,
           g.calibrated_lower_bound::double precision as lb,
           g.outcome::double precision as y,
           g.brier::double precision as brier,
           g.log_loss::double precision as loss
      from public.wow_nfl_forward_verified_grades_v2 g
     where g.model_version=r.model_version
       and g.model_artifact_id=r.model_artifact_id
       and g.artifact_checksum=a.artifact_checksum
       and g.can_execute is false
       and g.prediction_created_at < g.event_start_time_utc
  ), quality as (
    select count(*) as cohort_n, avg(brier) as mean_brier,
           avg(loss) as mean_log_loss,
           abs(avg(p)-avg(y)) as absolute_bias,
           avg(y)-avg(lb) as lower_gap
      from exact_cohort
  ), bins as (
    -- Python calibration_health: ten half-open bins, p=1 in the last bin.
    select least(floor(p*10.0)::integer,9) as bin,
           sum(p-y) as net_probability_error
      from exact_cohort
     group by 1
  )
  select q.mean_brier, q.mean_log_loss,
         (select sum(abs(net_probability_error)) from bins)
           / nullif(q.cohort_n,0)::double precision,
         q.absolute_bias, q.lower_gap
    into v_exact_brier, v_exact_log_loss, v_exact_ece,
         v_exact_abs_bias, v_exact_lower_gap
    from quality q;
  v_exact_quality := coalesce(
    v_exact_brier < 0.25
    and v_exact_log_loss < ln(2.0)
    and v_exact_ece <= 0.12
    and v_exact_abs_bias <= 0.08
    and v_exact_lower_gap >= -0.03,
    false
  );
  v_certified := coalesce(
    (v_cert->>'ok')::boolean
    and v_cert->>'certification_status'='CERTIFIED'
    and v_cert->>'independent_verification_status'='PASS'
    and v_cert->>'probability_publishable'='true'
    and v_cert->>'can_execute'='false'
    and v_cert->>'sport'='NFL'
    and v_cert->>'league_scope'='NFL'
    and v_cert->>'controlling_specialist'='wow.nfl-game-win-probability-expert'
    and v_cert->>'model_family'=r.model_family
    and v_cert->>'model_version'=r.model_version
    and v_cert->>'artifact_id'=a.artifact_id::text
    and v_cert->>'artifact_sha256'=a.artifact_checksum
    and v_cert->>'feature_schema_version'=a.feature_schema_version
    and v_cert->>'calibration_artifact_id'=c.calibrator_id::text
    and v_cert->>'calibration_method'=c.calibration_method
    -- A dataset hash cannot substitute for a calibrator-artifact hash.
    and length(v_calibration_sha)=64
    and v_calibration_sha ~ '^[0-9a-f]{64}$'
    and v_cert->>'calibration_sha256'=v_calibration_sha
    and v_certified_at is not null
    and v_certified_at<=r.model_timestamp
    and v_health.schema_version='NFL_FORWARD_CALIBRATION_HEALTH_V3'
    and v_health.status='PASS'
    and v_health.certification_recommendation='FORWARD_EVIDENCE_PASS'
    and v_health.blockers='[]'::jsonb
    and v_health.minimum_forward_required>=100
    and v_health.graded_n>=v_health.minimum_forward_required
    and v_exact_grades=v_distinct_games
    and v_distinct_games>=v_health.minimum_forward_required
    and v_exact_quality,
    false
  );

  if r.event_start_time_utc <= now() then v_blockers:=array_append(v_blockers,'EVENT_NOT_PREGAME'); end if;
  if not r.model_package_valid then v_blockers:=array_append(v_blockers,'MODEL_PACKAGE_INVALID'); end if;
  if not r.provenance_complete then v_blockers:=array_append(v_blockers,'PROVENANCE_INCOMPLETE'); end if;
  if not v_certified then
    v_blockers:=array_append(v_blockers,'NFL_INDEPENDENT_SPECIALIST_CERTIFICATION_UNPROVEN');
  end if;
  if not v_exact_quality then
    v_blockers:=array_append(v_blockers,'NFL_EXACT_COHORT_CALIBRATION_NOT_PASS');
  end if;
  if not r.model_probability_publishable and not v_certified then
    v_blockers:=array_append(v_blockers,'MODEL_PROBABILITY_NOT_PUBLISHABLE');
  end if;
  if r.calibration_health_status <> 'PASS' then v_blockers:=array_append(v_blockers,'CALIBRATION_HEALTH_NOT_PASS'); end if;
  if r.latest_material_update_timestamp is not null and r.model_timestamp < r.latest_material_update_timestamp then v_blockers:=array_append(v_blockers,'MODEL_STALE_AFTER_MATERIAL_UPDATE'); end if;
  if a.artifact_id is null or not a.active or not a.promoted or a.lifecycle_state <> 'CHAMPION' then v_blockers:=array_append(v_blockers,'NFL_CHAMPION_ARTIFACT_NOT_ACTIVE'); end if;
  if c.calibrator_id is null or not c.active or not c.promoted or c.validation_status <> 'PASS' or c.health_status <> 'PASS' then v_blockers:=array_append(v_blockers,'NFL_CALIBRATOR_NOT_ACTIVE_PASS'); end if;
  if r.can_execute then v_blockers:=array_append(v_blockers,'CAN_EXECUTE_MUST_BE_FALSE'); end if;
  if r.blend_publishable then v_blockers:=array_append(v_blockers,'UNCERTIFIED_BLEND_PUBLICATION_BLOCKED'); end if;

  if cardinality(v_blockers)=0 then v_label:='FINAL_APPROVED'; end if;
  return jsonb_build_object(
    'status',case when v_label='FINAL_APPROVED' then 'PASS' else 'HOLD' end,
    'terminal_label',v_label,
    'blockers',to_jsonb(v_blockers),
    'global_terminal_reducer','V17_TERMINAL_REDUCER',
    'probability_publishable',(v_label='FINAL_APPROVED'),
    'rank_eligible',(v_label='FINAL_APPROVED'),
    'can_execute',false
  );
end;
$function$;
