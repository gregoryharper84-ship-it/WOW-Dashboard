-- V17 Projected-Lineup Score Bridge
--
-- Preserve the existing certified MLB scorer and its strict final-publication
-- semantics.  This migration wraps that scorer so a valid, already-scored
-- fitted probability is not erased when the *only current* publication holds
-- are lineup confirmation / post-lineup refresh requirements.
--
-- The wrapper never manufactures a probability or scenario weight.  Numeric
-- fields are read from the immutable wow_mlb_forward_score_snapshots row that
-- the certified scorer itself produced.  Final/rank publication remains held.
-- can_execute=false always.

begin;

-- Preserve the exact pre-patch scorer implementation once.  The existence
-- guard keeps replays/idempotent migration tooling safe.
do $$
begin
  if exists (
    select 1
    from pg_proc p join pg_namespace n on n.oid=p.pronamespace
    where n.nspname='public'
      and p.proname='wow_mlb_score_event_bridge'
      and pg_get_function_identity_arguments(p.oid)=
        'p_official_event_id text, p_event_start_time timestamp with time zone, p_requested_slate_date date, p_home_team text, p_away_team text, p_venue text, p_home_starting_pitcher text, p_away_starting_pitcher text, p_source_snapshot_id uuid'
  ) and not exists (
    select 1
    from pg_proc p join pg_namespace n on n.oid=p.pronamespace
    where n.nspname='public'
      and p.proname='wow_mlb_score_event_bridge_pre_projected_lineup'
  ) then
    execute 'alter function public.wow_mlb_score_event_bridge(text,timestamptz,date,text,text,text,text,text,uuid) rename to wow_mlb_score_event_bridge_pre_projected_lineup';
  end if;
end $$;

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
as $$
declare
  v_result jsonb;
  v_gate jsonb;
  v_score public.wow_mlb_forward_score_snapshots%rowtype;
  v_event public.wow_mlb_forward_shadow_events%rowtype;
  v_non_lineup_blocker_n integer := 0;
  v_numeric_valid boolean := false;
  v_lineup_state text := 'PROJECTED_HIGH_CONFIDENCE';
begin
  v_result := public.wow_mlb_score_event_bridge_pre_projected_lineup(
    p_official_event_id,
    p_event_start_time,
    p_requested_slate_date,
    p_home_team,
    p_away_team,
    p_venue,
    p_home_starting_pitcher,
    p_away_starting_pitcher,
    p_source_snapshot_id
  );

  -- Normal confirmed-lineup publication and every non-model failure keep their
  -- exact pre-patch semantics.
  if coalesce(v_result->>'status','') <> 'MODEL_SCORED_HELD'
     or coalesce(v_result->>'code','') <> 'REAL_FITTED_MODEL_PATH_PROVEN'
     or coalesce((v_result->>'scoring_evidence_produced')::boolean,false) is not true
     or coalesce((v_result->>'probability_fields_withheld')::boolean,true) is not true then
    return v_result;
  end if;

  v_gate := public.wow_governed_deployment_state();
  if coalesce(v_gate->>'deployment_contract_status','FAIL') <> 'PASS'
     or coalesce(v_gate->>'runtime_capability_status','UNAVAILABLE') <> 'AVAILABLE'
     or coalesce(v_gate->>'governed_probability_capability','UNAVAILABLE') <> 'AVAILABLE'
     or coalesce(v_gate->>'ratification_status','NOT_RATIFIED') <> 'RATIFIED'
     or coalesce(v_gate->>'calibration_health_status','UNAVAILABLE') <> 'PASS'
     or not coalesce((v_gate->>'production_feature_ready')::boolean,false)
     or not coalesce((v_gate->>'probability_publishable')::boolean,false) then
    return v_result;
  end if;

  -- Only lineup/confirmation-refresh holds may use the projected-lineup
  -- sporting-probability contract.  Any other current blocker remains strict.
  select count(*)
  into v_non_lineup_blocker_n
  from jsonb_array_elements_text(coalesce(v_result->'current_publication_blockers','[]'::jsonb)) as b(value)
  where b.value not in ('LINEUP_NOT_CONFIRMED','POST_LINEUP_SCORE_SNAPSHOT_REQUIRED')
    and b.value not like 'OFFICIAL_LINEUP_REFRESH_%';

  if v_non_lineup_blocker_n <> 0 then
    return v_result;
  end if;

  select * into v_score
  from public.wow_mlb_forward_score_snapshots
  where score_snapshot_id = nullif(v_result->>'score_snapshot_id','')::uuid
  limit 1;

  select * into v_event
  from public.wow_mlb_forward_shadow_events
  where shadow_event_id = nullif(v_result->>'shadow_event_id','')::uuid
  limit 1;

  if v_score.score_snapshot_id is null
     or v_event.shadow_event_id is null
     or clock_timestamp() >= v_event.event_start_time
     or v_event.feature_hydration_status <> 'PASS'
     or v_event.lineup_status = 'CONFIRMED' then
    return v_result;
  end if;

  v_numeric_valid :=
    v_score.raw_home_probability > 0 and v_score.raw_home_probability < 1
    and v_score.raw_away_probability > 0 and v_score.raw_away_probability < 1
    and abs((v_score.raw_home_probability + v_score.raw_away_probability) - 1.0) <= 0.000001
    and v_score.calibrated_home_probability > 0 and v_score.calibrated_home_probability < 1
    and v_score.calibrated_away_probability > 0 and v_score.calibrated_away_probability < 1
    and abs((v_score.calibrated_home_probability + v_score.calibrated_away_probability) - 1.0) <= 0.000001
    and v_score.home_lower_bound > 0 and v_score.home_lower_bound <= v_score.calibrated_home_probability
    and v_score.home_upper_bound >= v_score.calibrated_home_probability and v_score.home_upper_bound < 1
    and v_score.away_lower_bound > 0 and v_score.away_lower_bound <= v_score.calibrated_away_probability
    and v_score.away_upper_bound >= v_score.calibrated_away_probability and v_score.away_upper_bound < 1
    and coalesce(v_score.model_version,'') <> ''
    and coalesce(v_score.training_data_sha256,'') <> '';

  if not v_numeric_valid then
    return v_result;
  end if;

  -- A fully hydrated event with both canonical probable starters is the current
  -- certified high-confidence projected context.  We do not guess batting
  -- orders or create scenario weights here.
  if coalesce(v_event.home_probable_pitcher,'') = ''
     or coalesce(v_event.away_probable_pitcher,'') = '' then
    v_lineup_state := 'PROJECTED_MEDIUM_CONFIDENCE';
  end if;

  return v_result || jsonb_build_object(
    'status','MODEL_SCORED_PROJECTED_LINEUP',
    'code','LINEUP_PROJECTED_PROBABILITY_AVAILABLE',
    'lineup_state',v_lineup_state,
    'lineup_confirmation_required',true,
    'final_refresh_required',true,
    'lineup_scenario_modeling',jsonb_build_object(
      'status','CERTIFIED_CONTEXTUAL_PROJECTED_LINEUP_MODEL',
      'scenario_weights_invented_by_governor',false
    ),
    'raw_home_probability',v_score.raw_home_probability,
    'raw_away_probability',v_score.raw_away_probability,
    'calibrated_home_probability',v_score.calibrated_home_probability,
    'calibrated_away_probability',v_score.calibrated_away_probability,
    'calibrated_home_lower_bound',v_score.home_lower_bound,
    'calibrated_home_upper_bound',v_score.home_upper_bound,
    'calibrated_away_lower_bound',v_score.away_lower_bound,
    'calibrated_away_upper_bound',v_score.away_upper_bound,
    'projected_runs_home',v_score.home_mu,
    'projected_runs_away',v_score.away_mu,
    'tie_after_9_probability',v_score.tie_after_9_probability,
    'sporting_probability_completed',true,
    'sporting_probability_status','COMPLETED_HELD_LINEUP_CONFIRMATION',
    'sporting_probability_publishable',true,
    'probability_fields_withheld',false,
    'probability_publishable',true,
    'rank_eligible',false,
    'terminal_label','MODEL_QUALIFIED_HOLD',
    'terminal_ceiling','MODEL_QUALIFIED_HOLD',
    'qualification_ceiling_reason','LINEUP_CONFIRMATION_PENDING',
    'blockers',jsonb_build_array('LINEUP_CONFIRMATION_PENDING'),
    'host_terminal_authority',false,
    'global_terminal_authority','V17_TERMINAL_REDUCER',
    'can_execute',false
  );
end;
$$;

comment on function public.wow_mlb_score_event_bridge(text,timestamptz,date,text,text,text,text,text,uuid)
is 'V17 canonical MLB event scorer. Confirmed-lineup behavior delegates unchanged to pre-projected bridge. If the current governed deployment is ratified/healthy and the only current holds are lineup confirmation/final refresh, exposes the immutable fitted sporting probability with rank_eligible=false and MODEL_QUALIFIED_HOLD. Never invents scenario weights; can_execute=false.';

commit;
