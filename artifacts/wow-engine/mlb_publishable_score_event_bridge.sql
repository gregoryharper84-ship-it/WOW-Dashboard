-- WOW MLB governed /score-event dual-mode bridge — 2026-08-28
--
-- Held mode preserves the existing G11 proof behavior: fitted-model evidence is
-- produced, but numeric probabilities stay withheld. Publishable mode is only
-- reachable when wow_governed_deployment_state() says publication is ratified.
-- Even then this function re-checks the latest material event snapshot, performs
-- an official lineup refresh at request time, requires a post-lineup score, and
-- keeps can_execute=false.

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
  l public.wow_mlb_forward_lineup_snapshots%rowtype;
  v_gate jsonb;
  v_score_result jsonb;
  v_lineup_refresh jsonb;
  v_identity_errors text[] := '{}';
  v_current_blockers text[] := '{}';
  v_score_time_blockers text[] := '{}';
  v_internal_valid boolean := false;
  v_publication_attempt boolean := false;
  v_lineup_refresh_ok boolean := false;
  v_publishable boolean := false;
begin
  if p_official_event_id is null or btrim(p_official_event_id)='' then
    return jsonb_build_object(
      'status','BLOCKED','code','OFFICIAL_EVENT_ID_MISSING',
      'scoring_evidence_produced',false,'probability_publishable',false,'can_execute',false
    );
  end if;
  if p_event_start_time is null or p_event_start_time <= clock_timestamp() then
    return jsonb_build_object(
      'status','BLOCKED','code','EVENT_NOT_PREGAME',
      'scoring_evidence_produced',false,'probability_publishable',false,'can_execute',false
    );
  end if;
  if p_requested_slate_date is null then
    return jsonb_build_object(
      'status','BLOCKED','code','SLATE_DATE_MISSING',
      'scoring_evidence_produced',false,'probability_publishable',false,'can_execute',false
    );
  end if;

  -- Always select the latest material pregame snapshot. A caller cannot pin the
  -- bridge to an older snapshot after a starter/time/venue/team change.
  select e0.* into e
  from public.wow_mlb_forward_shadow_events e0
  join public.wow_mlb_v2d_frozen_spec fs
    on fs.spec_id=e0.spec_id and fs.status='RESEARCH_FROZEN'
  where e0.official_event_id=p_official_event_id
    and e0.official_date=p_requested_slate_date
    and e0.event_start_time=p_event_start_time
    and e0.snapshot_timestamp is not null
    and e0.snapshot_timestamp < e0.event_start_time
  order by e0.snapshot_timestamp desc,e0.shadow_event_id desc
  limit 1;

  if not found then
    return jsonb_build_object(
      'status','BLOCKED','code','FROZEN_EVENT_SNAPSHOT_NOT_FOUND',
      'scoring_evidence_produced',false,'probability_publishable',false,'can_execute',false
    );
  end if;

  if e.snapshot_id is distinct from p_source_snapshot_id then
    v_identity_errors:=array_append(v_identity_errors,'SOURCE_SNAPSHOT_STALE');
  end if;
  if btrim(lower(e.home_team)) <> btrim(lower(p_home_team)) then
    v_identity_errors:=array_append(v_identity_errors,'HOME_TEAM_MISMATCH');
  end if;
  if btrim(lower(e.away_team)) <> btrim(lower(p_away_team)) then
    v_identity_errors:=array_append(v_identity_errors,'AWAY_TEAM_MISMATCH');
  end if;
  if btrim(lower(coalesce(e.venue_name,''))) <> btrim(lower(coalesce(p_venue,''))) then
    v_identity_errors:=array_append(v_identity_errors,'VENUE_MISMATCH');
  end if;
  if btrim(lower(coalesce(e.home_probable_pitcher,''))) <> btrim(lower(coalesce(p_home_starting_pitcher,''))) then
    v_identity_errors:=array_append(v_identity_errors,'HOME_STARTER_MISMATCH');
  end if;
  if btrim(lower(coalesce(e.away_probable_pitcher,''))) <> btrim(lower(coalesce(p_away_starting_pitcher,''))) then
    v_identity_errors:=array_append(v_identity_errors,'AWAY_STARTER_MISMATCH');
  end if;

  if cardinality(v_identity_errors)>0 then
    return jsonb_build_object(
      'status','BLOCKED','code','EVENT_IDENTITY_MISMATCH',
      'identity_errors',to_jsonb(v_identity_errors),
      'server_snapshot_id',e.snapshot_id,'server_snapshot_timestamp',e.snapshot_timestamp,
      'scoring_evidence_produced',false,'probability_publishable',false,'can_execute',false
    );
  end if;

  if e.feature_hydration_status <> 'PASS' then
    return jsonb_build_object(
      'status','BLOCKED','code','FEATURE_HYDRATION_NOT_PASS',
      'feature_hydration_status',e.feature_hydration_status,
      'shadow_event_id',e.shadow_event_id,'server_snapshot_id',e.snapshot_id,
      'scoring_evidence_produced',false,'probability_publishable',false,'can_execute',false
    );
  end if;

  v_gate:=public.wow_governed_deployment_state();
  v_publication_attempt :=
    coalesce(v_gate->>'governed_probability_capability','UNAVAILABLE')='AVAILABLE'
    and coalesce((v_gate->>'probability_publishable')::boolean,false);

  -- Publication mode performs a synchronous official-lineup final refresh.
  -- If the batting order changed, the lineup function creates immutable
  -- provenance and re-scores before this bridge can publish anything.
  if v_publication_attempt then
    v_lineup_refresh:=public.wow_mlb_forward_confirm_lineup(e.shadow_event_id);
    if coalesce(v_lineup_refresh->>'status','') in (
      'CONFIRMED','CONFIRMED_LINEUP_CHANGED','CONFIRMED_FROM_EXISTING_SNAPSHOT','UNCHANGED_CONFIRMED_LINEUP'
    ) then
      v_lineup_refresh_ok:=true;
    else
      v_current_blockers:=array_append(
        v_current_blockers,
        'OFFICIAL_LINEUP_REFRESH_' || coalesce(v_lineup_refresh->>'reason',v_lineup_refresh->>'status','FAILED')
      );
    end if;

    -- Re-read event state after the refresh/rescore transaction.
    select e1.* into e
    from public.wow_mlb_forward_shadow_events e1
    where e1.shadow_event_id=e.shadow_event_id;
  end if;

  select * into s
  from public.wow_mlb_forward_score_snapshots
  where shadow_event_id=e.shadow_event_id
  order by model_timestamp desc,created_at desc,score_snapshot_id desc
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
    order by model_timestamp desc,created_at desc,score_snapshot_id desc
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
      'status','BLOCKED','code','SCORED_MODEL_VALIDATION_FAILED',
      'shadow_event_id',e.shadow_event_id,'score_snapshot_id',s.score_snapshot_id,
      'scoring_evidence_produced',false,'probability_publishable',false,'can_execute',false
    );
  end if;

  select * into h
  from public.wow_mlb_v2d_calibration_health
  where spec_id=e.spec_id
  order by assessed_at desc
  limit 1;

  -- Re-read the multi-latch gate after the external lineup fetch/rescore. A
  -- revocation, health change, runtime-capability change, or new ratification
  -- during the request must fail closed. A mid-request promotion also does not
  -- publish because v_publication_attempt must already have been true.
  v_gate:=public.wow_governed_deployment_state();
  v_publishable :=
    v_publication_attempt
    and v_lineup_refresh_ok
    and coalesce(v_gate->>'governed_probability_capability','UNAVAILABLE')='AVAILABLE'
    and coalesce((v_gate->>'probability_publishable')::boolean,false);

  v_score_time_blockers:=coalesce(s.blockers,'{}');

  -- Current publication blockers are recomputed now. Score-time blockers are
  -- preserved separately for audit because older shadow scores were correctly
  -- stamped while calibration/readiness was still blocked.
  if h.spec_id is null then
    v_current_blockers:=array_append(v_current_blockers,'CALIBRATION_HEALTH_UNAVAILABLE');
  elsif h.calibration_health_status <> 'PASS' then
    v_current_blockers:=array_cat(v_current_blockers,coalesce(h.blockers,'{}'));
  end if;
  if coalesce(v_gate->>'deployment_contract_status','FAIL')<>'PASS' then
    v_current_blockers:=array_append(v_current_blockers,'DEPLOYMENT_CONTRACT_NOT_PASS');
  end if;
  if coalesce(v_gate->>'runtime_capability_status','UNAVAILABLE')<>'AVAILABLE' then
    v_current_blockers:=array_append(v_current_blockers,'MLB_EVENT_PROBABILITY_RUNTIME_CAPABILITY_UNAVAILABLE');
  end if;
  if coalesce(v_gate->>'ratification_status','NOT_RATIFIED')<>'RATIFIED' then
    v_current_blockers:=array_append(v_current_blockers,'PUBLICATION_NOT_RATIFIED');
  end if;
  if not coalesce((v_gate->>'production_feature_ready')::boolean,false) then
    v_current_blockers:=array_append(v_current_blockers,'PRODUCTION_FEATURE_READY_FALSE');
  end if;
  if not coalesce((v_gate->>'probability_publishable')::boolean,false) then
    v_current_blockers:=array_append(v_current_blockers,'GOVERNED_PROBABILITY_NOT_PUBLISHABLE');
  end if;
  if e.lineup_status<>'CONFIRMED' or e.lineup_snapshot_id is null or e.lineup_confirmed_at is null then
    v_current_blockers:=array_append(v_current_blockers,'LINEUP_NOT_CONFIRMED');
  else
    select * into l
    from public.wow_mlb_forward_lineup_snapshots
    where lineup_snapshot_id=e.lineup_snapshot_id
      and shadow_event_id=e.shadow_event_id
    limit 1;
    if not found
       or not l.strict_pregame_provenance
       or l.official_pitch_events_at_capture<>0
       or l.official_completed_plays_at_capture<>0
       or l.captured_at>=e.event_start_time then
      v_current_blockers:=array_append(v_current_blockers,'LINEUP_STRICT_PREGAME_PROVENANCE_INVALID');
    end if;
  end if;
  if s.lineup_status_at_score<>'CONFIRMED'
     or e.lineup_confirmed_at is null
     or s.model_timestamp<e.lineup_confirmed_at then
    v_current_blockers:=array_append(v_current_blockers,'POST_LINEUP_SCORE_SNAPSHOT_REQUIRED');
  end if;

  select coalesce(array_agg(distinct x order by x),'{}'::text[])
  into v_current_blockers
  from unnest(coalesce(v_current_blockers,'{}')) x;

  if v_publishable and cardinality(v_current_blockers)=0 then
    return jsonb_build_object(
      'status','MODEL_SCORED_PUBLISHABLE',
      'code','GOVERNED_PROBABILITY_PUBLISHED',
      'controlling_specialist','wow.mlb-game-win-probability-expert',
      'shadow_event_id',e.shadow_event_id,
      'score_snapshot_id',s.score_snapshot_id,
      'spec_id',e.spec_id,
      'server_snapshot_id',e.snapshot_id,
      'server_snapshot_timestamp',e.snapshot_timestamp,
      'lineup_snapshot_id',e.lineup_snapshot_id,
      'lineup_confirmed_at',e.lineup_confirmed_at,
      'model_timestamp',s.model_timestamp,
      'model_version',s.model_version,
      'training_data_sha256',s.training_data_sha256,
      'score_status',s.score_status,
      'feature_hydration_status',e.feature_hydration_status,
      'lineup_status',e.lineup_status,
      'calibration_health_status',h.calibration_health_status,
      'ratification_status',v_gate->>'ratification_status',
      'ratification_id',v_gate->>'ratification_id',
      'current_publication_blockers','[]'::jsonb,
      'score_time_blockers',to_jsonb(v_score_time_blockers),
      'raw_home_probability',s.raw_home_probability,
      'raw_away_probability',s.raw_away_probability,
      'calibrated_home_probability',s.calibrated_home_probability,
      'calibrated_away_probability',s.calibrated_away_probability,
      'calibrated_home_lower_bound',s.home_lower_bound,
      'calibrated_home_upper_bound',s.home_upper_bound,
      'calibrated_away_lower_bound',s.away_lower_bound,
      'calibrated_away_upper_bound',s.away_upper_bound,
      'projected_runs_home',s.home_mu,
      'projected_runs_away',s.away_mu,
      'tie_after_9_probability',s.tie_after_9_probability,
      'scoring_evidence_produced',true,
      'probability_fields_withheld',false,
      'probability_publishable',true,
      'can_execute',false
    );
  end if;

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
    'governed_probability_capability',coalesce(v_gate->>'governed_probability_capability','UNAVAILABLE'),
    'ratification_status',coalesce(v_gate->>'ratification_status','NOT_RATIFIED'),
    'current_publication_blockers',to_jsonb(v_current_blockers),
    'score_time_blockers',to_jsonb(v_score_time_blockers),
    'scoring_evidence_produced',true,
    'probability_fields_withheld',true,
    'probability_publishable',false,
    'can_execute',false
  );
end;
$function$;
