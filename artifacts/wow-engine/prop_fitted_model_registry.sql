-- WOW_PROP_FITTED_MODEL_V1 governed artifact registry.
-- This registry is metadata/control-plane only. It never manufactures a model,
-- probability, calibration state, or execution authority.

create table if not exists public.wow_prop_fitted_model_artifacts (
    artifact_id uuid primary key default gen_random_uuid(),
    created_at timestamptz not null default now(),
    provider_identity text not null default 'WOW_PROP_FITTED_MODEL_V1',
    model_family text not null,
    model_artifact_version text not null,
    calibrator_version text not null,
    sport text not null,
    stat_type text not null,
    feature_schema_version text not null,
    feature_transform_version text not null,
    specialist_version text not null,
    certification_id text not null,
    lifecycle_state text not null,
    training_dataset_hash text not null,
    training_code_sha text not null,
    artifact_checksum text not null,
    artifact_format text not null,
    artifact_payload jsonb not null,
    supported_line_min numeric not null,
    supported_line_max numeric not null,
    training_rows integer not null,
    validation_metrics jsonb not null default '{}'::jsonb,
    promoted boolean not null default false,
    active boolean not null default false,
    probability_publishable boolean not null default false,
    can_execute boolean not null default false,
    constraint wow_prop_fitted_provider_identity check (provider_identity = 'WOW_PROP_FITTED_MODEL_V1'),
    constraint wow_prop_fitted_lifecycle check (lifecycle_state in ('CANDIDATE','SHADOW','PROSPECTIVE_CERTIFIED','CHAMPION','RETIRED')),
    constraint wow_prop_fitted_line_range check (supported_line_min >= 0 and supported_line_max >= supported_line_min),
    constraint wow_prop_fitted_training_rows check (training_rows > 0),
    constraint wow_prop_fitted_payload_object check (jsonb_typeof(artifact_payload) = 'object'),
    constraint wow_prop_fitted_metrics_object check (jsonb_typeof(validation_metrics) = 'object'),
    constraint wow_prop_fitted_never_publish check (probability_publishable = false),
    constraint wow_prop_fitted_never_execute check (can_execute = false)
);

-- Exactly one active artifact can govern a sport/stat/schema tuple.
create unique index if not exists uq_wow_prop_fitted_active_route
    on public.wow_prop_fitted_model_artifacts (upper(sport), upper(stat_type), feature_schema_version)
    where active;

create unique index if not exists uq_wow_prop_fitted_artifact_version
    on public.wow_prop_fitted_model_artifacts (provider_identity, model_artifact_version);

create index if not exists idx_wow_prop_fitted_route
    on public.wow_prop_fitted_model_artifacts (sport, stat_type, feature_schema_version, active, promoted, created_at desc);

alter table public.wow_prop_fitted_model_artifacts enable row level security;
revoke all on table public.wow_prop_fitted_model_artifacts from anon, authenticated;
grant all on table public.wow_prop_fitted_model_artifacts to service_role;

create or replace function public.wow_prop_certified_model_artifact(
    p_sport text,
    p_stat_type text,
    p_feature_schema_version text
) returns jsonb
language plpgsql
stable
set search_path = public
as $$
declare
    v_row public.wow_prop_fitted_model_artifacts%rowtype;
begin
    select * into v_row
      from public.wow_prop_fitted_model_artifacts
     where upper(sport) = upper(p_sport)
       and upper(stat_type) = upper(p_stat_type)
       and feature_schema_version = p_feature_schema_version
       and active = true
       and promoted = true
       and lifecycle_state in ('PROSPECTIVE_CERTIFIED','CHAMPION')
     order by case when lifecycle_state = 'CHAMPION' then 0 else 1 end, created_at desc
     limit 1;

    if not found then
        return jsonb_build_object(
            'ok', false,
            'code', 'PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND',
            'provider_identity', 'WOW_PROP_FITTED_MODEL_V1',
            'sport', p_sport,
            'stat_type', p_stat_type,
            'feature_schema_version', p_feature_schema_version,
            'probability_publishable', false,
            'can_execute', false
        );
    end if;

    return jsonb_build_object(
        'ok', true,
        'code', 'PROP_CERTIFIED_MODEL_ARTIFACT_READY',
        'artifact_id', v_row.artifact_id,
        'provider_identity', v_row.provider_identity,
        'model_family', v_row.model_family,
        'model_artifact_version', v_row.model_artifact_version,
        'calibrator_version', v_row.calibrator_version,
        'sport', v_row.sport,
        'stat_type', v_row.stat_type,
        'feature_schema_version', v_row.feature_schema_version,
        'feature_transform_version', v_row.feature_transform_version,
        'specialist_version', v_row.specialist_version,
        'certification_id', v_row.certification_id,
        'lifecycle_state', v_row.lifecycle_state,
        'training_dataset_hash', v_row.training_dataset_hash,
        'training_code_sha', v_row.training_code_sha,
        'artifact_checksum', v_row.artifact_checksum,
        'artifact_format', v_row.artifact_format,
        'artifact_payload', v_row.artifact_payload,
        'supported_line_min', v_row.supported_line_min,
        'supported_line_max', v_row.supported_line_max,
        'training_rows', v_row.training_rows,
        'validation_metrics', v_row.validation_metrics,
        'probability_publishable', false,
        'can_execute', false
    );
end;
$$;

revoke all on function public.wow_prop_certified_model_artifact(text,text,text) from public, anon, authenticated;
grant execute on function public.wow_prop_certified_model_artifact(text,text,text) to service_role;

-- Do not flip PROP_PROBABILITY to AVAILABLE here. A registry is not a trained
-- artifact, and a trained artifact is not a certified end-to-end path.
update public.wow_runtime_capabilities
   set capability_status = 'UNAVAILABLE',
       evidence = jsonb_build_object(
           'reason', 'NO_CERTIFIED_PROP_MODEL_ARTIFACT',
           'provider_identity', 'WOW_PROP_FITTED_MODEL_V1',
           'artifact_registry', 'wow_prop_fitted_model_artifacts',
           'evidence_snapshot_contract', 'PROP_EVIDENCE_V1',
           'distribution_contract', 'DISCRETE_PMF',
           'llp_player_props_allowed', false
       ),
       can_execute = false,
       updated_at = now()
 where capability_key = 'PROP_PROBABILITY';
