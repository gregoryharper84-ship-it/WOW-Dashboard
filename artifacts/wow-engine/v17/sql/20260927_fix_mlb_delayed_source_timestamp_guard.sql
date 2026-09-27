-- V17 MLB delayed-start feature-source timestamp repair.
-- Class B hydration reliability only. The fitted 38-feature vector, frozen model,
-- calibration, publication authority, terminal authority, and can_execute=false
-- semantics remain unchanged.

create or replace function public.wow_mlb_forward_build_side_features(
  p_shadow_event_id uuid,
  p_side text
)
returns jsonb
language plpgsql
set search_path to ''
as $function$
declare
  e public.wow_mlb_forward_shadow_events%rowtype;
  v_side text := upper(trim(p_side));
  v_scoring_id integer;
  v_opp_id integer;
  v_starter_id integer;
  v_is_home double precision;
  v_sc_body jsonb;
  v_opp_body jsonb;
  v_bp_body jsonb;
  v_starter_body jsonb;
  v_sc_hit jsonb;
  v_sc_pitch jsonb;
  v_opp_pitch jsonb;
  v_opp_field jsonb;
  v_bp jsonb;
  v_st jsonb;
  v_ctx jsonb;
  v_work public.wow_mlb_forward_bullpen_workload%rowtype;
  v_sc_games double precision;
  v_opp_games double precision;
  v_features text[] := array['is_home','off_runs_pg','off_hits_pg','off_hr_pg','off_bb_pg','off_so_pg','off_tb_pg','off_run_diff_pg','off_win_rate','off_sb_pg','off_cs_pg','off_days_rest','opp_runs_allowed_pg','opp_errors_pg','opp_win_rate','opp_bp_era','opp_bp_k_rate','opp_bp_bb_rate','opp_bp_hr_rate','opp_bp_pitches_3d','opp_bp_outs_3d','opp_bp_apps_3d','opp_starter_prior_starts','opp_starter_era','opp_starter_k_rate','opp_starter_bb_rate','opp_starter_h_rate','opp_starter_hr_rate','opp_starter_outs_per_start','opp_starter_tbf_per_start','opp_starter_pitches_per_start','opp_starter_strike_rate','opp_starter_days_rest','opp_starter_pitches_last3','park_total_runs_prior','park_prior_games','opp_days_rest','min_team_prior_games'];
  v_vec double precision[];
  v_aux_ids uuid[];
  v_urls text[];
  v_latest_capture timestamptz;
  v_fs uuid;
begin
  select * into e
  from public.wow_mlb_forward_shadow_events
  where shadow_event_id=p_shadow_event_id;
  if not found then
    raise exception 'shadow event not found';
  end if;

  -- Authoritative event state, not nominal clock time alone, owns the pregame
  -- boundary. The same bounded allowlist is used by the outer hydrator.
  if e.event_status not in ('Scheduled','Pre-Game','Warmup','Delayed Start') then
    update public.wow_mlb_forward_shadow_events
    set feature_hydration_status='BLOCKED_EVENT_NOT_PREGAME'
    where shadow_event_id=p_shadow_event_id;
    return jsonb_build_object(
      'status','BLOCKED',
      'reason','EVENT_NOT_PREGAME',
      'event_status',e.event_status,
      'side',v_side,
      'can_execute',false
    );
  end if;

  if e.event_start_time <= clock_timestamp() - interval '6 hours' then
    update public.wow_mlb_forward_shadow_events
    set feature_hydration_status='BLOCKED_PREGAME_WINDOW_EXPIRED'
    where shadow_event_id=p_shadow_event_id;
    return jsonb_build_object(
      'status','BLOCKED',
      'reason','PREGAME_WINDOW_EXPIRED',
      'event_status',e.event_status,
      'event_start_time',e.event_start_time,
      'side',v_side,
      'can_execute',false
    );
  end if;

  if v_side='HOME' then
    v_scoring_id:=e.home_team_id;
    v_opp_id:=e.away_team_id;
    v_starter_id:=e.away_probable_pitcher_id;
    v_is_home:=1;
  elsif v_side='AWAY' then
    v_scoring_id:=e.away_team_id;
    v_opp_id:=e.home_team_id;
    v_starter_id:=e.home_probable_pitcher_id;
    v_is_home:=0;
  else
    raise exception 'side must be HOME or AWAY';
  end if;

  if v_starter_id is null then
    update public.wow_mlb_forward_shadow_events
    set feature_hydration_status='DELAYED_STARTER_UNRESOLVED'
    where shadow_event_id=p_shadow_event_id;
    return jsonb_build_object(
      'status','DELAYED',
      'reason','PROBABLE_STARTER_UNRESOLVED',
      'side',v_side,
      'can_execute',false
    );
  end if;

  v_sc_body:=public.wow_mlb_forward_get_aux_json(e.snapshot_id,'TEAM_SEASON',v_scoring_id::text);
  v_opp_body:=public.wow_mlb_forward_get_aux_json(e.snapshot_id,'TEAM_SEASON',v_opp_id::text);
  v_bp_body:=public.wow_mlb_forward_get_aux_json(e.snapshot_id,'TEAM_RELIEF_SEASON',v_opp_id::text);
  v_starter_body:=public.wow_mlb_forward_get_aux_json(e.snapshot_id,'STARTER_SEASON_GAMELOG',v_starter_id::text);
  if v_sc_body is null or v_opp_body is null or v_bp_body is null or v_starter_body is null then
    update public.wow_mlb_forward_shadow_events
    set feature_hydration_status='BLOCKED_SOURCE_MISSING'
    where shadow_event_id=p_shadow_event_id;
    return jsonb_build_object(
      'status','BLOCKED',
      'reason','REQUIRED_FORWARD_SOURCE_MISSING',
      'side',v_side,
      'can_execute',false
    );
  end if;

  select max(captured_at) into v_latest_capture
  from public.wow_mlb_forward_aux_snapshots
  where shadow_snapshot_id=e.snapshot_id and (
    (source_kind='TEAM_SEASON' and subject_id in (v_scoring_id::text,v_opp_id::text)) or
    (source_kind='TEAM_RELIEF_SEASON' and subject_id=v_opp_id::text) or
    (source_kind='STARTER_SEASON_GAMELOG' and subject_id=v_starter_id::text) or
    (source_kind='MLB_SCHEDULE_SEASON_TO_DATE' and subject_id='2026')
  );

  if v_latest_capture is null then
    update public.wow_mlb_forward_shadow_events
    set feature_hydration_status='BLOCKED_SOURCE_TIMESTAMP'
    where shadow_event_id=p_shadow_event_id;
    return jsonb_build_object(
      'status','BLOCKED',
      'reason','SOURCE_TIMESTAMP_MISSING',
      'side',v_side,
      'can_execute',false
    );
  end if;

  if v_latest_capture > clock_timestamp() then
    update public.wow_mlb_forward_shadow_events
    set feature_hydration_status='BLOCKED_SOURCE_TIMESTAMP'
    where shadow_event_id=p_shadow_event_id;
    return jsonb_build_object(
      'status','BLOCKED',
      'reason','SOURCE_TIMESTAMP_IN_FUTURE',
      'side',v_side,
      'latest_capture',v_latest_capture,
      'event_start_time',e.event_start_time,
      'can_execute',false
    );
  end if;

  -- For a normal future game, this preserves the old strict pre-start rule.
  -- Once nominal first pitch has passed, post-nominal source captures are valid
  -- only because the canonical row is still in an authoritative pregame state
  -- and remains inside the six-hour bounded lateness window checked above.
  if e.event_start_time > clock_timestamp() and v_latest_capture >= e.event_start_time then
    update public.wow_mlb_forward_shadow_events
    set feature_hydration_status='BLOCKED_SOURCE_TIMESTAMP'
    where shadow_event_id=p_shadow_event_id;
    return jsonb_build_object(
      'status','BLOCKED',
      'reason','SOURCE_TIMESTAMP_NOT_PREGAME',
      'side',v_side,
      'latest_capture',v_latest_capture,
      'event_start_time',e.event_start_time,
      'can_execute',false
    );
  end if;

  select * into v_work
  from public.wow_mlb_forward_bullpen_workload
  where shadow_snapshot_id=e.snapshot_id and team_id=v_opp_id;
  if not found or v_work.hydration_status<>'PASS' then
    update public.wow_mlb_forward_shadow_events
    set feature_hydration_status='BLOCKED_BULLPEN_WORKLOAD'
    where shadow_event_id=p_shadow_event_id;
    return jsonb_build_object(
      'status','BLOCKED',
      'reason','BULLPEN_WORKLOAD_UNAVAILABLE',
      'side',v_side,
      'can_execute',false
    );
  end if;

  v_sc_hit:=public.wow_mlb_team_stat_group(v_sc_body,'hitting');
  v_sc_pitch:=public.wow_mlb_team_stat_group(v_sc_body,'pitching');
  v_opp_pitch:=public.wow_mlb_team_stat_group(v_opp_body,'pitching');
  v_opp_field:=public.wow_mlb_team_stat_group(v_opp_body,'fielding');
  v_bp:=public.wow_mlb_first_stat_split(v_bp_body);
  v_st:=public.wow_mlb_starter_prior_features(v_starter_body,e.official_date);
  v_ctx:=public.wow_mlb_forward_schedule_context(e.snapshot_id,e.official_date,v_scoring_id,v_opp_id,e.venue_id);
  v_sc_games:=coalesce((v_ctx->>'scoring_prior_games')::double precision,0);
  v_opp_games:=coalesce((v_ctx->>'opponent_prior_games')::double precision,0);
  if v_sc_games<=0 or v_opp_games<=0 or v_sc_hit is null or v_sc_pitch is null or v_opp_pitch is null or v_opp_field is null or v_bp is null or v_st is null then
    update public.wow_mlb_forward_shadow_events
    set feature_hydration_status='BLOCKED_FEATURE_COMPONENT'
    where shadow_event_id=p_shadow_event_id;
    return jsonb_build_object(
      'status','BLOCKED',
      'reason','FEATURE_COMPONENT_UNAVAILABLE',
      'side',v_side,
      'can_execute',false
    );
  end if;

  v_vec:=array[
    v_is_home,
    coalesce((v_sc_hit->>'runs')::double precision,0)/v_sc_games,
    coalesce((v_sc_hit->>'hits')::double precision,0)/v_sc_games,
    coalesce((v_sc_hit->>'homeRuns')::double precision,0)/v_sc_games,
    coalesce((v_sc_hit->>'baseOnBalls')::double precision,0)/v_sc_games,
    coalesce((v_sc_hit->>'strikeOuts')::double precision,0)/v_sc_games,
    coalesce((v_sc_hit->>'totalBases')::double precision,0)/v_sc_games,
    (coalesce((v_sc_hit->>'runs')::double precision,0)-coalesce((v_sc_pitch->>'runs')::double precision,0))/v_sc_games,
    coalesce((v_ctx->>'scoring_win_rate')::double precision,0),
    coalesce((v_sc_hit->>'stolenBases')::double precision,0)/v_sc_games,
    coalesce((v_sc_hit->>'caughtStealing')::double precision,0)/v_sc_games,
    coalesce((v_ctx->>'scoring_days_rest')::double precision,0),
    coalesce((v_opp_pitch->>'runs')::double precision,0)/v_opp_games,
    coalesce((v_opp_field->>'errors')::double precision,0)/v_opp_games,
    coalesce((v_ctx->>'opponent_win_rate')::double precision,0),
    case when coalesce((v_bp->>'outs')::double precision,0)>0 then 27.0*coalesce((v_bp->>'earnedRuns')::double precision,0)/((v_bp->>'outs')::double precision) else 0 end,
    case when coalesce((v_bp->>'battersFaced')::double precision,0)>0 then coalesce((v_bp->>'strikeOuts')::double precision,0)/((v_bp->>'battersFaced')::double precision) else 0 end,
    case when coalesce((v_bp->>'battersFaced')::double precision,0)>0 then coalesce((v_bp->>'baseOnBalls')::double precision,0)/((v_bp->>'battersFaced')::double precision) else 0 end,
    case when coalesce((v_bp->>'battersFaced')::double precision,0)>0 then coalesce((v_bp->>'homeRuns')::double precision,0)/((v_bp->>'battersFaced')::double precision) else 0 end,
    v_work.relief_pitches::double precision,
    v_work.relief_outs::double precision,
    v_work.relief_appearances::double precision,
    coalesce((v_st->>'prior_starts')::double precision,0),
    coalesce((v_st->>'era')::double precision,0),
    coalesce((v_st->>'k_rate')::double precision,0),
    coalesce((v_st->>'bb_rate')::double precision,0),
    coalesce((v_st->>'h_rate')::double precision,0),
    coalesce((v_st->>'hr_rate')::double precision,0),
    coalesce((v_st->>'outs_per_start')::double precision,0),
    coalesce((v_st->>'tbf_per_start')::double precision,0),
    coalesce((v_st->>'pitches_per_start')::double precision,0),
    coalesce((v_st->>'strike_rate')::double precision,0),
    coalesce((v_st->>'days_rest')::double precision,0),
    coalesce((v_st->>'pitches_last3')::double precision,0),
    coalesce((v_ctx->>'park_total_runs_prior')::double precision,0),
    coalesce((v_ctx->>'park_prior_games')::double precision,0),
    coalesce((v_ctx->>'opponent_days_rest')::double precision,0),
    coalesce((v_ctx->>'min_team_prior_games')::double precision,0)
  ];

  select array_agg(aux_snapshot_id order by source_kind,subject_id),
         array_agg(source_url order by source_kind,subject_id)
  into v_aux_ids,v_urls
  from (
    select distinct on (source_kind,subject_id)
           aux_snapshot_id,source_kind,subject_id,source_url
    from public.wow_mlb_forward_aux_snapshots
    where shadow_snapshot_id=e.snapshot_id and (
      (source_kind='TEAM_SEASON' and subject_id in (v_scoring_id::text,v_opp_id::text)) or
      (source_kind='TEAM_RELIEF_SEASON' and subject_id=v_opp_id::text) or
      (source_kind='STARTER_SEASON_GAMELOG' and subject_id=v_starter_id::text) or
      (source_kind='MLB_SCHEDULE_SEASON_TO_DATE' and subject_id='2026')
    )
    order by source_kind,subject_id,captured_at desc
  ) q;

  insert into public.wow_mlb_forward_feature_snapshots(
    shadow_event_id,side,scoring_team_id,opponent_team_id,probable_starter_id,
    feature_names,feature_vector,source_snapshot_ids,source_urls,hydration_status
  )
  values(
    p_shadow_event_id,v_side,v_scoring_id,v_opp_id,v_starter_id,
    v_features,v_vec,coalesce(v_aux_ids,'{}'),coalesce(v_urls,'{}'),'PASS'
  )
  on conflict(shadow_event_id,side) do update
  set created_at=now(),
      scoring_team_id=excluded.scoring_team_id,
      opponent_team_id=excluded.opponent_team_id,
      probable_starter_id=excluded.probable_starter_id,
      feature_names=excluded.feature_names,
      feature_vector=excluded.feature_vector,
      source_snapshot_ids=excluded.source_snapshot_ids,
      source_urls=excluded.source_urls,
      hydration_status='PASS',
      blockers='{}'
  returning feature_snapshot_id into v_fs;

  if (
    select count(*)
    from public.wow_mlb_forward_feature_snapshots
    where shadow_event_id=p_shadow_event_id and hydration_status='PASS'
  )=2 then
    update public.wow_mlb_forward_shadow_events
    set feature_hydration_status='PASS'
    where shadow_event_id=p_shadow_event_id;
  end if;

  return jsonb_build_object(
    'status','PASS',
    'feature_snapshot_id',v_fs,
    'shadow_event_id',p_shadow_event_id,
    'side',v_side,
    'feature_count',cardinality(v_vec),
    'starter_prior_starts',(v_st->>'prior_starts')::int,
    'latest_source_capture',v_latest_capture,
    'can_execute',false
  );
end;
$function$;
