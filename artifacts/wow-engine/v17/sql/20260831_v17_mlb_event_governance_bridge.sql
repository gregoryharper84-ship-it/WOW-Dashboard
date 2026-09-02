-- WOW v17 Phase A: additive LLP team/event governance bridge.
-- This migration does not activate v17, authorize execution, or alter v16 routes.
-- It links a server-owned MLB fitted score snapshot into the existing event ledger,
-- runs the existing pre/post/final event gates, and adds the v17 quantitative team
-- failure-path requirement before any v17 bridge response can report PASS.

create or replace function public.wow_v17_team_failure_path_gate(
  p_event_prediction_id uuid
)
returns jsonb
language plpgsql
set search_path to ''
as $function$
declare
  r public.wow_event_predictions%rowtype;
  reasons text[] := '{}';
  fav jsonb;
  upset jsonb;
  fav_normal numeric;
  fav_unconditional numeric;
  upset_normal numeric;
  upset_unconditional numeric;
begin
  select * into r
  from public.wow_event_predictions
  where event_prediction_id = p_event_prediction_id;

  if not found then
    raise exception 'event prediction not found';
  end if;

  fav := r.favorite_failure_paths_json;
  upset := r.underdog_upset_path_json;

  if fav is null or jsonb_typeof(fav) <> 'object' then
    reasons := array_append(reasons, 'FAVORITE_FAILURE_PATHS_MISSING');
  else
    if fav->>'schema_version' is distinct from 'WOW_V17_TEAM_EVENT_FAILURE_PATH_V1' then
      reasons := array_append(reasons, 'FAVORITE_FAILURE_PATH_SCHEMA_UNPROVEN');
    end if;
    if jsonb_typeof(fav->'regimes') <> 'array' or jsonb_array_length(coalesce(fav->'regimes','[]'::jsonb)) = 0 then
      reasons := array_append(reasons, 'FAVORITE_FAILURE_REGIMES_MISSING');
    end if;
    if jsonb_typeof(fav->'normal_regime_probability') <> 'number'
       or jsonb_typeof(fav->'unconditional_probability') <> 'number' then
      reasons := array_append(reasons, 'FAVORITE_FAILURE_PROBABILITY_COMPONENTS_MISSING');
    else
      fav_normal := (fav->>'normal_regime_probability')::numeric;
      fav_unconditional := (fav->>'unconditional_probability')::numeric;
      if not (fav_normal > 0 and fav_normal < 1 and fav_unconditional > 0 and fav_unconditional < 1) then
        reasons := array_append(reasons, 'FAVORITE_FAILURE_PROBABILITY_DOMAIN_INVALID');
      elsif abs(fav_unconditional - fav_normal) <= 0.000000001 then
        reasons := array_append(reasons, 'FAVORITE_FAILURE_PATH_DOES_NOT_CHANGE_UNCONDITIONAL_PROBABILITY');
      end if;
    end if;
  end if;

  if r.favorite_failure_path_probability is null
     or not (r.favorite_failure_path_probability > 0 and r.favorite_failure_path_probability < 1) then
    reasons := array_append(reasons, 'FAVORITE_FAILURE_PATH_PROBABILITY_MISSING');
  end if;
  if nullif(btrim(coalesce(r.largest_favorite_loss_path,'')), '') is null then
    reasons := array_append(reasons, 'LARGEST_FAVORITE_LOSS_PATH_MISSING');
  end if;

  if upset is null or jsonb_typeof(upset) <> 'object' then
    reasons := array_append(reasons, 'UNDERDOG_UPSET_PATH_MISSING');
  else
    if upset->>'schema_version' is distinct from 'WOW_V17_TEAM_EVENT_FAILURE_PATH_V1' then
      reasons := array_append(reasons, 'UNDERDOG_UPSET_PATH_SCHEMA_UNPROVEN');
    end if;
    if jsonb_typeof(upset->'regimes') <> 'array' or jsonb_array_length(coalesce(upset->'regimes','[]'::jsonb)) = 0 then
      reasons := array_append(reasons, 'UNDERDOG_UPSET_REGIMES_MISSING');
    end if;
    if jsonb_typeof(upset->'normal_regime_probability') <> 'number'
       or jsonb_typeof(upset->'unconditional_probability') <> 'number' then
      reasons := array_append(reasons, 'UNDERDOG_UPSET_PROBABILITY_COMPONENTS_MISSING');
    else
      upset_normal := (upset->>'normal_regime_probability')::numeric;
      upset_unconditional := (upset->>'unconditional_probability')::numeric;
      if not (upset_normal > 0 and upset_normal < 1 and upset_unconditional > 0 and upset_unconditional < 1) then
        reasons := array_append(reasons, 'UNDERDOG_UPSET_PROBABILITY_DOMAIN_INVALID');
      elsif abs(upset_unconditional - upset_normal) <= 0.000000001 then
        reasons := array_append(reasons, 'UNDERDOG_UPSET_PATH_DOES_NOT_CHANGE_UNCONDITIONAL_PROBABILITY');
      end if;
    end if;
  end if;

  return jsonb_build_object(
    'status', case when cardinality(reasons)=0 then 'PASS' else 'FAIL' end,
    'schema_version', 'WOW_V17_TEAM_EVENT_FAILURE_PATH_V1',
    'blockers', to_jsonb(reasons),
    'can_execute', false
  );
end;
$function$;

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
  s public.wow_mlb_forward_score_snapshots%rowtype;
  e public.wow_mlb_forward_shadow_events%rowtype;
  r public.wow_event_predictions%rowtype;
  v_event_prediction_id uuid;
  v_latest_material_update timestamptz;
  v_capability text := 'UNAVAILABLE';
  v_premodel jsonb;
  v_failure jsonb;
  v_postmodel jsonb;
  v_final jsonb;
  v_terminal jsonb;
  v_failure_blockers text[] := '{}';
  v_bridge_blockers text[] := '{}';
  v_probability_audit text;
  v_event_decision text;
  v_event_mutex text;
  v_postmodel_status text := 'HOLD';
  v_final_status text := 'HOLD';
  v_status text := 'HOLD';
  v_terminal_label text := 'MODEL_QUALIFIED_HOLD';
  v_publishable boolean := false;
  v_rank_eligible boolean := false;
  v_refresh_id uuid := gen_random_uuid();
begin
  if nullif(btrim(coalesce(p_research_run_id,'')), '') is null
     or nullif(btrim(coalesce(p_event_key,'')), '') is null
     or nullif(btrim(coalesce(p_requested_timezone,'')), '') is null then
    return jsonb_build_object(
      'status','HOLD',
      'blockers',jsonb_build_array('V17_EVENT_REQUEST_IDENTITY_INCOMPLETE'),
      'probability_publishable',false,
      'rank_eligible',false,
      'global_terminal_reducer','V17_TERMINAL_REDUCER',
      'can_execute',false
    );
  end if;

  if upper(coalesce(p_candidate_family,'')) not in (
    'TEAM_EVENT','OUTRIGHT_WINNER','MONEYLINE','FAVORITE','UNDERDOG','UPSET','MATCH_WINNER','FIGHT_WINNER'
  ) then
    return jsonb_build_object(
      'status','HOLD',
      'blockers',jsonb_build_array('V17_TEAM_EVENT_CANDIDATE_FAMILY_INVALID'),
      'probability_publishable',false,
      'rank_eligible',false,
      'global_terminal_reducer','V17_TERMINAL_REDUCER',
      'can_execute',false
    );
  end if;

  if upper(coalesce(p_decision_intent,'')) not in ('WINNER','FAVORITE','UNDERDOG','UPSET','BEST_SIDE') then
    return jsonb_build_object(
      'status','HOLD',
      'blockers',jsonb_build_array('V17_TEAM_EVENT_DECISION_INTENT_INVALID'),
      'probability_publishable',false,
      'rank_eligible',false,
      'global_terminal_reducer','V17_TERMINAL_REDUCER',
      'can_execute',false
    );
  end if;

  select * into s
  from public.wow_mlb_forward_score_snapshots
  where score_snapshot_id=p_score_snapshot_id;
  if not found then
    return jsonb_build_object(
      'status','HOLD',
      'blockers',jsonb_build_array('MLB_SCORE_SNAPSHOT_NOT_FOUND'),
      'probability_publishable',false,
      'rank_eligible',false,
      'global_terminal_reducer','V17_TERMINAL_REDUCER',
      'can_execute',false
    );
  end if;

  select * into e
  from public.wow_mlb_forward_shadow_events
  where shadow_event_id=s.shadow_event_id;
  if not found then
    return jsonb_build_object(
      'status','HOLD',
      'blockers',jsonb_build_array('MLB_SHADOW_EVENT_NOT_FOUND'),
      'probability_publishable',false,
      'rank_eligible',false,
      'global_terminal_reducer','V17_TERMINAL_REDUCER',
      'can_execute',false
    );
  end if;

  v_latest_material_update := greatest(
    e.snapshot_timestamp,
    coalesce(e.lineup_confirmed_at,e.snapshot_timestamp)
  );

  select case when capability_status='AVAILABLE' then 'AVAILABLE' else 'UNAVAILABLE' end
    into v_capability
  from public.wow_runtime_capabilities
  where capability_key='MLB_EVENT_PROBABILITY'
  order by updated_at desc
  limit 1;
  v_capability := coalesce(v_capability,'UNAVAILABLE');

  insert into public.wow_event_predictions (
    research_run_id,event_key,official_event_id,requested_slate_date,requested_timezone,
    scan_stage,event_start_time,sport,league,market_family,settlement_basis,
    home_team,away_team,venue,home_starting_pitcher,away_starting_pitcher,
    home_starter_status,away_starter_status,home_lineup_status,away_lineup_status,
    source_snapshot_id,source_snapshot_timestamp,source_coverage_status,
    latest_material_update_timestamp,model_timestamp,model_valid_after_latest_update,
    model_version,model_artifact_id,projected_runs_home,projected_runs_away,tie_after_9_probability,
    raw_home_probability,raw_away_probability,
    calibrated_home_probability,calibrated_home_lower_bound,calibrated_home_upper_bound,
    calibrated_away_probability,calibrated_away_lower_bound,calibrated_away_upper_bound,
    calibration_method,calibration_version,scoring_snapshot_id,
    event_status,event_status_timestamp,critical_status_timestamp,
    event_status_source,starter_status_source,lineup_status_source,
    governed_probability_capability,blockers,probability_publishable,rank_eligible,can_execute
  ) values (
    p_research_run_id,p_event_key,e.official_event_id,e.official_date,p_requested_timezone,
    'PREGAME',e.event_start_time,'MLB','MLB','OUTRIGHT_WINNER','FULL_GAME_INCLUDING_EXTRA_INNINGS',
    e.home_team,e.away_team,e.venue_name,e.home_probable_pitcher,e.away_probable_pitcher,
    case when e.home_probable_pitcher is null then 'UNRESOLVED' else 'PROBABLE' end,
    case when e.away_probable_pitcher is null then 'UNRESOLVED' else 'PROBABLE' end,
    case when upper(coalesce(e.lineup_status,''))='CONFIRMED' then 'CONFIRMED' else 'PROJECTED' end,
    case when upper(coalesce(e.lineup_status,''))='CONFIRMED' then 'CONFIRMED' else 'PROJECTED' end,
    e.snapshot_id,e.snapshot_timestamp,'PARTIAL',
    v_latest_material_update,s.model_timestamp,(s.model_timestamp>=v_latest_material_update),
    s.model_version,s.spec_id::text,s.home_mu,s.away_mu,s.tie_after_9_probability,
    s.raw_home_probability,s.raw_away_probability,
    s.calibrated_home_probability,s.home_lower_bound,s.home_upper_bound,
    s.calibrated_away_probability,s.away_lower_bound,s.away_upper_bound,
    s.calibration_method,s.calibration_id::text,s.score_snapshot_id,
    case when upper(coalesce(e.event_status,''))='SCHEDULED' then 'SCHEDULED' else upper(coalesce(e.event_status,'SCHEDULED')) end,
    e.snapshot_timestamp,coalesce(e.lineup_confirmed_at,e.snapshot_timestamp),
    'MLB_FORWARD_SHADOW_EVENT','MLB_FORWARD_SHADOW_EVENT','MLB_FORWARD_SHADOW_EVENT',
    v_capability,coalesce(s.blockers,'{}'::text[]),false,false,false
  )
  on conflict (research_run_id,event_key,settlement_basis)
  do update set
    official_event_id=excluded.official_event_id,
    requested_slate_date=excluded.requested_slate_date,
    requested_timezone=excluded.requested_timezone,
    event_start_time=excluded.event_start_time,
    home_team=excluded.home_team,
    away_team=excluded.away_team,
    venue=excluded.venue,
    home_starting_pitcher=excluded.home_starting_pitcher,
    away_starting_pitcher=excluded.away_starting_pitcher,
    home_starter_status=excluded.home_starter_status,
    away_starter_status=excluded.away_starter_status,
    home_lineup_status=excluded.home_lineup_status,
    away_lineup_status=excluded.away_lineup_status,
    source_snapshot_id=excluded.source_snapshot_id,
    source_snapshot_timestamp=excluded.source_snapshot_timestamp,
    latest_material_update_timestamp=excluded.latest_material_update_timestamp,
    model_timestamp=excluded.model_timestamp,
    model_valid_after_latest_update=excluded.model_valid_after_latest_update,
    model_version=excluded.model_version,
    model_artifact_id=excluded.model_artifact_id,
    projected_runs_home=excluded.projected_runs_home,
    projected_runs_away=excluded.projected_runs_away,
    tie_after_9_probability=excluded.tie_after_9_probability,
    raw_home_probability=excluded.raw_home_probability,
    raw_away_probability=excluded.raw_away_probability,
    calibrated_home_probability=excluded.calibrated_home_probability,
    calibrated_home_lower_bound=excluded.calibrated_home_lower_bound,
    calibrated_home_upper_bound=excluded.calibrated_home_upper_bound,
    calibrated_away_probability=excluded.calibrated_away_probability,
    calibrated_away_lower_bound=excluded.calibrated_away_lower_bound,
    calibrated_away_upper_bound=excluded.calibrated_away_upper_bound,
    calibration_method=excluded.calibration_method,
    calibration_version=excluded.calibration_version,
    scoring_snapshot_id=excluded.scoring_snapshot_id,
    event_status=excluded.event_status,
    event_status_timestamp=excluded.event_status_timestamp,
    critical_status_timestamp=excluded.critical_status_timestamp,
    governed_probability_capability=excluded.governed_probability_capability,
    blockers=excluded.blockers,
    probability_publishable=false,
    rank_eligible=false,
    can_execute=false
  returning event_prediction_id into v_event_prediction_id;

  v_premodel := public.wow_run_event_premodel_gates(v_event_prediction_id,now(),600,2,0.01);
  v_failure := public.wow_v17_team_failure_path_gate(v_event_prediction_id);

  if v_failure->>'status' <> 'PASS' then
    select coalesce(array_agg(value),'{}'::text[])
      into v_failure_blockers
    from jsonb_array_elements_text(coalesce(v_failure->'blockers','[]'::jsonb));

    update public.wow_event_predictions
    set blockers=(
          select coalesce(array_agg(distinct x),'{}'::text[])
          from unnest(coalesce(blockers,'{}'::text[]) || v_failure_blockers) as x
        ),
        rank_eligible=false,
        probability_publishable=false
    where event_prediction_id=v_event_prediction_id;
  end if;

  v_postmodel := public.wow_run_event_postmodel_gates(v_event_prediction_id,0.04);
  v_final := public.wow_run_event_final_gates(v_event_prediction_id,v_refresh_id,now(),600);
  v_terminal := public.wow_reduce_event_terminal_label(v_event_prediction_id,now());

  select * into r
  from public.wow_event_predictions
  where event_prediction_id=v_event_prediction_id;

  v_probability_audit := r.probability_audit_result;
  v_event_decision := r.event_decision;
  v_event_mutex := r.event_mutex_status;
  v_terminal_label := coalesce(r.terminal_label,'MODEL_QUALIFIED_HOLD');
  v_publishable := coalesce(r.probability_publishable,false);
  v_rank_eligible := coalesce(r.rank_eligible,false);

  if v_failure->>'status'='PASS'
     and v_probability_audit='PASS_PROBABILITY_AUDIT'
     and r.calibration_health_status='PASS'
     and r.governed_probability_capability='AVAILABLE'
     and v_event_mutex='PASS'
     and v_rank_eligible=true then
    v_postmodel_status := 'PASS';
  end if;

  if v_final#>>'{final_refresh,final_refresh_status}'='PASS'
     and v_final#>>'{publication,probability_publishable}'='true'
     and v_terminal_label='FINAL_APPROVED'
     and v_publishable=true then
    v_final_status := 'PASS';
  end if;

  if v_postmodel_status='PASS' and v_final_status='PASS' then
    v_status := 'PASS';
  end if;

  select coalesce(array_agg(distinct x),'{}'::text[])
    into v_bridge_blockers
  from unnest(
    coalesce(r.blockers,'{}'::text[])
    || coalesce(r.rank_eligibility_reasons,'{}'::text[])
    || coalesce(r.final_refresh_reasons,'{}'::text[])
    || coalesce(v_failure_blockers,'{}'::text[])
  ) as x;

  return jsonb_build_object(
    'status',v_status,
    'event_prediction_id',v_event_prediction_id,
    'score_snapshot_id',p_score_snapshot_id,
    'probability_audit_result',coalesce(v_probability_audit,'NOT_PROVEN'),
    'event_decision',coalesce(v_event_decision,'NOT_PROVEN'),
    'event_mutex_status',coalesce(v_event_mutex,'NOT_PROVEN'),
    'team_failure_path_status',coalesce(v_failure->>'status','FAIL'),
    'team_failure_path_gate',v_failure,
    'premodel_gates',v_premodel,
    'postmodel_gates',v_postmodel,
    'postmodel_gates_status',v_postmodel_status,
    'final_gates',v_final,
    'final_gates_status',v_final_status,
    'terminal_label',v_terminal_label,
    'terminal_ceiling',coalesce(r.terminal_ceiling,v_terminal_label),
    'rank_eligible',v_rank_eligible,
    'probability_publishable',v_publishable,
    'blockers',to_jsonb(v_bridge_blockers),
    'global_terminal_reducer','V17_TERMINAL_REDUCER',
    'can_execute',false
  );
end;
$function$;
