-- WOW v16 Clean Core — governed MLB event fitted-model artifact registry.
--
-- This registry is intentionally distinct from wow_mlb_research_model_artifacts
-- and from the RESEARCH_FROZEN V2D forward-shadow spec. Research/shadow success
-- is evidence for certification; it is not itself a production artifact.
--
-- This migration seeds NO artifact and changes NO runtime capability. An MLB
-- event model may be returned by the certified-artifact RPC only after an
-- explicit certification row is active, promoted, linked to a calibrator, and
-- in a certified lifecycle state. can_execute is permanently false.

create table if not exists public.wow_mlb_event_fitted_model_artifacts (
    artifact_id uuid primary key default gen_random_uuid(),
    provider_identity text not null default 'WOW_MLB_EVENT_FITTED_MODEL_V1',
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
    constraint wow_mlb_event_fitted_provider_identity check (
        provider_identity = 'WOW_MLB_EVENT_FITTED_MODEL_V1'
    ),
    constraint wow_mlb_event_fitted_lifecycle check (
        lifecycle_state in ('CANDIDATE','PROSPECTIVE_CERTIFIED','CHAMPION','RETIRED','BLOCKED')
    ),
    constraint wow_mlb_event_fitted_training_rows_positive check (training_rows > 0),
    constraint wow_mlb_event_fitted_payload_object check (jsonb_typeof(artifact_payload) = 'object'),
    constraint wow_mlb_event_fitted_validation_object check (jsonb_typeof(validation_metrics) = 'object'),
    constraint wow_mlb_event_fitted_checksum_shape check (artifact_checksum ~ '^[0-9a-f]{64}$'),
    constraint wow_mlb_event_fitted_bundle_shape check (bundle_fingerprint ~ '^[0-9a-f]{64}$'),
    constraint wow_mlb_event_fitted_code_sha_shape check (training_code_sha ~ '^[0-9a-f]{40,64}$'),
    constraint wow_mlb_event_fitted_dataset_hash_shape check (training_dataset_hash ~ '^[0-9a-f]{64}$'),
    constraint wow_mlb_event_fitted_never_execute check (can_execute = false),
    constraint wow_mlb_event_fitted_registry_not_probability_publication check (probability_publishable = false),
    constraint wow_mlb_event_fitted_certified_requirements check (
        lifecycle_state not in ('PROSPECTIVE_CERTIFIED','CHAMPION')
        or (
            active = true
            and promoted = true
            and calibrator_id is not null
            and certification_id is not null
            and length(trim(certification_id)) > 0
            and promoted_at is not null
        )
    )
);

alter table public.wow_mlb_event_fitted_model_artifacts enable row level security;

create unique index if not exists wow_mlb_event_fitted_one_active_champion
    on public.wow_mlb_event_fitted_model_artifacts (feature_schema_version)
    where active = true and lifecycle_state = 'CHAMPION';

create index if not exists wow_mlb_event_fitted_route_lookup
    on public.wow_mlb_event_fitted_model_artifacts (
        feature_schema_version, lifecycle_state, active, promoted, created_at desc
    );

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
      and calibrator_id is not null
      and certification_id is not null
    order by
      case when lifecycle_state = 'CHAMPION' then 0 else 1 end,
      promoted_at desc nulls last,
      created_at desc
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
        'certification_id', a.certification_id,
        'lifecycle_state', a.lifecycle_state,
        'probability_publishable', false,
        'can_execute', false
    );
end;
$$;

revoke all on function public.wow_mlb_event_certified_model_artifact(text)
from anon, authenticated;
