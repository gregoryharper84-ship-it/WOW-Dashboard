-- WOW V17 NFL governed team/event probability publication substrate.
-- Additive only. This migration does not weaken MLB/prop governance and never
-- enables wager execution. Runtime probability publication remains subordinate
-- to V17_TERMINAL_REDUCER.

create table if not exists public.wow_nfl_event_fitted_model_artifacts (
  artifact_id uuid primary key default gen_random_uuid(),
  provider_identity text not null default 'WOW_NFL_EVENT_FITTED_MODEL_V1',
  model_family text not null,
  model_artifact_version text not null unique,
  artifact_format text not null,
  artifact_payload jsonb not null,
  artifact_checksum text not null,
  bundle_fingerprint text not null,
  feature_schema_version text not null,
  feature_transform_version text not null,
  training_code_sha text not null,
  training_dataset_hash text not null,
  training_rows integer not null,
  validation_metrics jsonb not null default '{}'::jsonb,
  calibrator_id uuid references public.wow_calibrators(calibrator_id),
  certification_id text,
  lifecycle_state text not null default 'CANDIDATE',
  active boolean not null default false,
  promoted boolean not null default false,
  probability_publishable boolean not null default false,
  can_execute boolean not null default false,
  created_at timestamptz not null default now(),
  promoted_at timestamptz,
  retired_at timestamptz,
  constraint wow_nfl_event_fitted_provider_identity
    check (provider_identity = 'WOW_NFL_EVENT_FITTED_MODEL_V1'),
  constraint wow_nfl_event_fitted_checksum_shape
    check (artifact_checksum ~ '^[0-9a-f]{64}$'),
  constraint wow_nfl_event_fitted_bundle_shape
    check (bundle_fingerprint ~ '^[0-9a-f]{64}$'),
  constraint wow_nfl_event_fitted_code_sha_shape
    check (training_code_sha ~ '^[0-9a-f]{40,64}$'),
  constraint wow_nfl_event_fitted_dataset_hash_shape
    check (training_dataset_hash ~ '^[0-9a-f]{64}$'),
  constraint wow_nfl_event_fitted_training_rows_positive
    check (training_rows > 0),
  constraint wow_nfl_event_fitted_payload_object
    check (jsonb_typeof(artifact_payload) = 'object'),
  constraint wow_nfl_event_fitted_validation_object
    check (jsonb_typeof(validation_metrics) = 'object'),
  constraint wow_nfl_event_fitted_lifecycle
    check (lifecycle_state in ('CANDIDATE','PROSPECTIVE_CERTIFIED','CHAMPION','RETIRED','BLOCKED')),
  constraint wow_nfl_event_fitted_certified_requirements
    check (
      lifecycle_state not in ('PROSPECTIVE_CERTIFIED','CHAMPION')
      or (
        active = true and promoted = true and calibrator_id is not null
        and certification_id is not null and length(trim(certification_id)) > 0
        and promoted_at is not null
      )
    ),
  constraint wow_nfl_event_fitted_registry_not_probability_publication
    check (probability_publishable = false),
  constraint wow_nfl_event_fitted_never_execute
    check (can_execute = false)
);

create unique index if not exists wow_nfl_event_fitted_one_active_champion
  on public.wow_nfl_event_fitted_model_artifacts(feature_schema_version)
  where active = true and lifecycle_state = 'CHAMPION';

create index if not exists wow_nfl_event_fitted_route_lookup
  on public.wow_nfl_event_fitted_model_artifacts(
    feature_schema_version, lifecycle_state, active, promoted, created_at desc
  );

alter table public.wow_nfl_event_fitted_model_artifacts enable row level security;

create table if not exists public.wow_nfl_event_predictions (
  event_prediction_id uuid primary key default gen_random_uuid(),
  score_snapshot_id uuid not null unique default gen_random_uuid(),
  created_at timestamptz not null default now(),
  research_run_id text not null,
  event_key text not null,
  official_event_id text not null,
  requested_slate_date date not null,
  requested_timezone text not null,
  event_start_time_utc timestamptz not null,
  home_team text not null,
  away_team text not null,
  selected_participant text not null,
  opponent text not null,
  source_snapshot_id uuid not null references public.wow_nfl_source_snapshots(snapshot_id),
  source_snapshot_timestamp timestamptz not null,
  latest_material_update_timestamp timestamptz,
  feature_row_hash text not null,
  feature_schema_version text not null,
  feature_order_hash text not null,
  model_artifact_id uuid not null references public.wow_nfl_event_fitted_model_artifacts(artifact_id),
  model_family text not null,
  model_version text not null,
  model_timestamp timestamptz not null,
  raw_home_probability numeric not null,
  raw_away_probability numeric not null,
  calibrated_home_probability numeric not null,
  calibrated_away_probability numeric not null,
  calibrated_home_lower_bound numeric not null,
  calibrated_home_upper_bound numeric not null,
  calibrated_away_lower_bound numeric not null,
  calibrated_away_upper_bound numeric not null,
  calibrated_selection_probability numeric not null,
  calibrated_selection_lower_bound numeric not null,
  calibrated_selection_upper_bound numeric not null,
  calibration_method text not null,
  calibration_version text not null,
  calibration_health_status text not null,
  calibration_training_n integer not null,
  uncertainty_method text not null,
  ranking_basis text not null default 'CALIBRATED_LOWER_BOUND',
  ranked_probability numeric not null,
  model_package_valid boolean not null default false,
  provenance_complete boolean not null default false,
  model_probability_publishable boolean not null default false,
  blend_publishable boolean not null default false,
  can_execute boolean not null default false,
  model_output_snapshot jsonb not null,
  constraint wow_nfl_event_prediction_distinct_teams
    check (home_team <> away_team),
  constraint wow_nfl_event_prediction_selected_team
    check (selected_participant = home_team or selected_participant = away_team),
  constraint wow_nfl_event_prediction_opponent_team
    check ((opponent = home_team or opponent = away_team) and opponent <> selected_participant),
  constraint wow_nfl_event_prediction_hashes
    check (feature_row_hash ~ '^[0-9a-f]{64}$' and feature_order_hash ~ '^[0-9a-f]{64}$'),
  constraint wow_nfl_event_prediction_probability_range
    check (
      raw_home_probability > 0 and raw_home_probability < 1
      and raw_away_probability > 0 and raw_away_probability < 1
      and calibrated_home_probability > 0 and calibrated_home_probability < 1
      and calibrated_away_probability > 0 and calibrated_away_probability < 1
      and calibrated_home_lower_bound > 0 and calibrated_home_upper_bound < 1
      and calibrated_away_lower_bound > 0 and calibrated_away_upper_bound < 1
      and calibrated_selection_probability > 0 and calibrated_selection_probability < 1
      and calibrated_selection_lower_bound > 0 and calibrated_selection_upper_bound < 1
      and ranked_probability > 0 and ranked_probability < 1
    ),
  constraint wow_nfl_event_prediction_raw_sum
    check (abs((raw_home_probability + raw_away_probability) - 1.0) <= 0.000001),
  constraint wow_nfl_event_prediction_cal_sum
    check (abs((calibrated_home_probability + calibrated_away_probability) - 1.0) <= 0.000001),
  constraint wow_nfl_event_prediction_home_bounds
    check (calibrated_home_lower_bound <= calibrated_home_probability and calibrated_home_probability <= calibrated_home_upper_bound),
  constraint wow_nfl_event_prediction_away_bounds
    check (calibrated_away_lower_bound <= calibrated_away_probability and calibrated_away_probability <= calibrated_away_upper_bound),
  constraint wow_nfl_event_prediction_selection_bounds
    check (calibrated_selection_lower_bound <= calibrated_selection_probability and calibrated_selection_probability <= calibrated_selection_upper_bound),
  constraint wow_nfl_event_prediction_calibration_pass
    check (calibration_health_status = 'PASS'),
  constraint wow_nfl_event_prediction_training_n
    check (calibration_training_n > 0),
  constraint wow_nfl_event_prediction_rank_basis
    check (ranking_basis = 'CALIBRATED_LOWER_BOUND'),
  constraint wow_nfl_event_prediction_rank_value
    check (abs(ranked_probability - calibrated_selection_lower_bound) <= 0.000001),
  constraint wow_nfl_event_prediction_no_blend
    check (blend_publishable = false),
  constraint wow_nfl_event_prediction_never_execute
    check (can_execute = false)
);

alter table public.wow_nfl_event_predictions enable row level security;

create or replace function public.wow_v17_reject_nfl_prediction_mutation()
returns trigger
language plpgsql
set search_path to ''
as $function$
begin
  raise exception 'WOW_NFL_EVENT_PREDICTION_IMMUTABLE';
end;
$function$;

drop trigger if exists wow_nfl_event_predictions_immutable on public.wow_nfl_event_predictions;
create trigger wow_nfl_event_predictions_immutable
before update or delete on public.wow_nfl_event_predictions
for each row execute function public.wow_v17_reject_nfl_prediction_mutation();

create or replace function public.wow_v17_promote_nfl_model_bundle(
  p_calibrator jsonb,
  p_artifact jsonb
)
returns jsonb
language plpgsql
set search_path to ''
as $function$
declare
  v_calibrator_id uuid := coalesce(nullif(p_calibrator->>'calibrator_id','')::uuid, gen_random_uuid());
  v_artifact_id uuid := coalesce(nullif(p_artifact->>'artifact_id','')::uuid, gen_random_uuid());
  v_now timestamptz := now();
begin
  if coalesce(p_calibrator->>'sport','') <> 'NFL'
     or coalesce(p_calibrator->>'market_family','') <> 'OUTRIGHT_WINNER'
     or coalesce(p_artifact->>'provider_identity','') <> 'WOW_NFL_EVENT_FITTED_MODEL_V1'
     or coalesce((p_artifact->>'can_execute')::boolean,false) <> false then
    raise exception 'NFL_MODEL_BUNDLE_IDENTITY_INVALID';
  end if;

  update public.wow_nfl_event_fitted_model_artifacts
     set active=false, promoted=false, lifecycle_state='RETIRED', retired_at=v_now
   where active=true;
  update public.wow_calibrators
     set active=false, promoted=false
   where active=true and sport='NFL' and market_family='OUTRIGHT_WINNER';

  insert into public.wow_calibrators(
    calibrator_id, phase, calibration_method, calibration_version, parent_cohort,
    training_n, fit_start, fit_end, fit_metrics_json, platt_a, platt_b,
    fold_train_audit_json, bounds_method_version, promoted, active,
    sport, market_family, model_family, validation_status, health_status,
    source_data_hash, split_hash, brier_score, log_loss, calibration_error,
    live_bounds_json, fitted_at
  ) values (
    v_calibrator_id,
    p_calibrator->>'phase', p_calibrator->>'calibration_method', p_calibrator->>'calibration_version', p_calibrator->>'parent_cohort',
    (p_calibrator->>'training_n')::int, nullif(p_calibrator->>'fit_start','')::timestamptz, nullif(p_calibrator->>'fit_end','')::timestamptz,
    coalesce(p_calibrator->'fit_metrics_json','{}'::jsonb), (p_calibrator->>'platt_a')::numeric, (p_calibrator->>'platt_b')::numeric,
    p_calibrator->'fold_train_audit_json', p_calibrator->>'bounds_method_version', true, true,
    'NFL','OUTRIGHT_WINNER',p_calibrator->>'model_family','PASS','PASS',
    p_calibrator->>'source_data_hash',p_calibrator->>'split_hash',(p_calibrator->>'brier_score')::numeric,(p_calibrator->>'log_loss')::numeric,(p_calibrator->>'calibration_error')::numeric,
    p_calibrator->'live_bounds_json',v_now
  );

  insert into public.wow_nfl_event_fitted_model_artifacts(
    artifact_id, provider_identity, model_family, model_artifact_version, artifact_format,
    artifact_payload, artifact_checksum, bundle_fingerprint, feature_schema_version,
    feature_transform_version, training_code_sha, training_dataset_hash, training_rows,
    validation_metrics, calibrator_id, certification_id, lifecycle_state, active,
    promoted, probability_publishable, can_execute, promoted_at
  ) values (
    v_artifact_id,'WOW_NFL_EVENT_FITTED_MODEL_V1',p_artifact->>'model_family',p_artifact->>'model_artifact_version',p_artifact->>'artifact_format',
    p_artifact->'artifact_payload',p_artifact->>'artifact_checksum',p_artifact->>'bundle_fingerprint',p_artifact->>'feature_schema_version',
    p_artifact->>'feature_transform_version',p_artifact->>'training_code_sha',p_artifact->>'training_dataset_hash',(p_artifact->>'training_rows')::int,
    coalesce(p_artifact->'validation_metrics','{}'::jsonb),v_calibrator_id,p_artifact->>'certification_id','CHAMPION',true,
    true,false,false,v_now
  );

  return jsonb_build_object(
    'status','PROMOTED','artifact_id',v_artifact_id,'calibrator_id',v_calibrator_id,
    'model_artifact_version',p_artifact->>'model_artifact_version',
    'probability_publishable',false,'can_execute',false
  );
end;
$function$;

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
begin
  select * into r from public.wow_nfl_event_predictions where score_snapshot_id=p_score_snapshot_id;
  if not found then
    return jsonb_build_object('status','HOLD','terminal_label','MODEL_QUALIFIED_HOLD','blockers',jsonb_build_array('NFL_SCORE_SNAPSHOT_NOT_FOUND'),'global_terminal_reducer','V17_TERMINAL_REDUCER','probability_publishable',false,'rank_eligible',false,'can_execute',false);
  end if;
  select * into a from public.wow_nfl_event_fitted_model_artifacts where artifact_id=r.model_artifact_id;
  select * into c from public.wow_calibrators where calibrator_id=a.calibrator_id;

  if r.event_start_time_utc <= now() then v_blockers:=array_append(v_blockers,'EVENT_NOT_PREGAME'); end if;
  if not r.model_package_valid then v_blockers:=array_append(v_blockers,'MODEL_PACKAGE_INVALID'); end if;
  if not r.provenance_complete then v_blockers:=array_append(v_blockers,'PROVENANCE_INCOMPLETE'); end if;
  if not r.model_probability_publishable then v_blockers:=array_append(v_blockers,'MODEL_PROBABILITY_NOT_PUBLISHABLE'); end if;
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

create or replace function public.wow_v17_nfl_team_event_governance_bridge(
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
  r public.wow_nfl_event_predictions%rowtype;
  terminal jsonb;
  v_blockers text[] := '{}';
  v_pass boolean := false;
begin
  select * into r from public.wow_nfl_event_predictions where score_snapshot_id=p_score_snapshot_id;
  if not found then
    return jsonb_build_object('status','HOLD','probability_audit_result','NOT_PROVEN','event_mutex_status','NOT_PROVEN','postmodel_gates_status','HOLD','final_gates_status','HOLD','terminal_label','MODEL_QUALIFIED_HOLD','blockers',jsonb_build_array('NFL_SCORE_SNAPSHOT_NOT_FOUND'),'global_terminal_reducer','V17_TERMINAL_REDUCER','probability_publishable',false,'rank_eligible',false,'can_execute',false);
  end if;

  if r.research_run_id <> p_research_run_id then v_blockers:=array_append(v_blockers,'RESEARCH_RUN_ID_MISMATCH'); end if;
  if r.event_key <> p_event_key then v_blockers:=array_append(v_blockers,'EVENT_KEY_MISMATCH'); end if;
  if upper(coalesce(p_decision_intent,'')) not in ('WINNER','BEST_SIDE','FAVORITE','UNDERDOG','UPSET') then v_blockers:=array_append(v_blockers,'DECISION_INTENT_UNSUPPORTED'); end if;
  if upper(coalesce(p_candidate_family,'')) not in ('TEAM_EVENT','OUTRIGHT_WINNER','MONEYLINE','FAVORITE','UNDERDOG','UPSET','MATCH_WINNER','FIGHT_WINNER') then v_blockers:=array_append(v_blockers,'CANDIDATE_FAMILY_UNSUPPORTED'); end if;
  if nullif(trim(coalesce(p_requested_timezone,'')),'') is null then v_blockers:=array_append(v_blockers,'REQUESTED_TIMEZONE_MISSING'); end if;

  terminal := public.wow_v17_reduce_nfl_event_terminal_label(p_score_snapshot_id);
  v_pass := cardinality(v_blockers)=0 and terminal->>'status'='PASS';

  return jsonb_build_object(
    'status',case when v_pass then 'PASS' else 'HOLD' end,
    'event_prediction_id',r.event_prediction_id,
    'score_snapshot_id',r.score_snapshot_id,
    'decision_intent',upper(p_decision_intent),
    'probability_audit_result',case when r.model_package_valid and r.provenance_complete then 'PASS_PROBABILITY_AUDIT' else 'FAIL_PROBABILITY_AUDIT' end,
    'calibration_health_status',r.calibration_health_status,
    'event_decision',case when v_pass then 'SELECTED' else 'HOLD' end,
    'selected_participant',r.selected_participant,
    'event_mutex_status',case when r.home_team<>r.away_team and r.selected_participant in (r.home_team,r.away_team) then 'PASS' else 'FAIL' end,
    'postmodel_gates_status',case when r.model_package_valid and r.provenance_complete and r.calibration_health_status='PASS' then 'PASS' else 'HOLD' end,
    'final_gates_status',case when v_pass then 'PASS' else 'HOLD' end,
    'rank_eligible',v_pass,
    'ranking_basis',r.ranking_basis,
    'ranked_probability',r.ranked_probability,
    'terminal_label',case when v_pass then 'FINAL_APPROVED' else coalesce(terminal->>'terminal_label','MODEL_QUALIFIED_HOLD') end,
    'terminal_ceiling',case when v_pass then 'FINAL_APPROVED' else 'MODEL_QUALIFIED_HOLD' end,
    'blockers',to_jsonb(v_blockers) || coalesce(terminal->'blockers','[]'::jsonb),
    'probability_publishable',v_pass,
    'global_terminal_reducer','V17_TERMINAL_REDUCER',
    'market_required',false,
    'blend_publishable',false,
    'can_execute',false,
    'terminal',terminal
  );
end;
$function$;
