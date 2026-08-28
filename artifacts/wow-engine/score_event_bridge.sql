-- WOW v16 Clean Core — MLB /score-event real fitted-model bridge
-- Applied to Supabase wow-engine-validation on 2026-08-28.
--
-- This bridge proves the production API can reach the existing frozen MLB
-- fitted scorer without publishing held probabilities. It validates exact
-- event identity against the server-side pregame snapshot, requires the
-- fitted/calibrated score artifact and predictive bounds to be internally
-- valid, and returns provenance/blocker metadata only while publication is
-- blocked. can_execute remains false.

create or replace function public.wow_mlb_score_event_bridge(
  p_official_event_id text,
  p_event_start_time timestamptz,
  p_requested_slate_date date,
  p_home_team text,
  p_away_team text,
  p_venue text,
  p_home_starting_pitcher text,
  p_away_starting_pitcher text,
  p_source_snapshot_id uuid
) returns jsonb
language plpgsql
set search_path to ''
as $function$
declare
  e public.wow_mlb_forward_shadow_events%rowtype;
  s public.wow_mlb_forward_score_snapshots%rowtype;
  h public.wow_mlb_v2d_calibration_health%rowtype;
  v_gate jsonb;
  v_score_result jsonb;
  v_blockers text[] := '{}';
  v_identity_errors text[] := '{}';
  v_internal_valid boolean := false;
begin
  if p_official_event_id is null or btrim(p_official_event_id)='' then
    return jsonb_build_object('status','BLOCKED','code','OFFICIAL_EVENT_ID_MISSING','scoring_evidence_produced',false,'probability_publishable',false,'can_execute',false);
  end if;
  if p_event_start_time is null or p_event_start_time <= now() then
    return jsonb_build_object('status','BLOCKED','code','EVENT_NOT_PREGAME','scoring_evidence_produced',false,'probability_publishable',false,'can_execute',false);
  end if;
  if p_requested_slate_date is null then
    return jsonb_build_object('status','BLOCKED','code','SLATE_DATE_MISSING','scoring_evidence_produced',false,'probability_publishable',false,'can_execute',false);
  end if;

  select e0.* into e
  from public.wow_mlb_forward_shadow_events e0
  join public.wow_mlb_v2d_frozen_spec fs on fs.spec_id=e0.spec_id and fs.status='RESEARCH_FROZEN'
  where e0.official_event_id=p_official_event_id
    and e0.official_date=p_requested_slate_date
    and e0.event_start_time=p_event_start_time
    and e0.snapshot_timestamp is not null
    and e0.snapshot_timestamp < e0.event_start_time
  order by
    case when e0.snapshot_id=p_source_snapshot_id then 0 else 1 end,
    case when e0.model_score_status like 'SHADOW_SCORED%' then 0 else 1 end,
    e0.snapshot_timestamp desc
  limit 1;

  if not found then
    return jsonb_build_object('status','BLOCKED','code','FROZEN_EVENT_SNAPSHOT_NOT_FOUND','scoring_evidence_produced',false,'probability_publishable',false,'can_execute',false);
  end if;

  if btrim(lower(e.home_team)) <> btrim(lower(p_home_team)) then v_identity_errors:=array_append(v_identity_errors,'HOME_TEAM_MISMATCH'); end if;
  if btrim(lower(e.away_team)) <> btrim(lower(p_away_team)) then v_identity_errors:=array_append(v_identity_errors,'AWAY_TEAM_MISMATCH'); end if;
  if btrim(lower(coalesce(e.venue_name,''))) <> btrim(lower(coalesce(p_venue,''))) then v_identity_errors:=array_append(v_identity_errors,'VENUE_MISMATCH'); end if;
  if btrim(lower(coalesce(e.home_probable_pitcher,''))) <> btrim(lower(coalesce(p_home_starting_pitcher,''))) then v_identity_errors:=array_append(v_identity_errors,'HOME_STARTER_MISMATCH'); end if;
  if btrim(lower(coalesce(e.away_probable_pitcher,''))) <> btrim(lower(coalesce(p_away_starting_pitcher,''))) then v_identity_errors:=array_append(v_identity_errors,'AWAY_STARTER_MISMATCH'); end if;

  if cardinality(v_identity_errors)>0 then
    return jsonb_build_object(
      'status','BLOCKED','code','EVENT_IDENTITY_MISMATCH','identity_errors',to_jsonb(v_identity_errors),
      'server_snapshot_id',e.snapshot_id,'server_snapshot_timestamp',e.snapshot_timestamp,
      'scoring_evidence_produced',false,'probability_publishable',false,'can_execute',false
    );
  end if;

  if e.feature_hydration_status <> 'PASS' then
    return jsonb_build_object(
      'status','BLOCKED','code','FEATURE_HYDRATION_NOT_PASS','feature_hydration_status',e.feature_hydration_status,
      'shadow_event_id',e.shadow_event_id,'server_snapshot_id',e.snapshot_id,
      'scoring_evidence_produced',false,'probability_publishable',false,'can_execute',false
    );
  end if;

  select * into s
  from public.wow_mlb_forward_score_snapshots
  where shadow_event_id=e.shadow_event_id
  order by created_at desc
  limit 1;

  if not found then
    v_score_result:=public.wow_mlb_forward_score_event(e.shadow_event_id);
    if coalesce(v_score_result->>'status','') not like 'SHADOW_SCORED%' then
      return jsonb_build_object(
        'status','BLOCKED','code','MODEL_SCORING_BLOCKED','shadow_event_id',e.shadow_event_id,
        'scorer_status',v_score_result->>'status','scorer_reason',v_score_result->>'reason',
        'scoring_evidence_produced',false,'probability_publishable',false,'can_execute',false
      );
    end if;
    select * into s
    from public.wow_mlb_forward_score_snapshots
    where shadow_event_id=e.shadow_event_id
    order by created_at desc
    limit 1;
  end if;

  v_internal_valid :=
    s.score_snapshot_id is not null
    and s.model_timestamp < e.event_start_time
    and s.raw_home_probability > 0 and s.raw_home_probability < 1
    and s.raw_away_probability > 0 and s.raw_away_probability < 1
    and abs((s.raw_home_probability+s.raw_away_probability)-1.0) <= 0.000001
    and s.calibrated_home_probability > 0 and s.calibrated_home_probability < 1
    and s.calibrated_away_probability > 0 and s.calibrated_away_probability < 1
    and abs((s.calibrated_home_probability+s.calibrated_away_probability)-1.0) <= 0.000001
    and s.home_lower_bound > 0 and s.home_lower_bound <= s.calibrated_home_probability
    and s.home_upper_bound >= s.calibrated_home_probability and s.home_upper_bound < 1
    and s.away_lower_bound > 0 and s.away_lower_bound <= s.calibrated_away_probability
    and s.away_upper_bound >= s.calibrated_away_probability and s.away_upper_bound < 1
    and coalesce(s.model_version,'') <> ''
    and coalesce(s.training_data_sha256,'') <> '';

  if not v_internal_valid then
    return jsonb_build_object(
      'status','BLOCKED','code','SCORED_MODEL_VALIDATION_FAILED','shadow_event_id',e.shadow_event_id,
      'score_snapshot_id',s.score_snapshot_id,'scoring_evidence_produced',false,
      'probability_publishable',false,'can_execute',false
    );
  end if;

  select * into h
  from public.wow_mlb_v2d_calibration_health
  where spec_id=e.spec_id
  order by assessed_at desc
  limit 1;

  v_gate:=public.wow_governed_deployment_state();
  v_blockers:=coalesce(s.blockers,'{}');
  if h.spec_id is null then
    v_blockers:=array_append(v_blockers,'CALIBRATION_HEALTH_UNAVAILABLE');
  elsif h.calibration_health_status <> 'PASS' then
    v_blockers:=array_cat(v_blockers,coalesce(h.blockers,'{}'));
  end if;
  if coalesce(v_gate->>'governed_probability_capability','UNAVAILABLE') <> 'AVAILABLE' then
    v_blockers:=array_append(v_blockers,'GOVERNED_PROBABILITY_CAPABILITY_UNAVAILABLE');
  end if;

  select array_agg(distinct x order by x) into v_blockers
  from unnest(coalesce(v_blockers,'{}')) x;

  return jsonb_build_object(
    'status','MODEL_SCORED_HELD',
    'code','REAL_FITTED_MODEL_PATH_PROVEN',
    'controlling_specialist','wow.mlb-game-win-probability-expert',
    'shadow_event_id',e.shadow_event_id,
    'score_snapshot_id',s.score_snapshot_id,
    'spec_id',e.spec_id,
    'server_snapshot_id',e.snapshot_id,
    'server_snapshot_timestamp',e.snapshot_timestamp,
    'requested_source_snapshot_id',p_source_snapshot_id,
    'model_timestamp',s.model_timestamp,
    'model_version',s.model_version,
    'training_data_sha256',s.training_data_sha256,
    'score_status',s.score_status,
    'feature_hydration_status',e.feature_hydration_status,
    'lineup_status',e.lineup_status,
    'calibration_health_status',coalesce(h.calibration_health_status,'UNAVAILABLE'),
    'calibration_blockers',coalesce(to_jsonb(h.blockers),'[]'::jsonb),
    'governed_probability_capability',coalesce(v_gate->>'governed_probability_capability','UNAVAILABLE'),
    'blockers',to_jsonb(coalesce(v_blockers,'{}')),
    'scoring_evidence_produced',true,
    'probability_fields_withheld',true,
    'probability_publishable',false,
    'can_execute',false
  );
end;
$function$;
