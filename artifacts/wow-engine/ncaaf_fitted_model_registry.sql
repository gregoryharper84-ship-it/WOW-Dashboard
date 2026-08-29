-- WOW_NCAAF_FITTED_MODEL_V1 governed artifact registry.
-- Metadata/control plane only. Registry presence never implies a trained model,
-- calibration health, publishable probability, money approval, or execution.

create table if not exists public.wow_ncaaf_fitted_model_artifacts (
    artifact_id uuid primary key default gen_random_uuid(),
    created_at timestamptz not null default now(),
    provider_identity text not null default 'WOW_NCAAF_FITTED_MODEL_V1',
    model_family text not null,
    model_artifact_version text not null,
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
    training_rows integer not null,
    training_seasons integer[] not null,
    validation_start_date date not null,
    validation_end_date date not null,
    validation_metrics jsonb not null default '{}'::jsonb,
    calibration_method text,
    calibrator_version text,
    calibration_training_n integer,
    promoted boolean not null default false,
    active boolean not null default false,
    probability_publishable boolean not null default false,
    can_execute boolean not null default false,

    constraint wow_ncaaf_fitted_provider_identity
        check (provider_identity = 'WOW_NCAAF_FITTED_MODEL_V1'),
    constraint wow_ncaaf_fitted_lifecycle
        check (lifecycle_state in ('CANDIDATE','SHADOW','PROSPECTIVE_CERTIFIED','CHAMPION','RETIRED')),
    constraint wow_ncaaf_fitted_training_rows check (training_rows > 0),
    constraint wow_ncaaf_fitted_training_seasons check (cardinality(training_seasons) > 0),
    constraint wow_ncaaf_fitted_validation_dates check (validation_end_date >= validation_start_date),
    constraint wow_ncaaf_fitted_payload_object check (jsonb_typeof(artifact_payload) = 'object'),
    constraint wow_ncaaf_fitted_metrics_object check (jsonb_typeof(validation_metrics) = 'object'),
    constraint wow_ncaaf_fitted_publish_requires_promotion check (
        probability_publishable = false
        or (
            promoted = true
            and active = true
            and lifecycle_state in ('PROSPECTIVE_CERTIFIED','CHAMPION')
            and calibrator_version is not null
            and calibration_training_n is not null
            and calibration_training_n > 0
        )
    ),
    constraint wow_ncaaf_fitted_never_execute check (can_execute = false)
);

create unique index if not exists uq_wow_ncaaf_fitted_active_route
    on public.wow_ncaaf_fitted_model_artifacts (feature_schema_version)
    where active;

create unique index if not exists uq_wow_ncaaf_fitted_artifact_version
    on public.wow_ncaaf_fitted_model_artifacts (provider_identity, model_artifact_version);

alter table public.wow_ncaaf_fitted_model_artifacts enable row level security;
revoke all on table public.wow_ncaaf_fitted_model_artifacts from anon, authenticated;
grant all on table public.wow_ncaaf_fitted_model_artifacts to service_role;

create or replace function public.wow_ncaaf_certified_model_artifact(
    p_feature_schema_version text
) returns jsonb
language plpgsql
stable
security invoker
set search_path = public
as $$
declare
    v_row public.wow_ncaaf_fitted_model_artifacts%rowtype;
begin
    select * into v_row
      from public.wow_ncaaf_fitted_model_artifacts
     where feature_schema_version = p_feature_schema_version
       and active = true
       and promoted = true
       and probability_publishable = true
       and lifecycle_state in ('PROSPECTIVE_CERTIFIED','CHAMPION')
     order by case when lifecycle_state = 'CHAMPION' then 0 else 1 end, created_at desc
     limit 1;

    if not found then
        return jsonb_build_object(
            'ok', false,
            'code', 'NCAAF_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND',
            'provider_identity', 'WOW_NCAAF_FITTED_MODEL_V1',
            'feature_schema_version', p_feature_schema_version,
            'probability_publishable', false,
            'can_execute', false
        );
    end if;

    return jsonb_build_object(
        'ok', true,
        'code', 'NCAAF_CERTIFIED_MODEL_ARTIFACT_READY',
        'artifact_id', v_row.artifact_id,
        'provider_identity', v_row.provider_identity,
        'model_family', v_row.model_family,
        'model_artifact_version', v_row.model_artifact_version,
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
        'training_rows', v_row.training_rows,
        'training_seasons', v_row.training_seasons,
        'validation_start_date', v_row.validation_start_date,
        'validation_end_date', v_row.validation_end_date,
        'validation_metrics', v_row.validation_metrics,
        'calibration_method', v_row.calibration_method,
        'calibrator_version', v_row.calibrator_version,
        'calibration_training_n', v_row.calibration_training_n,
        'probability_publishable', v_row.probability_publishable,
        'can_execute', false
    );
end;
$$;

revoke all on function public.wow_ncaaf_certified_model_artifact(text) from public, anon, authenticated;
grant execute on function public.wow_ncaaf_certified_model_artifact(text) to service_role;

comment on table public.wow_ncaaf_fitted_model_artifacts is
  'Governed NCAAF fitted-model registry. No row may authorize execution; publication requires certified/promo/calibrator evidence.';
