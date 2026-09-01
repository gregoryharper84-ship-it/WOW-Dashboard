-- WOW v16 Clean Core — MLB prospective specialist serving boundary.
--
-- This migration deliberately separates model-probability availability from
-- full governed publication. A PROSPECTIVE_CERTIFIED MLB artifact may support
-- a genuine fitted/contextual model probability, but its strict terminal
-- ceiling remains MODEL_QUALIFIED_HOLD. CHAMPION/ratification rules remain
-- unchanged for higher publication states. can_execute remains false.

alter table public.wow_mlb_event_fitted_model_artifacts
  add column if not exists specialist_calibration_identity jsonb not null default '{}'::jsonb;

alter table public.wow_mlb_event_fitted_model_artifacts
  drop constraint if exists wow_mlb_event_fitted_certified_requirements;

alter table public.wow_mlb_event_fitted_model_artifacts
  add constraint wow_mlb_event_fitted_certified_requirements check (
    lifecycle_state not in ('PROSPECTIVE_CERTIFIED','CHAMPION')
    or (
      active = true
      and promoted = true
      and certification_id is not null
      and length(trim(certification_id)) > 0
      and promoted_at is not null
      and (
        calibrator_id is not null
        or (
          jsonb_typeof(specialist_calibration_identity) = 'object'
          and specialist_calibration_identity <> '{}'::jsonb
        )
      )
    )
  );

create table if not exists public.wow_mlb_event_specialist_snapshots (
  specialist_snapshot_id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  research_run_id text not null,
  event_key text not null,
  official_event_id text not null,
  source_snapshot_id uuid not null,
  base_score_snapshot_id uuid not null references public.wow_mlb_forward_score_snapshots(score_snapshot_id),
  artifact_id uuid not null references public.wow_mlb_event_fitted_model_artifacts(artifact_id),
  model_version text not null,
  model_inputs_hash text not null,
  model_timestamp timestamptz not null,
  latest_material_update_timestamp timestamptz not null,
  simulation_seed bigint not null,
  simulation_count integer not null,
  raw_home_probability numeric not null,
  raw_away_probability numeric not null,
  calibrated_home_probability numeric not null,
  calibrated_away_probability numeric not null,
  calibrated_home_lower_bound numeric not null,
  calibrated_home_upper_bound numeric not null,
  calibrated_away_lower_bound numeric not null,
  calibrated_away_upper_bound numeric not null,
  favorite_failure_paths_json jsonb not null,
  underdog_upset_path_json jsonb not null,
  context_evidence_json jsonb not null,
  output_json jsonb not null,
  terminal_ceiling text not null default 'MODEL_QUALIFIED_HOLD',
  model_probability_publishable boolean not null default true,
  probability_publishable boolean not null default false,
  can_execute boolean not null default false,
  constraint wow_mlb_event_specialist_simulation_min check (simulation_count >= 50000),
  constraint wow_mlb_event_specialist_raw_home_domain check (raw_home_probability > 0 and raw_home_probability < 1),
  constraint wow_mlb_event_specialist_raw_away_domain check (raw_away_probability > 0 and raw_away_probability < 1),
  constraint wow_mlb_event_specialist_cal_home_domain check (calibrated_home_probability > 0 and calibrated_home_probability < 1),
  constraint wow_mlb_event_specialist_cal_away_domain check (calibrated_away_probability > 0 and calibrated_away_probability < 1),
  constraint wow_mlb_event_specialist_raw_reconcile check (abs((raw_home_probability + raw_away_probability) - 1.0) < 0.000001),
  constraint wow_mlb_event_specialist_cal_reconcile check (abs((calibrated_home_probability + calibrated_away_probability) - 1.0) < 0.000001),
  constraint wow_mlb_event_specialist_home_bounds check (calibrated_home_lower_bound < calibrated_home_probability and calibrated_home_probability < calibrated_home_upper_bound),
  constraint wow_mlb_event_specialist_away_bounds check (calibrated_away_lower_bound < calibrated_away_probability and calibrated_away_probability < calibrated_away_upper_bound),
  constraint wow_mlb_event_specialist_hold_ceiling check (terminal_ceiling = 'MODEL_QUALIFIED_HOLD'),
  constraint wow_mlb_event_specialist_model_probability_allowed check (model_probability_publishable = true),
  constraint wow_mlb_event_specialist_full_publication_off check (probability_publishable = false),
  constraint wow_mlb_event_specialist_never_execute check (can_execute = false),
  constraint wow_mlb_event_specialist_input_hash_shape check (model_inputs_hash ~ '^[0-9a-f]{64}$')
);

alter table public.wow_mlb_event_specialist_snapshots enable row level security;
create index if not exists wow_mlb_event_specialist_event_lookup
  on public.wow_mlb_event_specialist_snapshots(official_event_id, created_at desc);

create or replace function public.wow_mlb_event_certified_model_artifact(
  p_feature_schema_version text
) returns jsonb
language plpgsql
stable
security invoker
set search_path = public
as $$
declare
  a public.wow_mlb_event_fitted_model_artifacts%rowtype;
begin
  select * into a
  from public.wow_mlb_event_fitted_model_artifacts
  where feature_schema_version = p_feature_schema_version
    and active = true
    and promoted = true
    and lifecycle_state in ('PROSPECTIVE_CERTIFIED','CHAMPION')
    and certification_id is not null
    and (
      calibrator_id is not null
      or (
        jsonb_typeof(specialist_calibration_identity) = 'object'
        and specialist_calibration_identity <> '{}'::jsonb
      )
    )
  order by case when lifecycle_state = 'CHAMPION' then 0 else 1 end,
           promoted_at desc nulls last, created_at desc
  limit 1;

  if not found then
    return jsonb_build_object(
      'ok', false,
      'code', 'MLB_EVENT_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND',
      'provider_identity', 'WOW_MLB_EVENT_FITTED_MODEL_V1',
      'feature_schema_version', p_feature_schema_version,
      'probability_publishable', false,
      'can_execute', false
    );
  end if;

  return jsonb_build_object(
    'ok', true,
    'code', 'MLB_EVENT_CERTIFIED_MODEL_ARTIFACT_READY',
    'artifact_id', a.artifact_id,
    'provider_identity', a.provider_identity,
    'model_family', a.model_family,
    'model_artifact_version', a.model_artifact_version,
    'artifact_format', a.artifact_format,
    'artifact_payload', a.artifact_payload,
    'artifact_checksum', a.artifact_checksum,
    'bundle_fingerprint', a.bundle_fingerprint,
    'feature_schema_version', a.feature_schema_version,
    'feature_transform_version', a.feature_transform_version,
    'training_code_sha', a.training_code_sha,
    'training_dataset_hash', a.training_dataset_hash,
    'training_rows', a.training_rows,
    'validation_metrics', a.validation_metrics,
    'calibrator_id', a.calibrator_id,
    'specialist_calibration_identity', a.specialist_calibration_identity,
    'certification_id', a.certification_id,
    'lifecycle_state', a.lifecycle_state,
    'model_probability_publishable', a.lifecycle_state in ('PROSPECTIVE_CERTIFIED','CHAMPION'),
    'probability_publishable', false,
    'terminal_ceiling', case when a.lifecycle_state='PROSPECTIVE_CERTIFIED' then 'MODEL_QUALIFIED_HOLD' else null end,
    'can_execute', false
  );
end;
$$;

revoke all on function public.wow_mlb_event_certified_model_artifact(text) from anon, authenticated;

-- Seed the completed contextual specialist as PROSPECTIVE_CERTIFIED only.
-- It intentionally does not create or imply CHAMPION status.
with fs as (
  select * from public.wow_mlb_v2d_frozen_spec
  where status='RESEARCH_FROZEN'
  order by created_at desc limit 1
), cal as (
  select c.* from public.wow_mlb_v2d_intercept_calibration c
  join fs on fs.calibration_id=c.calibration_id
), val as (
  select v.* from public.wow_mlb_v2d_validation_ledger v
  join fs on fs.spec_id=v.spec_id
  order by v.created_at desc limit 1
), health as (
  select h.* from public.wow_mlb_v2d_calibration_health h
  join fs on fs.spec_id=h.spec_id
), bundle as (
  select
    fs.*,
    cal.method as cal_method,
    cal.prior_games,
    cal.intercept_shift,
    val.n as validation_n,
    val.model_brier,
    val.baseline_brier,
    val.paired_mean_diff,
    val.paired_ci95_low,
    val.paired_ci95_high,
    val.status as validation_status,
    health.graded_shadow_n,
    health.pending_shadow_n,
    health.calibration_health_status,
    jsonb_build_object(
      'baseline_spec_id',fs.spec_id,
      'baseline_spec_version',fs.spec_version,
      'baseline_model_version',fs.config_json->>'model_version',
      'context_adapter_version','MLB_V16_V2D_CONTEXT_SHARED_SIM_R1',
      'lineup_model_version','MLB_LINEUP_PLATOON_SHRINK_V1',
      'weather_model_version','MLB_OFFICIAL_FEED_WEATHER_V1',
      'failure_model_version','MLB_FAILURE_REGIME_MIXTURE_V1',
      'minimum_simulations',50000,
      'market_prior_weight',0.0,
      'terminal_ceiling','MODEL_QUALIFIED_HOLD',
      'can_execute',false
    ) as payload
  from fs join cal on true join val on true join health on true
)
insert into public.wow_mlb_event_fitted_model_artifacts(
  provider_identity,model_family,model_artifact_version,artifact_format,
  artifact_payload,artifact_checksum,bundle_fingerprint,
  feature_schema_version,feature_transform_version,training_code_sha,
  training_dataset_hash,training_rows,validation_metrics,
  calibrator_id,specialist_calibration_identity,certification_id,
  lifecycle_state,active,promoted,probability_publishable,can_execute,promoted_at
)
select
  'WOW_MLB_EVENT_FITTED_MODEL_V1',
  'V2D_SHARED_NB_PLUS_CONTEXT_REGIMES',
  'MLB_V16_V2D_CONTEXT_SHARED_SIM_R1',
  'JSON_REFERENCE_BUNDLE_V1',
  payload,
  encode(extensions.digest(convert_to(payload::text,'UTF8'),'sha256'),'hex'),
  encode(extensions.digest(convert_to(concat_ws('|',spec_sha256,training_data_sha256,'40525c26543b6df96006434af58d6acfe9aa47a7',calibration_id::text,'MLB_V16_V2D_CONTEXT_SHARED_SIM_R1'),'UTF8'),'sha256'),'hex'),
  'MLB_V2D_CONTEXT_V1',
  'V2D_FEATURES_PLUS_CONFIRMED_LINEUP_PLATOON_WEATHER_REGIMES_V1',
  '40525c26543b6df96006434af58d6acfe9aa47a7',
  training_data_sha256,
  (config_json->>'training_rows')::integer,
  jsonb_build_object(
    'retrospective_n',validation_n,
    'model_brier',model_brier,
    'baseline_brier',baseline_brier,
    'paired_mean_diff',paired_mean_diff,
    'paired_ci95_low',paired_ci95_low,
    'paired_ci95_high',paired_ci95_high,
    'retrospective_status',validation_status,
    'graded_forward_shadow_n',graded_shadow_n,
    'pending_forward_shadow_n',pending_shadow_n,
    'calibration_health_status',calibration_health_status,
    'certification_scope','PROSPECTIVE_MODEL_PROBABILITY_ONLY'
  ),
  null,
  jsonb_build_object(
    'source_table','wow_mlb_v2d_intercept_calibration',
    'calibration_id',calibration_id,
    'method',cal_method,
    'prior_games',prior_games,
    'intercept_shift',intercept_shift
  ),
  'V16-PROSPECTIVE-20260831-MLB-EVENT-R1',
  'PROSPECTIVE_CERTIFIED',true,true,false,false,now()
from bundle
on conflict (model_artifact_version) do update set
  artifact_payload=excluded.artifact_payload,
  artifact_checksum=excluded.artifact_checksum,
  bundle_fingerprint=excluded.bundle_fingerprint,
  validation_metrics=excluded.validation_metrics,
  specialist_calibration_identity=excluded.specialist_calibration_identity,
  certification_id=excluded.certification_id,
  lifecycle_state='PROSPECTIVE_CERTIFIED',
  active=true,
  promoted=true,
  probability_publishable=false,
  can_execute=false,
  promoted_at=now();

create or replace function public.wow_mlb_prospective_model_state()
returns jsonb
language sql
stable
set search_path to ''
as $$
with artifact as (
  select a.*
  from public.wow_mlb_event_fitted_model_artifacts a
  where a.feature_schema_version='MLB_V2D_CONTEXT_V1'
    and a.active=true and a.promoted=true
    and a.lifecycle_state in ('PROSPECTIVE_CERTIFIED','CHAMPION')
  order by case when a.lifecycle_state='CHAMPION' then 0 else 1 end, a.promoted_at desc
  limit 1
), health as (
  select h.* from public.wow_mlb_v2d_calibration_health h
  order by h.assessed_at desc limit 1
)
select jsonb_build_object(
  'model_probability_capability',case when a.artifact_id is not null and h.calibration_health_status='PASS' then 'AVAILABLE' else 'UNAVAILABLE' end,
  'artifact_id',a.artifact_id,
  'lifecycle_state',a.lifecycle_state,
  'certification_id',a.certification_id,
  'calibration_health_status',coalesce(h.calibration_health_status,'UNAVAILABLE'),
  'graded_forward_shadow_n',coalesce(h.graded_shadow_n,0),
  'pending_forward_shadow_n',coalesce(h.pending_shadow_n,0),
  'terminal_ceiling',case when a.lifecycle_state='PROSPECTIVE_CERTIFIED' then 'MODEL_QUALIFIED_HOLD' else null end,
  'model_probability_publishable',coalesce(a.lifecycle_state in ('PROSPECTIVE_CERTIFIED','CHAMPION'),false) and coalesce(h.calibration_health_status='PASS',false),
  'probability_publishable',false,
  'can_execute',false
)
from artifact a full join health h on true;
$$;
