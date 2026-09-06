-- LLP-PATCH-2026-09-05-MLB-STARTER-SEQUENCING-DISPERSION
-- Additive backend contract only. It does not promote a scorer and does not
-- modify the current MLB champion. The MSD lane remains MODEL_UNAVAILABLE until
-- an exact fitted/certified artifact is active and promoted.

create table if not exists public.wow_mlb_msd_v17_contracts (
  patch_id text primary key,
  patch_version text not null,
  status text not null,
  model_family text not null,
  feature_schema_version text not null,
  calibration_contract_version text not null,
  active boolean not null default false,
  can_execute boolean not null default false check (can_execute = false),
  dry_run_only boolean not null default true,
  requirements jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.wow_mlb_msd_v17_required_features (
  patch_id text not null references public.wow_mlb_msd_v17_contracts(patch_id) on delete cascade,
  scope text not null,
  feature_name text not null,
  required boolean not null default true,
  max_age_seconds integer,
  primary key (patch_id, scope, feature_name)
);

create table if not exists public.wow_mlb_msd_v17_audit_snapshots (
  audit_id uuid primary key default gen_random_uuid(),
  patch_id text not null references public.wow_mlb_msd_v17_contracts(patch_id),
  research_run_id text,
  event_id text not null,
  selected_participant text,
  terminal_status text not null,
  missing_fields text[] not null default '{}',
  scorer_status text,
  starter_dispersion_model_version text,
  sequencing_model_version text,
  bullpen_model_version text,
  simulation_version text,
  model_version text,
  calibration_version text,
  source_snapshot_id text,
  raw_probability double precision,
  calibrated_probability double precision,
  lower_bound double precision,
  upper_bound double precision,
  p_starter_4plus double precision,
  p_starter_6plus double precision,
  p_three_plus_run_inning double precision,
  p_scoreless_first_5 double precision,
  catastrophic_failure_probability double precision,
  sequencing_concentration_index double precision,
  handoff_risk double precision,
  calibration_width double precision,
  feature_snapshot_timestamp timestamptz,
  participant_snapshot_timestamp timestamptz,
  market_leakage_detected boolean not null default false,
  package jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  check (raw_probability is null or raw_probability between 0 and 1),
  check (calibrated_probability is null or calibrated_probability between 0 and 1),
  check (lower_bound is null or lower_bound between 0 and 1),
  check (upper_bound is null or upper_bound between 0 and 1),
  check (calibration_width is null or calibration_width >= 0)
);

create table if not exists public.wow_mlb_msd_v17_regression_results (
  result_id uuid primary key default gen_random_uuid(),
  patch_id text not null references public.wow_mlb_msd_v17_contracts(patch_id),
  test_id text not null,
  status text not null check (status in ('PASS','FAIL','BLOCKED','NOT_RUN')),
  detail jsonb not null default '{}'::jsonb,
  commit_sha text,
  tested_at timestamptz not null default now()
);

alter table public.wow_mlb_msd_v17_contracts enable row level security;
alter table public.wow_mlb_msd_v17_required_features enable row level security;
alter table public.wow_mlb_msd_v17_audit_snapshots enable row level security;
alter table public.wow_mlb_msd_v17_regression_results enable row level security;

insert into public.wow_mlb_msd_v17_contracts(
  patch_id, patch_version, status, model_family, feature_schema_version,
  calibration_contract_version, active, can_execute, dry_run_only, requirements
) values (
  'LLP-PATCH-2026-09-05-MLB-STARTER-SEQUENCING-DISPERSION',
  'v17',
  'BACKEND_CONTRACT_INSTALLED_CHALLENGER_NOT_PROMOTED',
  'MLB_STARTER_SEQUENCING_DISPERSION_V17',
  'MLB_MSD_V17_FEATURES_V1',
  'MLB_MSD_V17_DYNAMIC_CALIBRATION_V1',
  false, false, true,
  jsonb_build_object(
    'ranking_metric','calibrated_lower_bound',
    'market_features_forbidden',true,
    'universal_haircut_forbidden',true,
    'minimum_simulations',50000,
    'promotion_metrics',jsonb_build_array(
      'brier_score','log_loss','calibration_slope_intercept','reliability_by_bucket',
      'p_starter_4plus_calibration','p_three_plus_run_inning_calibration',
      'favorite_upset_rate_by_catastrophic_risk_decile',
      'starter_quality_tier_calibration','late_change_performance'
    ),
    'can_execute',false,
    'dry_run_only_no_live_trading_no_market_orders',true
  )
) on conflict (patch_id) do update set
  status=excluded.status,
  model_family=excluded.model_family,
  feature_schema_version=excluded.feature_schema_version,
  calibration_contract_version=excluded.calibration_contract_version,
  active=false,
  can_execute=false,
  dry_run_only=true,
  requirements=excluded.requirements,
  updated_at=now();

insert into public.wow_mlb_msd_v17_required_features(patch_id, scope, feature_name, required, max_age_seconds)
select 'LLP-PATCH-2026-09-05-MLB-STARTER-SEQUENCING-DISPERSION', v.scope, v.feature_name, true, v.max_age_seconds
from (values
 ('STARTER','identity',21600),('STARTER','handedness',86400),('STARTER','confirmation_timestamp',21600),
 ('STARTER','expected_pitch_count_innings_distribution',21600),('STARTER','xwoba_allowed',86400),('STARTER','xslg_allowed',86400),
 ('STARTER','xba_allowed',86400),('STARTER','k_rate',86400),('STARTER','bb_rate',86400),('STARTER','whiff_rate',86400),
 ('STARTER','chase_rate',86400),('STARTER','putaway_rate',86400),('STARTER','barrel_rate',86400),('STARTER','hard_hit_rate',86400),
 ('STARTER','gb_rate',86400),('STARTER','fb_rate',86400),('STARTER','hr_contact_profile',86400),('STARTER','velocity_movement_flags',21600),
 ('STARTER','rest_workload',21600),('STARTER','times_through_order_splits',604800),
 ('OFFENSE','projected_batting_order',21600),('OFFENSE','batter_event_probabilities',21600),('OFFENSE','contact_quality_distribution',86400),
 ('OFFENSE','platoon_composition',21600),('OFFENSE','bench_late_substitution_availability',21600),
 ('CONTEXT','projected_lineup_handedness_availability',21600),('CONTEXT','park_weather_state',10800),
 ('CONTEXT','bullpen_availability_leverage_workload',10800)
) as v(scope,feature_name,max_age_seconds)
on conflict (patch_id, scope, feature_name) do update set required=true, max_age_seconds=excluded.max_age_seconds;

insert into public.wow_runtime_capabilities(capability_key, capability_status, updated_at, evidence, can_execute)
values (
 'MLB_EVENT_MSD_V17','UNAVAILABLE',now(),
 jsonb_build_object(
   'patch_id','LLP-PATCH-2026-09-05-MLB-STARTER-SEQUENCING-DISPERSION',
   'backend_contract_installed',true,'active',false,'exact_fitted_artifact_ready',false,
   'feature_schema_version','MLB_MSD_V17_FEATURES_V1',
   'terminal_label_if_selected_now','MODEL_UNAVAILABLE',
   'reason','CHALLENGER_FITTED_ARTIFACT_NOT_CERTIFIED',
   'current_champion_untouched',true,'can_execute',false
 ),false
)
on conflict (capability_key) do update set
 capability_status='UNAVAILABLE', updated_at=now(), evidence=excluded.evidence, can_execute=false;

create or replace function public.wow_mlb_msd_v17_contract_status()
returns jsonb language plpgsql stable as $$
declare
  v_contract public.wow_mlb_msd_v17_contracts%rowtype;
  v_artifact record;
begin
  select * into v_contract from public.wow_mlb_msd_v17_contracts
  where patch_id='LLP-PATCH-2026-09-05-MLB-STARTER-SEQUENCING-DISPERSION';

  select artifact_id, model_artifact_version, feature_schema_version, lifecycle_state, active, promoted
  into v_artifact
  from public.wow_mlb_event_fitted_model_artifacts
  where model_family='MLB_STARTER_SEQUENCING_DISPERSION_V17'
    and feature_schema_version='MLB_MSD_V17_FEATURES_V1'
    and active=true and promoted=true
    and lifecycle_state in ('PROSPECTIVE_CERTIFIED','CHAMPION')
  order by created_at desc limit 1;

  if not found then
    return jsonb_build_object(
      'patch_id',v_contract.patch_id,'integration_status',v_contract.status,
      'active',v_contract.active,'model_capability','MODEL_UNAVAILABLE',
      'reason','EXACT_FITTED_MSD_V17_ARTIFACT_NOT_CERTIFIED','can_execute',false
    );
  end if;

  return jsonb_build_object(
    'patch_id',v_contract.patch_id,'integration_status',v_contract.status,
    'active',v_contract.active,'model_capability','AVAILABLE',
    'artifact_id',v_artifact.artifact_id,'model_artifact_version',v_artifact.model_artifact_version,
    'feature_schema_version',v_artifact.feature_schema_version,
    'lifecycle_state',v_artifact.lifecycle_state,'can_execute',false
  );
end;
$$;
