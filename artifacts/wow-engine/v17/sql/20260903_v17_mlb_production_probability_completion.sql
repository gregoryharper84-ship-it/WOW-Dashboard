-- WOW V17 MLB production probability completion.
--
-- Purpose:
--   * preserve strict market requirements for FAVORITE/UNDERDOG/UPSET intents;
--   * allow WINNER/BEST_SIDE sporting-probability publication without fabricating
--     sportsbook evidence, consistent with V17's probability/market separation;
--   * hydrate current official event evidence and fitted-model provenance;
--   * derive quantitative favorite-loss / underdog-upset paths directly from the
--     fitted full-game NB score distribution (regulation one-run, regulation
--     multi-run, and extra-inning loss paths);
--   * adapt the event calibration gate to the certified MLB V2D calibration ledger.
--
-- This migration never authorizes wager execution. can_execute remains false.

alter table public.wow_event_predictions
  add column if not exists decision_intent text;

alter table public.wow_event_predictions
  drop constraint if exists chk_event_decision_intent;
alter table public.wow_event_predictions
  add constraint chk_event_decision_intent check (
    decision_intent is null or decision_intent in ('WINNER','BEST_SIDE','FAVORITE','UNDERDOG','UPSET')
  );

-- Existing hard constraints encoded sportsbook-market requirements for every
-- rankable probability. V17 separates probability-only winner ranking from
-- downstream market-role/value work. Preserve the legacy requirements whenever
-- the intent is market-relative.
alter table public.wow_event_predictions
  drop constraint if exists chk_event_rank_eligible_requires_hard_gates;
alter table public.wow_event_predictions
  add constraint chk_event_rank_eligible_requires_hard_gates check (
    rank_eligible = false or (
      model_ready = true
      and model_readiness_status = 'PASS'
      and scoring_snapshot_id is not null
      and probability_audit_result = 'PASS_PROBABILITY_AUDIT'
      and market_independence_status = 'PASS'
      and source_coverage_status = 'COMPLETE'
      and source_snapshot_timestamp is not null
      and independent_model_weight is not null
      and uncertainty_components is not null
      and raw_home_probability is not null
      and raw_away_probability is not null
      and independent_home_probability is not null
      and independent_away_probability is not null
      and calibrated_home_probability is not null
      and calibrated_away_probability is not null
      and calibrated_home_lower_bound is not null
      and calibrated_away_lower_bound is not null
      and calibrated_home_upper_bound is not null
      and calibrated_away_upper_bound is not null
      and calibration_health_status = 'PASS'
      and governed_probability_capability = 'AVAILABLE'
      and selected_participant is not null
      and model_timestamp is not null
      and model_valid_after_latest_update = true
      and probability_invalidated = false
      and rerun_required = false
      and rank_eligibility_status = 'PASS'
      and cardinality(rank_eligibility_reasons) = 0
      and (
        decision_intent in ('WINNER','BEST_SIDE')
        or (
          market_prior_available = true
          and market_prior_home_probability is not null
          and market_prior_away_probability is not null
          and market_timestamp is not null
          and market_role_status = 'LOCKED'
          and market_role_consensus_status = 'PASS'
          and coalesce(market_role_consensus_book_count,0) >= 2
          and selected_market_role in ('FAVORITE','UNDERDOG')
        )
      )
    )
  );

alter table public.wow_event_predictions
  drop constraint if exists chk_event_publishable_requires_final_refresh;
alter table public.wow_event_predictions
  add constraint chk_event_publishable_requires_final_refresh check (
    probability_publishable = false or (
      final_refresh_status = 'PASS'
      and final_refresh_snapshot_id is not null
      and final_refresh_timestamp is not null
      and event_status_fresh_at_refresh = true
      and critical_status_fresh_at_refresh = true
      and settlement_fresh_at_refresh = true
      and probability_invalidated = false
      and rerun_required = false
      and cardinality(final_refresh_reasons) = 0
      and (
        decision_intent in ('WINNER','BEST_SIDE')
        or market_fresh_at_refresh = true
      )
    )
  );

create or replace function public.wow_mlb_v17_failure_path_package(
  p_score_snapshot_id uuid
)
returns jsonb
language plpgsql
stable
set search_path to ''
as $function$
declare
  s public.wow_mlb_forward_score_snapshots%rowtype;
  d public.wow_mlb_v2b_distribution_state%rowtype;
  mults double precision[];
  weights double precision[];
  ph double precision[];
  pa double precision[];
  ri integer;
  hi integer;
  ai integer;
  hm double precision;
  am double precision;
  mass double precision;
  reg_one double precision;
  reg_multi double precision;
  reg_tie double precision;
  weighted_one double precision := 0;
  weighted_multi double precision := 0;
  weighted_tie double precision := 0;
  favorite_side text;
  favorite_probability double precision;
  underdog_probability double precision;
  extra_loss double precision;
  total_loss double precision;
  largest_path text;
  largest_probability double precision;
  favorite_json jsonb;
  upset_json jsonb;
begin
  select * into s
  from public.wow_mlb_forward_score_snapshots
  where score_snapshot_id = p_score_snapshot_id;
  if not found then
    return jsonb_build_object('status','BLOCKED','code','MLB_SCORE_SNAPSHOT_NOT_FOUND','can_execute',false);
  end if;

  select * into d
  from public.wow_mlb_v2b_distribution_state
  where distribution_id = s.distribution_id;
  if not found then
    return jsonb_build_object('status','BLOCKED','code','MLB_DISTRIBUTION_STATE_NOT_FOUND','can_execute',false);
  end if;

  favorite_side := case
    when s.calibrated_home_probability >= s.calibrated_away_probability then 'HOME'
    else 'AWAY'
  end;
  favorite_probability := case when favorite_side='HOME' then s.calibrated_home_probability else s.calibrated_away_probability end;
  underdog_probability := 1.0 - favorite_probability;

  mults := array[d.low_multiplier,d.center_multiplier,d.high_multiplier];
  weights := array[d.low_weight,d.center_weight,d.high_weight];

  for ri in 1..3 loop
    hm := s.home_mu * mults[ri];
    am := s.away_mu * mults[ri];
    ph := public.wow_nb_pmf_array(hm,d.home_alpha_independent,40);
    pa := public.wow_nb_pmf_array(am,d.away_alpha_independent,40);
    reg_one := 0;
    reg_multi := 0;
    reg_tie := 0;
    mass := 0;

    for hi in 0..40 loop
      for ai in 0..40 loop
        mass := mass + ph[hi+1]*pa[ai+1];
        if hi=ai then
          reg_tie := reg_tie + ph[hi+1]*pa[ai+1];
        elsif favorite_side='HOME' and ai>hi then
          if ai-hi=1 then reg_one := reg_one + ph[hi+1]*pa[ai+1];
          else reg_multi := reg_multi + ph[hi+1]*pa[ai+1]; end if;
        elsif favorite_side='AWAY' and hi>ai then
          if hi-ai=1 then reg_one := reg_one + ph[hi+1]*pa[ai+1];
          else reg_multi := reg_multi + ph[hi+1]*pa[ai+1]; end if;
        end if;
      end loop;
    end loop;

    if mass <= 0 then
      return jsonb_build_object('status','BLOCKED','code','MLB_FAILURE_PATH_PMF_MASS_INVALID','can_execute',false);
    end if;
    reg_one := reg_one/mass;
    reg_multi := reg_multi/mass;
    reg_tie := reg_tie/mass;
    weighted_one := weighted_one + weights[ri]*reg_one;
    weighted_multi := weighted_multi + weights[ri]*reg_multi;
    weighted_tie := weighted_tie + weights[ri]*reg_tie;
  end loop;

  extra_loss := weighted_tie * case
    when favorite_side='HOME' then (1.0-d.extra_inning_home_win_probability)
    else d.extra_inning_home_win_probability
  end;
  total_loss := weighted_one + weighted_multi + extra_loss;

  if not (total_loss > 0 and total_loss < 1) then
    return jsonb_build_object('status','BLOCKED','code','MLB_FAILURE_PATH_PROBABILITY_DOMAIN_INVALID','can_execute',false);
  end if;

  largest_path := 'REGULATION_ONE_RUN_LOSS';
  largest_probability := weighted_one;
  if weighted_multi > largest_probability then
    largest_path := 'REGULATION_MULTI_RUN_LOSS';
    largest_probability := weighted_multi;
  end if;
  if extra_loss > largest_probability then
    largest_path := 'EXTRA_INNING_LOSS';
    largest_probability := extra_loss;
  end if;

  favorite_json := jsonb_build_object(
    'schema_version','WOW_V17_TEAM_EVENT_FAILURE_PATH_V1',
    'model_version','MLB_FAILURE_REGIME_MIXTURE_V1',
    'basis','FITTED_FULL_GAME_SCORE_DISTRIBUTION',
    'favorite_basis','MODEL_CALIBRATED_LEADER',
    'favorite_side',favorite_side,
    'normal_regime_probability',weighted_one+weighted_multi,
    'unconditional_probability',total_loss,
    'calibrated_reference_loss_probability',underdog_probability,
    'regimes',jsonb_build_array(
      jsonb_build_object('name','REGULATION_ONE_RUN_LOSS','probability',weighted_one),
      jsonb_build_object('name','REGULATION_MULTI_RUN_LOSS','probability',weighted_multi),
      jsonb_build_object('name','EXTRA_INNING_LOSS','probability',extra_loss)
    ),
    'largest_path',largest_path,
    'largest_path_probability',largest_probability,
    'score_snapshot_id',p_score_snapshot_id,
    'distribution_id',s.distribution_id,
    'can_execute',false
  );

  upset_json := jsonb_build_object(
    'schema_version','WOW_V17_TEAM_EVENT_FAILURE_PATH_V1',
    'model_version','MLB_FAILURE_REGIME_MIXTURE_V1',
    'basis','FITTED_FULL_GAME_SCORE_DISTRIBUTION',
    'underdog_side',case when favorite_side='HOME' then 'AWAY' else 'HOME' end,
    'normal_regime_probability',weighted_one+weighted_multi,
    'unconditional_probability',total_loss,
    'calibrated_reference_probability',underdog_probability,
    'regimes',jsonb_build_array(
      jsonb_build_object('name','REGULATION_ONE_RUN_UPSET','probability',weighted_one),
      jsonb_build_object('name','REGULATION_MULTI_RUN_UPSET','probability',weighted_multi),
      jsonb_build_object('name','EXTRA_INNING_UPSET','probability',extra_loss)
    ),
    'score_snapshot_id',p_score_snapshot_id,
    'distribution_id',s.distribution_id,
    'can_execute',false
  );

  return jsonb_build_object(
    'status','PASS',
    'favorite_side',favorite_side,
    'favorite_failure_path_probability',total_loss,
    'largest_favorite_loss_path',largest_path,
    'favorite_failure_paths_json',favorite_json,
    'underdog_upset_path_json',upset_json,
    'can_execute',false
  );
end;
$function$;

-- Current official evidence + fitted-model handoff. Weather remains contextual
-- because the certified baseline feature vector has no numeric weather column;
-- injury is explicitly NOT_APPLICABLE as a separate numeric feature, with player
-- availability represented by current starter/lineup identity evidence.
create or replace function public.wow_v17_hydrate_mlb_event_governance_evidence(
  p_event_prediction_id uuid,
  p_score_snapshot_id uuid,
  p_evidence jsonb default '{}'::jsonb,
  p_decision_intent text default 'BEST_SIDE'
)
returns jsonb
language plpgsql
set search_path to ''
as $function$
declare
  r public.wow_event_predictions%rowtype;
  s public.wow_mlb_forward_score_snapshots%rowtype;
  se public.wow_mlb_forward_shadow_events%rowtype;
  cal public.wow_mlb_v2d_intercept_calibration%rowtype;
  health public.wow_mlb_v2d_calibration_health%rowtype;
  deployment jsonb;
  failure jsonb;
  live_resp extensions.http_response;
  live jsonb;
  fetched_at timestamptz;
  live_state text;
  live_home_starter text;
  live_away_starter text;
  home_order jsonb;
  away_order jsonb;
  home_bp public.wow_mlb_forward_bullpen_workload%rowtype;
  away_bp public.wow_mlb_forward_bullpen_workload%rowtype;
  k text;
  value_text text;
  source_name text;
  source_grade text;
  evidence_status text;
  ttl integer;
  payload jsonb;
  payload_hash text;
  evidence_id uuid;
  evidence_ts timestamptz;
  inserted integer := 0;
  scoring_n integer := 0;
  blockers text[] := '{}';
  required_kinds text[] := array[
    'OFFICIAL_EVENT_ID','EVENT_STATUS','HOME_STARTER','AWAY_STARTER',
    'HOME_LINEUP','AWAY_LINEUP','BULLPEN_STATUS','WEATHER_STATUS',
    'INJURY_STATUS','SETTLEMENT'
  ];
begin
  if upper(coalesce(p_decision_intent,'')) not in ('WINNER','BEST_SIDE','FAVORITE','UNDERDOG','UPSET') then
    return jsonb_build_object('status','HOLD','blockers',jsonb_build_array('V17_TEAM_EVENT_DECISION_INTENT_INVALID'),'can_execute',false);
  end if;

  select * into r from public.wow_event_predictions where event_prediction_id=p_event_prediction_id for update;
  if not found then
    return jsonb_build_object('status','HOLD','blockers',jsonb_build_array('EVENT_PREDICTION_NOT_FOUND'),'can_execute',false);
  end if;
  select * into s from public.wow_mlb_forward_score_snapshots where score_snapshot_id=p_score_snapshot_id;
  if not found then
    return jsonb_build_object('status','HOLD','blockers',jsonb_build_array('MLB_SCORE_SNAPSHOT_NOT_FOUND'),'can_execute',false);
  end if;
  select * into se from public.wow_mlb_forward_shadow_events where shadow_event_id=s.shadow_event_id;
  if not found then
    return jsonb_build_object('status','HOLD','blockers',jsonb_build_array('MLB_SHADOW_EVENT_NOT_FOUND'),'can_execute',false);
  end if;
  if se.official_event_id is distinct from r.official_event_id then
    return jsonb_build_object('status','HOLD','blockers',jsonb_build_array('OFFICIAL_EVENT_ID_MISMATCH'),'can_execute',false);
  end if;
  if clock_timestamp() >= r.event_start_time then
    return jsonb_build_object('status','HOLD','blockers',jsonb_build_array('EVENT_NOT_PREGAME'),'can_execute',false);
  end if;

  live_resp := extensions.http_get(format('https://statsapi.mlb.com/api/v1.1/game/%s/feed/live',r.official_event_id)::varchar);
  if live_resp.status <> 200 then
    return jsonb_build_object('status','HOLD','blockers',jsonb_build_array('OFFICIAL_MLB_LIVE_FEED_UNAVAILABLE'),'http_status',live_resp.status,'can_execute',false);
  end if;
  live := live_resp.content::jsonb;
  fetched_at := clock_timestamp();
  live_state := coalesce(live#>>'{gameData,status,detailedState}','');
  if live_state in ('In Progress','Final','Game Over','Postponed','Cancelled','Canceled') then
    return jsonb_build_object('status','HOLD','blockers',jsonb_build_array('EVENT_NOT_PREGAME'),'official_state',live_state,'can_execute',false);
  end if;
  if live#>>'{gameData,teams,home,name}' is distinct from r.home_team
     or live#>>'{gameData,teams,away,name}' is distinct from r.away_team then
    return jsonb_build_object('status','HOLD','blockers',jsonb_build_array('OFFICIAL_TEAM_IDENTITY_MISMATCH'),'can_execute',false);
  end if;

  live_home_starter := nullif(btrim(coalesce(live#>>'{gameData,probablePitchers,home,fullName}','')), '');
  live_away_starter := nullif(btrim(coalesce(live#>>'{gameData,probablePitchers,away,fullName}','')), '');
  if live_home_starter is null or live_away_starter is null then
    return jsonb_build_object('status','HOLD','blockers',jsonb_build_array('CURRENT_STARTER_IDENTITY_UNAVAILABLE'),'can_execute',false);
  end if;
  if lower(live_home_starter) is distinct from lower(r.home_starting_pitcher)
     or lower(live_away_starter) is distinct from lower(r.away_starting_pitcher) then
    return jsonb_build_object(
      'status','HOLD',
      'blockers',jsonb_build_array('STARTER_IDENTITY_CHANGED_REQUIRES_MODEL_RESCORE'),
      'current_home_starter',live_home_starter,
      'current_away_starter',live_away_starter,
      'can_execute',false
    );
  end if;

  home_order := coalesce(live#>'{liveData,boxscore,teams,home,battingOrder}','[]'::jsonb);
  away_order := coalesce(live#>'{liveData,boxscore,teams,away,battingOrder}','[]'::jsonb);
  if jsonb_array_length(home_order)<>9 or jsonb_array_length(away_order)<>9 then
    return jsonb_build_object('status','HOLD','blockers',jsonb_build_array('OFFICIAL_LINEUP_NOT_CONFIRMED'),'can_execute',false);
  end if;
  if se.lineup_confirmed_at is null or s.model_timestamp < se.lineup_confirmed_at then
    return jsonb_build_object('status','HOLD','blockers',jsonb_build_array('POST_LINEUP_SCORE_SNAPSHOT_REQUIRED'),'can_execute',false);
  end if;

  select * into home_bp
  from public.wow_mlb_forward_bullpen_workload
  where shadow_snapshot_id=se.snapshot_id and team_id=se.home_team_id and hydration_status='PASS'
  order by window_end desc limit 1;
  select * into away_bp
  from public.wow_mlb_forward_bullpen_workload
  where shadow_snapshot_id=se.snapshot_id and team_id=se.away_team_id and hydration_status='PASS'
  order by window_end desc limit 1;
  if home_bp.team_id is null or away_bp.team_id is null then
    return jsonb_build_object('status','HOLD','blockers',jsonb_build_array('BULLPEN_WORKLOAD_EVIDENCE_UNAVAILABLE'),'can_execute',false);
  end if;

  select * into cal from public.wow_mlb_v2d_intercept_calibration where calibration_id=s.calibration_id;
  if not found then
    return jsonb_build_object('status','HOLD','blockers',jsonb_build_array('MLB_CERTIFIED_CALIBRATION_NOT_FOUND'),'can_execute',false);
  end if;
  select * into health from public.wow_mlb_v2d_calibration_health where spec_id=s.spec_id order by assessed_at desc limit 1;
  if not found or health.calibration_health_status<>'PASS' then
    return jsonb_build_object('status','HOLD','blockers',jsonb_build_array('MLB_CALIBRATION_HEALTH_NOT_PASS'),'can_execute',false);
  end if;
  deployment := public.wow_governed_deployment_state();
  if coalesce(deployment->>'governed_probability_capability','UNAVAILABLE')<>'AVAILABLE'
     or not coalesce((deployment->>'probability_publishable')::boolean,false) then
    return jsonb_build_object('status','HOLD','blockers',jsonb_build_array('MLB_GOVERNED_DEPLOYMENT_NOT_PUBLISHABLE'),'can_execute',false);
  end if;

  failure := public.wow_mlb_v17_failure_path_package(p_score_snapshot_id);
  if coalesce(failure->>'status','')<>'PASS' then
    return failure || jsonb_build_object('can_execute',false);
  end if;

  -- Reconcile stale score-time blockers only after current certified state proves
  -- they are obsolete. Missing/live blockers are never removed here.
  update public.wow_event_predictions
  set decision_intent=upper(p_decision_intent),
      controlling_specialist='wow.mlb-game-win-probability-expert',
      independent_home_probability=s.raw_home_probability,
      independent_away_probability=s.raw_away_probability,
      independent_model_weight=1.0,
      market_prior_available=false,
      market_prior_home_probability=null,
      market_prior_away_probability=null,
      market_prior_weight=0.0,
      calibration_status='PASS',
      calibration_method=s.calibration_method,
      calibration_version=s.calibration_id::text,
      calibration_training_n=cal.prior_games,
      calibration_parent_cohort=format('MLB_%s_%s',cal.prior_start_year,cal.prior_end_year),
      bounds_method_version='LOCAL_DECILE_WILSON_95',
      uncertainty_components=jsonb_build_object(
        'method','LOCAL_DECILE_WILSON_95_PLUS_FITTED_SCORE_DISTRIBUTION',
        'home_bound_status',s.home_bound_status,
        'away_bound_status',s.away_bound_status,
        'tie_after_9_probability',s.tie_after_9_probability,
        'tail_mass_estimate',s.tail_mass_estimate,
        'failure_model_version','MLB_FAILURE_REGIME_MIXTURE_V1'
      ),
      confidence_level='95%',
      governed_probability_capability='AVAILABLE',
      favorite_side=failure->>'favorite_side',
      favorite_failure_path_probability=(failure->>'favorite_failure_path_probability')::numeric,
      largest_favorite_loss_path=failure->>'largest_favorite_loss_path',
      favorite_failure_paths_json=failure->'favorite_failure_paths_json',
      underdog_upset_path_json=failure->'underdog_upset_path_json',
      event_status='SCHEDULED',
      event_status_timestamp=fetched_at,
      critical_status_timestamp=fetched_at,
      event_status_source='MLB_STATS_API_LIVE_FEED',
      starter_status_source='MLB_STATS_API_LIVE_FEED',
      lineup_status_source='MLB_STATS_API_LIVE_FEED',
      home_starter_status='PROBABLE',
      away_starter_status='PROBABLE',
      home_lineup_status='CONFIRMED',
      away_lineup_status='CONFIRMED',
      settlement_source='TEAM_EVENT_REQUEST_CONTRACT',
      settlement_timestamp=fetched_at,
      model_valid_after_latest_update=true,
      probability_invalidated=false,
      rerun_required=false,
      blockers=array_remove(array_remove(coalesce(blockers,'{}'::text[]),'CALIBRATION_HEALTH_FORWARD_EVIDENCE_PENDING'),'PRODUCTION_FEATURE_READY_FALSE')
  where event_prediction_id=p_event_prediction_id;

  foreach k in array required_kinds loop
    value_text := null;
    source_name := 'MLB_STATS_API_LIVE_FEED';
    source_grade := 'OFFICIAL';
    evidence_status := 'RETRIEVED';
    ttl := 900;
    evidence_ts := fetched_at;
    payload := '{}'::jsonb;

    case k
      when 'OFFICIAL_EVENT_ID' then
        value_text := r.official_event_id;
        payload := jsonb_build_object('official_event_id',r.official_event_id,'model_role','IDENTITY_LOCK');
      when 'EVENT_STATUS' then
        value_text := 'SCHEDULED';
        payload := jsonb_build_object('official_state',live_state,'normalized_state','SCHEDULED','model_role','PREGAME_STATUS_LOCK');
      when 'HOME_STARTER' then
        value_text := live_home_starter || '|PROBABLE';
        payload := jsonb_build_object('pitcher',live_home_starter,'status','PROBABLE','model_role','NUMERIC_INPUT_IDENTITY');
      when 'AWAY_STARTER' then
        value_text := live_away_starter || '|PROBABLE';
        payload := jsonb_build_object('pitcher',live_away_starter,'status','PROBABLE','model_role','NUMERIC_INPUT_IDENTITY');
      when 'HOME_LINEUP' then
        value_text := home_order::text;
        payload := jsonb_build_object('batting_order',home_order,'status','CONFIRMED','model_role','ROLE_AND_IDENTITY_GATE');
      when 'AWAY_LINEUP' then
        value_text := away_order::text;
        payload := jsonb_build_object('batting_order',away_order,'status','CONFIRMED','model_role','ROLE_AND_IDENTITY_GATE');
      when 'BULLPEN_STATUS' then
        source_name := 'WOW_MLB_FORWARD_BULLPEN_WORKLOAD';
        source_grade := 'PRIMARY';
        ttl := 3600;
        value_text := jsonb_build_object(
          'home_relief_pitches_3d',home_bp.relief_pitches,
          'home_relief_outs_3d',home_bp.relief_outs,
          'home_relief_appearances_3d',home_bp.relief_appearances,
          'away_relief_pitches_3d',away_bp.relief_pitches,
          'away_relief_outs_3d',away_bp.relief_outs,
          'away_relief_appearances_3d',away_bp.relief_appearances
        )::text;
        payload := jsonb_build_object(
          'home',to_jsonb(home_bp),
          'away',to_jsonb(away_bp),
          'model_role','NUMERIC_INPUT_CONFIRMED'
        );
      when 'WEATHER_STATUS' then
        ttl := 1800;
        value_text := coalesce(live#>'{gameData,weather}','{}'::jsonb)::text;
        payload := jsonb_build_object(
          'weather',coalesce(live#>'{gameData,weather}','{}'::jsonb),
          'model_role','CONTEXT_ONLY_NOT_NUMERIC_IN_CERTIFIED_V2D_BASELINE'
        );
      when 'INJURY_STATUS' then
        source_name := 'FITTED_MODEL_INPUT_CONTRACT';
        source_grade := 'PRIMARY';
        evidence_status := 'NOT_APPLICABLE';
        ttl := 86400;
        value_text := 'SEPARATE_INJURY_FEATURE_NOT_IN_CERTIFIED_V2D_SCHEMA';
        payload := jsonb_build_object(
          'reason','SEPARATE_INJURY_FEATURE_NOT_IN_CERTIFIED_V2D_SCHEMA',
          'availability_evidence','CURRENT_CONFIRMED_LINEUPS_AND_PROBABLE_STARTERS',
          'model_role','NOT_APPLICABLE_AS_SEPARATE_NUMERIC_INPUT'
        );
      when 'SETTLEMENT' then
        source_name := 'TEAM_EVENT_REQUEST_CONTRACT';
        source_grade := 'PRIMARY';
        ttl := 86400;
        value_text := r.settlement_basis;
        payload := jsonb_build_object('settlement_basis',r.settlement_basis,'model_role','SETTLEMENT_LOCK');
    end case;

    if value_text is null then
      continue;
    end if;
    payload := payload || jsonb_build_object(
      'event_prediction_id',p_event_prediction_id,
      'score_snapshot_id',p_score_snapshot_id,
      'official_event_id',r.official_event_id,
      'retrieved_at',fetched_at
    );
    payload_hash := encode(extensions.digest(convert_to(payload::text,'UTF8'),'sha256'),'hex');

    insert into public.wow_event_source_attempts(
      source_attempt_id,event_prediction_id,evidence_kind,provider,attempt_order,
      attempted_at,attempt_status,source_ref,can_execute
    ) values (
      gen_random_uuid(),p_event_prediction_id,k,source_name,1,fetched_at,'SUCCESS',
      coalesce(se.snapshot_id::text,r.official_event_id),false
    );

    select ee.evidence_id into evidence_id
    from public.wow_event_evidence ee
    where ee.event_prediction_id=p_event_prediction_id
      and ee.evidence_kind=k
      and ee.source_name=source_name
      and ee.payload_hash=payload_hash
    order by ee.retrieved_at desc limit 1;

    if evidence_id is null then
      evidence_id := gen_random_uuid();
      insert into public.wow_event_evidence(
        evidence_id,event_prediction_id,evidence_kind,subject_side,source_name,
        source_ref,source_grade,evidence_status,evidence_timestamp,retrieved_at,
        freshness_ttl_seconds,payload_hash,evidence_value,evidence_payload,can_execute
      ) values (
        evidence_id,p_event_prediction_id,k,
        case when k like 'HOME_%' then 'HOME' when k like 'AWAY_%' then 'AWAY' else null end,
        source_name,coalesce(se.snapshot_id::text,r.official_event_id),source_grade,
        evidence_status,evidence_ts,fetched_at,ttl,payload_hash,value_text,payload,false
      );
      inserted := inserted+1;
    end if;

    insert into public.wow_event_scoring_evidence(
      scoring_evidence_id,event_prediction_id,scoring_snapshot_id,evidence_kind,
      evidence_id,payload_hash,evidence_timestamp,retrieved_at,model_timestamp,can_execute
    ) values (
      gen_random_uuid(),p_event_prediction_id,p_score_snapshot_id,k,evidence_id,
      payload_hash,evidence_ts,fetched_at,s.model_timestamp,false
    )
    on conflict(event_prediction_id,scoring_snapshot_id,evidence_kind)
    do update set
      evidence_id=excluded.evidence_id,
      payload_hash=excluded.payload_hash,
      evidence_timestamp=excluded.evidence_timestamp,
      retrieved_at=excluded.retrieved_at,
      model_timestamp=excluded.model_timestamp,
      can_execute=false;
  end loop;

  select count(*) into scoring_n
  from public.wow_event_scoring_evidence
  where event_prediction_id=p_event_prediction_id and scoring_snapshot_id=p_score_snapshot_id;

  if scoring_n < 10 then blockers:=array_append(blockers,'SCORING_EVIDENCE_SNAPSHOT_INCOMPLETE'); end if;
  return jsonb_build_object(
    'status',case when cardinality(blockers)=0 then 'PASS' else 'HOLD' end,
    'blockers',to_jsonb(blockers),
    'decision_intent',upper(p_decision_intent),
    'evidence_rows_hydrated',inserted,
    'scoring_evidence_row_count',scoring_n,
    'complete_scoring_evidence_snapshot',scoring_n>=10,
    'calibration_health_status',health.calibration_health_status,
    'governed_probability_capability',deployment->>'governed_probability_capability',
    'failure_path_status',failure->>'status',
    'probability_publishable',false,
    'rank_eligible',false,
    'can_execute',false
  );
end;
$function$;

create or replace function public.wow_v17_assess_mlb_event_calibration_health(
  p_event_prediction_id uuid
)
returns jsonb
language plpgsql
set search_path to ''
as $function$
declare
  r public.wow_event_predictions%rowtype;
  s public.wow_mlb_forward_score_snapshots%rowtype;
  c public.wow_mlb_v2d_intercept_calibration%rowtype;
  h public.wow_mlb_v2d_calibration_health%rowtype;
  a public.wow_mlb_event_fitted_model_artifacts%rowtype;
  reasons text[] := '{}';
  status text;
begin
  select * into r from public.wow_event_predictions where event_prediction_id=p_event_prediction_id for update;
  if not found then raise exception 'event prediction not found'; end if;
  select * into s from public.wow_mlb_forward_score_snapshots where score_snapshot_id=r.scoring_snapshot_id;
  if not found then reasons:=array_append(reasons,'MLB_SCORE_SNAPSHOT_UNAVAILABLE'); end if;
  if s.score_snapshot_id is not null then
    select * into c from public.wow_mlb_v2d_intercept_calibration where calibration_id=s.calibration_id;
    if not found then reasons:=array_append(reasons,'MLB_CALIBRATOR_UNAVAILABLE'); end if;
    select * into h from public.wow_mlb_v2d_calibration_health where spec_id=s.spec_id order by assessed_at desc limit 1;
    if not found or h.calibration_health_status<>'PASS' then reasons:=array_append(reasons,'MLB_CALIBRATION_HEALTH_NOT_PASS'); end if;
    select * into a from public.wow_mlb_event_fitted_model_artifacts
    where active=true and promoted=true
      and artifact_payload->>'baseline_spec_id'=s.spec_id::text
      and specialist_calibration_identity->>'calibration_id'=s.calibration_id::text
    order by promoted_at desc,created_at desc limit 1;
    if not found then reasons:=array_append(reasons,'CERTIFIED_MLB_EVENT_ARTIFACT_UNAVAILABLE'); end if;
  end if;
  if c.calibration_id is not null then
    if r.calibration_method is distinct from c.method then reasons:=array_append(reasons,'CALIBRATION_METHOD_MISMATCH'); end if;
    if r.calibration_version is distinct from c.calibration_id::text then reasons:=array_append(reasons,'CALIBRATION_VERSION_MISMATCH'); end if;
    if r.calibration_training_n is distinct from c.prior_games then reasons:=array_append(reasons,'CALIBRATION_TRAINING_N_MISMATCH'); end if;
  end if;
  status:=case when cardinality(reasons)=0 then 'PASS' else 'FAIL' end;
  update public.wow_event_predictions
  set calibration_health_status=status,rank_eligible=false,rank_eligibility_status='NOT_EVALUATED',probability_publishable=false
  where event_prediction_id=p_event_prediction_id;
  return jsonb_build_object('calibration_health_status',status,'reasons',to_jsonb(reasons),'can_execute',false);
end;
$function$;

create or replace function public.wow_v17_audit_probability_only_event(
  p_event_prediction_id uuid
)
returns jsonb
language plpgsql
set search_path to ''
as $function$
declare
  r public.wow_event_predictions%rowtype;
  reasons text[] := '{}';
  audit text;
begin
  select * into r from public.wow_event_predictions where event_prediction_id=p_event_prediction_id for update;
  if not found then raise exception 'event prediction not found'; end if;
  if r.raw_home_probability is null or r.raw_away_probability is null then reasons:=array_append(reasons,'RAW_PROBABILITY_MISSING'); end if;
  if r.independent_home_probability is null or r.independent_away_probability is null then reasons:=array_append(reasons,'INDEPENDENT_PROBABILITY_MISSING'); end if;
  if coalesce(r.independent_model_weight,0)<=0 then reasons:=array_append(reasons,'NO_INDEPENDENT_SUPPORT'); end if;
  if coalesce(r.market_prior_available,false)=false and coalesce(r.market_prior_weight,0)<>0 then reasons:=array_append(reasons,'MARKET_WEIGHT_NONZERO_WITHOUT_MARKET'); end if;
  if r.calibrated_home_probability is null or r.calibrated_away_probability is null
     or r.calibrated_home_lower_bound is null or r.calibrated_away_lower_bound is null
     or r.calibrated_home_upper_bound is null or r.calibrated_away_upper_bound is null then reasons:=array_append(reasons,'CALIBRATED_RANGE_INCOMPLETE'); end if;
  if r.calibration_method is null or r.calibration_version is null or r.calibration_training_n is null or r.bounds_method_version is null then reasons:=array_append(reasons,'CALIBRATION_PROVENANCE_INCOMPLETE'); end if;
  if r.uncertainty_components is null then reasons:=array_append(reasons,'UNCERTAINTY_COMPONENTS_MISSING'); end if;
  if r.model_version is null or r.model_timestamp is null then reasons:=array_append(reasons,'MODEL_PROVENANCE_INCOMPLETE'); end if;
  if r.source_snapshot_id is null or r.source_snapshot_timestamp is null then reasons:=array_append(reasons,'SOURCE_SNAPSHOT_PROVENANCE_INCOMPLETE'); end if;
  if r.source_coverage_status<>'COMPLETE' then reasons:=array_append(reasons,'SOURCE_COVERAGE_NOT_COMPLETE'); end if;
  if r.source_conflict then reasons:=array_append(reasons,'SOURCE_CONFLICT'); end if;
  if not r.model_valid_after_latest_update or r.probability_invalidated or r.rerun_required then reasons:=array_append(reasons,'STALE_MODEL_INVALIDATED'); end if;
  audit:=case when cardinality(reasons)=0 then 'PASS_PROBABILITY_AUDIT' else 'PROBABILITY_AUDIT_FAILURE' end;
  update public.wow_event_predictions
  set market_independence_status=case when coalesce(independent_model_weight,0)>0 then 'PASS' else 'FAIL' end,
      probability_audit_result=audit,rank_eligible=false,rank_eligibility_status='NOT_EVALUATED',probability_publishable=false
  where event_prediction_id=p_event_prediction_id;
  return jsonb_build_object('probability_audit_result',audit,'market_independence_status','PASS','reasons',to_jsonb(reasons),'market_required',false,'can_execute',false);
end;
$function$;

create or replace function public.wow_v17_apply_probability_only_decision(
  p_event_prediction_id uuid,
  p_min_lower_bound_gap numeric default 0.04
)
returns jsonb
language plpgsql
set search_path to ''
as $function$
declare
  r public.wow_event_predictions%rowtype;
  gap numeric;
  selected text;
  decision text;
  reason text;
begin
  select * into r from public.wow_event_predictions where event_prediction_id=p_event_prediction_id for update;
  if not found then raise exception 'event prediction not found'; end if;
  if r.probability_audit_result is distinct from 'PASS_PROBABILITY_AUDIT' or r.calibration_health_status<>'PASS' or r.governed_probability_capability<>'AVAILABLE' then
    decision:='NO_PICK_UNCALIBRATED'; selected:=null; gap:=null; reason:='PROBABILITY_OR_CALIBRATION_NOT_GOVERNED';
  else
    gap:=abs(r.calibrated_home_lower_bound-r.calibrated_away_lower_bound);
    if gap<p_min_lower_bound_gap then
      decision:='NO_PICK_CLOSE_GAME'; selected:=null; reason:='LOWER_BOUND_GAP_BELOW_MINIMUM';
    elsif r.calibrated_home_lower_bound>r.calibrated_away_lower_bound then
      decision:='SELECTED'; selected:=r.home_team; reason:='HOME_HIGHER_CALIBRATED_LOWER_BOUND';
    else
      decision:='SELECTED'; selected:=r.away_team; reason:='AWAY_HIGHER_CALIBRATED_LOWER_BOUND';
    end if;
  end if;
  update public.wow_event_predictions
  set event_decision=decision,selected_participant=selected,selected_market_role=null,
      lower_bound_gap=gap,event_mutex_status='PASS',rank_eligible=false,
      rank_eligibility_status='NOT_EVALUATED',rank_eligibility_reasons=case when selected is null then array[decision] else '{}'::text[] end,
      probability_publishable=false
  where event_prediction_id=p_event_prediction_id;
  return jsonb_build_object('event_decision',decision,'selected_participant',selected,'selected_market_role',null,'lower_bound_gap',gap,'minimum_required_lower_bound_gap',p_min_lower_bound_gap,'decision_reason',reason,'selected_participant_count',case when selected is null then 0 else 1 end,'event_mutex_status','PASS','market_required',false,'can_execute',false);
end;
$function$;

create or replace function public.wow_v17_evaluate_probability_only_rank(
  p_event_prediction_id uuid
)
returns jsonb
language plpgsql
set search_path to ''
as $function$
declare
  r public.wow_event_predictions%rowtype;
  reasons text[] := '{}';
  scoring_n integer;
  pass boolean;
begin
  select * into r from public.wow_event_predictions where event_prediction_id=p_event_prediction_id for update;
  if not found then raise exception 'event prediction not found'; end if;
  if not r.model_ready or r.model_readiness_status<>'PASS' then reasons:=array_append(reasons,'MODEL_NOT_READY'); end if;
  select count(*) into scoring_n from public.wow_event_scoring_evidence where event_prediction_id=p_event_prediction_id and scoring_snapshot_id=r.scoring_snapshot_id;
  if r.scoring_snapshot_id is null or scoring_n<10 then reasons:=array_append(reasons,'SCORING_EVIDENCE_SNAPSHOT_INCOMPLETE'); end if;
  if r.probability_audit_result is distinct from 'PASS_PROBABILITY_AUDIT' then reasons:=array_append(reasons,'PROBABILITY_AUDIT_NOT_PASS'); end if;
  if r.calibration_health_status<>'PASS' then reasons:=array_append(reasons,'CALIBRATION_HEALTH_NOT_PASS'); end if;
  if r.governed_probability_capability<>'AVAILABLE' then reasons:=array_append(reasons,'GOVERNED_PROBABILITY_CAPABILITY_UNAVAILABLE'); end if;
  if r.selected_participant is null then reasons:=array_append(reasons,'SELECTED_PARTICIPANT_UNRESOLVED'); end if;
  if r.model_timestamp is null or not r.model_valid_after_latest_update or r.probability_invalidated or r.rerun_required then reasons:=array_append(reasons,'MODEL_STALE_OR_INVALIDATED'); end if;
  pass:=cardinality(reasons)=0;
  update public.wow_event_predictions
  set rank_eligibility_status=case when pass then 'PASS' else 'FAIL' end,
      rank_eligibility_reasons=reasons,rank_eligible=pass,probability_publishable=false
  where event_prediction_id=p_event_prediction_id;
  return jsonb_build_object('rank_eligible',pass,'rank_eligibility_status',case when pass then 'PASS' else 'FAIL' end,'reasons',to_jsonb(reasons),'market_required',false,'can_execute',false);
end;
$function$;

create or replace function public.wow_v17_probability_only_final_refresh(
  p_event_prediction_id uuid,
  p_refresh_snapshot_id uuid,
  p_as_of timestamptz default now()
)
returns jsonb
language plpgsql
set search_path to ''
as $function$
declare
  r public.wow_event_predictions%rowtype;
  k text;
  e record;
  sc record;
  reasons text[] := '{}';
  event_fresh boolean:=true;
  critical_fresh boolean:=true;
  settlement_fresh boolean:=true;
  material_change boolean:=false;
  status text;
  age_seconds integer;
  event_age integer;
  critical_age integer:=0;
  settlement_age integer;
  kinds text[]:=array['EVENT_STATUS','HOME_STARTER','AWAY_STARTER','HOME_LINEUP','AWAY_LINEUP','BULLPEN_STATUS','WEATHER_STATUS','INJURY_STATUS','SETTLEMENT'];
begin
  select * into r from public.wow_event_predictions where event_prediction_id=p_event_prediction_id for update;
  if not found then raise exception 'event prediction not found'; end if;
  foreach k in array kinds loop
    select evidence_status,source_grade,evidence_timestamp,freshness_ttl_seconds,payload_hash,retrieved_at into e
    from public.wow_event_evidence
    where event_prediction_id=p_event_prediction_id and evidence_kind=k
    order by retrieved_at desc,evidence_timestamp desc limit 1;
    if not found then
      reasons:=array_append(reasons,k||'_NOT_CALLED');
      if k='EVENT_STATUS' then event_fresh:=false; elsif k='SETTLEMENT' then settlement_fresh:=false; else critical_fresh:=false; end if;
    else
      age_seconds:=greatest(0,extract(epoch from (p_as_of-e.evidence_timestamp))::integer);
      if k='EVENT_STATUS' then event_age:=age_seconds; elsif k='SETTLEMENT' then settlement_age:=age_seconds; else critical_age:=greatest(critical_age,age_seconds); end if;
      if e.evidence_status not in ('RETRIEVED','NOT_APPLICABLE') or e.source_grade='PROXY' or e.payload_hash is null or age_seconds>e.freshness_ttl_seconds then
        reasons:=array_append(reasons,k||'_NOT_FRESH');
        if k='EVENT_STATUS' then event_fresh:=false; elsif k='SETTLEMENT' then settlement_fresh:=false; else critical_fresh:=false; end if;
      end if;
      select payload_hash into sc from public.wow_event_scoring_evidence
      where event_prediction_id=p_event_prediction_id and scoring_snapshot_id=r.scoring_snapshot_id and evidence_kind=k limit 1;
      if not found then reasons:=array_append(reasons,k||'_SCORING_HASH_MISSING'); material_change:=true;
      elsif e.payload_hash is distinct from sc.payload_hash then reasons:=array_append(reasons,k||'_MATERIAL_CHANGE_AFTER_MODEL'); material_change:=true; end if;
    end if;
  end loop;
  if r.event_status<>'SCHEDULED' then reasons:=array_append(reasons,'EVENT_NOT_SCHEDULED'); event_fresh:=false; end if;
  if r.source_conflict then reasons:=array_append(reasons,'SOURCE_CONFLICT'); critical_fresh:=false; end if;
  status:=case when material_change then 'RERUN_REQUIRED' when cardinality(reasons)>0 then 'FAIL' else 'PASS' end;
  update public.wow_event_predictions
  set final_refresh_snapshot_id=p_refresh_snapshot_id,final_refresh_timestamp=p_as_of,final_refresh_status=status,
      final_refresh_reasons=reasons,event_status_fresh_at_refresh=event_fresh,critical_status_fresh_at_refresh=critical_fresh,
      market_fresh_at_refresh=false,settlement_fresh_at_refresh=settlement_fresh,event_status_age_seconds_at_refresh=event_age,
      critical_status_age_seconds_at_refresh=critical_age,market_age_seconds_at_refresh=null,settlement_age_seconds_at_refresh=settlement_age,
      probability_invalidated=case when material_change then true else probability_invalidated end,
      rerun_required=case when material_change then true else rerun_required end,
      rank_eligible=case when status='PASS' then rank_eligible else false end,
      rank_eligibility_status=case when status='PASS' then rank_eligibility_status else 'FAIL' end,
      probability_publishable=false
  where event_prediction_id=p_event_prediction_id;
  return jsonb_build_object('final_refresh_status',status,'reasons',to_jsonb(reasons),'event_status_fresh',event_fresh,'critical_status_fresh',critical_fresh,'market_fresh',false,'market_required',false,'settlement_fresh',settlement_fresh,'material_change_after_model',material_change,'probability_invalidated',material_change,'rerun_required',material_change,'can_execute',false);
end;
$function$;

-- Preserve the existing market-dependent bridge under a stable legacy name, then
-- restore the canonical function name as a V17 intent router.
do $do$
begin
  if to_regprocedure('public.wow_v17_mlb_team_event_governance_bridge_legacy(uuid,text,text,text,text,text)') is null then
    alter function public.wow_v17_mlb_team_event_governance_bridge(uuid,text,text,text,text,text)
      rename to wow_v17_mlb_team_event_governance_bridge_legacy;
  end if;
end
$do$;

create or replace function public.wow_v17_mlb_probability_only_governance_bridge(
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
  legacy jsonb;
  event_id uuid;
  failure jsonb;
  identity jsonb;
  sources jsonb;
  ready jsonb;
  audit jsonb;
  calibration jsonb;
  decision jsonb;
  rank_gate jsonb;
  refresh jsonb;
  publication jsonb;
  terminal jsonb;
  r public.wow_event_predictions%rowtype;
  bridge_blockers text[] := '{}';
  final_status text:='HOLD';
begin
  legacy:=public.wow_v17_mlb_team_event_governance_bridge_legacy(
    p_score_snapshot_id,p_research_run_id,p_event_key,p_requested_timezone,p_candidate_family,p_decision_intent
  );
  event_id:=nullif(legacy->>'event_prediction_id','')::uuid;
  if event_id is null then return legacy; end if;

  update public.wow_event_predictions
  set decision_intent=upper(p_decision_intent),
      blockers=array_remove(array_remove(coalesce(blockers,'{}'::text[]),'CALIBRATION_HEALTH_FORWARD_EVIDENCE_PENDING'),'PRODUCTION_FEATURE_READY_FALSE')
  where event_prediction_id=event_id;

  failure:=public.wow_v17_team_failure_path_gate(event_id);
  if failure->>'status'='PASS' then
    update public.wow_event_predictions
    set blockers=array_remove(array_remove(array_remove(array_remove(coalesce(blockers,'{}'::text[]),
      'FAVORITE_FAILURE_PATHS_MISSING'),'FAVORITE_FAILURE_PATH_PROBABILITY_MISSING'),'LARGEST_FAVORITE_LOSS_PATH_MISSING'),'UNDERDOG_UPSET_PATH_MISSING')
    where event_prediction_id=event_id;
  end if;

  identity:=public.wow_evaluate_event_identity_lock(event_id,now());
  sources:=public.wow_refresh_event_source_completeness(event_id,now());
  ready:=public.wow_evaluate_event_model_readiness(event_id);
  audit:=public.wow_v17_audit_probability_only_event(event_id);
  calibration:=public.wow_v17_assess_mlb_event_calibration_health(event_id);
  decision:=public.wow_v17_apply_probability_only_decision(event_id,0.04);
  rank_gate:=public.wow_v17_evaluate_probability_only_rank(event_id);
  refresh:=public.wow_v17_probability_only_final_refresh(event_id,gen_random_uuid(),now());

  select * into r from public.wow_event_predictions where event_prediction_id=event_id;
  if failure->>'status'='PASS'
     and r.identity_lock_status='PASS'
     and r.source_completeness_status='PASS'
     and r.model_readiness_status='PASS'
     and r.probability_audit_result='PASS_PROBABILITY_AUDIT'
     and r.calibration_health_status='PASS'
     and r.governed_probability_capability='AVAILABLE'
     and r.event_decision='SELECTED'
     and r.rank_eligible=true
     and r.final_refresh_status='PASS' then
    publication:=public.wow_mark_event_publishable(event_id);
  else
    publication:=jsonb_build_object('status','BLOCKED','probability_publishable',false,'can_execute',false);
  end if;
  terminal:=public.wow_reduce_event_terminal_label(event_id,now());
  select * into r from public.wow_event_predictions where event_prediction_id=event_id;

  bridge_blockers:=coalesce(r.blockers,'{}'::text[]);
  if failure->>'status'<>'PASS' then bridge_blockers:=array_append(bridge_blockers,'TEAM_FAILURE_PATH_GATE_NOT_PASS'); end if;
  if r.identity_lock_status<>'PASS' then bridge_blockers:=array_append(bridge_blockers,'IDENTITY_LOCK_NOT_PASS'); end if;
  if r.source_completeness_status<>'PASS' then bridge_blockers:=array_append(bridge_blockers,'SOURCE_COMPLETENESS_NOT_PASS'); end if;
  if r.model_readiness_status<>'PASS' then bridge_blockers:=array_append(bridge_blockers,'MODEL_READINESS_NOT_PASS'); end if;
  if r.probability_audit_result<>'PASS_PROBABILITY_AUDIT' then bridge_blockers:=array_append(bridge_blockers,'PROBABILITY_AUDIT_NOT_PASS'); end if;
  if r.calibration_health_status<>'PASS' then bridge_blockers:=array_append(bridge_blockers,'CALIBRATION_HEALTH_NOT_PASS'); end if;
  if not r.rank_eligible then bridge_blockers:=array_append(bridge_blockers,'RANK_ELIGIBILITY_NOT_PASS'); end if;
  if r.final_refresh_status<>'PASS' then bridge_blockers:=array_append(bridge_blockers,'FINAL_REFRESH_NOT_PASS'); end if;
  select coalesce(array_agg(distinct x order by x),'{}'::text[]) into bridge_blockers from unnest(bridge_blockers) x;
  final_status:=case when r.probability_publishable and r.rank_eligible and r.terminal_label='FINAL_APPROVED' then 'PASS' else 'HOLD' end;

  return jsonb_build_object(
    'status',final_status,
    'event_prediction_id',event_id,
    'score_snapshot_id',p_score_snapshot_id,
    'decision_intent',upper(p_decision_intent),
    'probability_audit_result',r.probability_audit_result,
    'calibration_health_status',r.calibration_health_status,
    'event_decision',r.event_decision,
    'selected_participant',r.selected_participant,
    'event_mutex_status',r.event_mutex_status,
    'team_failure_path_status',failure->>'status',
    'team_failure_path_gate',failure,
    'rank_eligible',r.rank_eligible,
    'final_refresh_status',r.final_refresh_status,
    'terminal_label',r.terminal_label,
    'terminal_ceiling',r.terminal_ceiling,
    'blockers',to_jsonb(bridge_blockers),
    'probability_publishable',r.probability_publishable,
    'global_terminal_reducer','V17_TERMINAL_REDUCER',
    'market_required',false,
    'can_execute',false,
    'identity_gate',identity,
    'source_gate',sources,
    'model_readiness',ready,
    'probability_audit',audit,
    'calibration_gate',calibration,
    'decision_gate',decision,
    'rank_gate',rank_gate,
    'final_refresh',refresh,
    'publication',publication,
    'terminal',terminal
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
begin
  if upper(coalesce(p_decision_intent,'')) in ('WINNER','BEST_SIDE') then
    return public.wow_v17_mlb_probability_only_governance_bridge(
      p_score_snapshot_id,p_research_run_id,p_event_key,p_requested_timezone,p_candidate_family,p_decision_intent
    );
  end if;
  return public.wow_v17_mlb_team_event_governance_bridge_legacy(
    p_score_snapshot_id,p_research_run_id,p_event_key,p_requested_timezone,p_candidate_family,p_decision_intent
  );
end;
$function$;
