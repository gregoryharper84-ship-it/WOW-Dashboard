-- WOW_PROP_FITTED_MODEL_V1 governed artifact registry.
-- This registry is metadata/control-plane only. It never manufactures a model,
-- probability, calibration state, or execution authority.
--
-- V17 exact capability identity is:
--   sport + league + market_family + stat_type + period
-- feature_schema_version is immutable artifact compatibility metadata, not part
-- of the capability identity.

create table if not exists public.wow_prop_fitted_model_artifacts (
    artifact_id uuid primary key default gen_random_uuid(),
    created_at timestamptz not null default now(),
    provider_identity text not null default 'WOW_PROP_FITTED_MODEL_V1',
    model_family text not null,
    model_artifact_version text not null,
    calibrator_version text not null,
    sport text not null,
    league text not null,
    market_family text not null,
    stat_type text not null,
    period text not null,
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

-- Forward migration for registries created before exact V17 route scoping.
alter table public.wow_prop_fitted_model_artifacts
    add column if not exists league text,
    add column if not exists market_family text,
    add column if not exists period text;

-- Existing certified prop families are player props. Current MLB rows use MLB as
-- both sport and league. Period is derivable from immutable stat identity.
-- Preserve all first-inning spellings already used by certified MLB routes.
update public.wow_prop_fitted_model_artifacts
   set league = coalesce(nullif(trim(league), ''), upper(trim(sport))),
       market_family = coalesce(nullif(trim(market_family), ''), 'PLAYER_PROP'),
       period = coalesce(
           nullif(trim(period), ''),
           case
               when upper(stat_type) like '%1IP%'
                 or upper(stat_type) like '%FIRST_INNING%'
                 or upper(stat_type) like '%1ST_INNING%'
                   then 'FIRST_INNING'
               else 'FULL_GAME'
           end
       )
 where league is null or trim(league) = ''
    or market_family is null or trim(market_family) = ''
    or period is null or trim(period) = '';

alter table public.wow_prop_fitted_model_artifacts
    alter column league set not null,
    alter column market_family set not null,
    alter column period set not null;

-- The old sport/stat/schema uniqueness rule was too coarse and could let one
-- supported family imply readiness outside its exact market/period scope.
drop index if exists public.uq_wow_prop_fitted_active_route;

-- Exactly one active artifact can govern one normalized V17 capability tuple.
-- feature_schema_version is deliberately excluded from identity.
create unique index if not exists uq_wow_prop_fitted_active_route_v17
    on public.wow_prop_fitted_model_artifacts (
        upper(trim(sport)),
        upper(trim(league)),
        upper(trim(market_family)),
        upper(trim(stat_type)),
        upper(trim(period))
    )
    where active;

create unique index if not exists uq_wow_prop_fitted_artifact_version
    on public.wow_prop_fitted_model_artifacts (provider_identity, model_artifact_version);

drop index if exists public.idx_wow_prop_fitted_route;
create index if not exists idx_wow_prop_fitted_route_v17
    on public.wow_prop_fitted_model_artifacts (
        sport, league, market_family, stat_type, period,
        feature_schema_version, active, promoted, created_at desc
    );

alter table public.wow_prop_fitted_model_artifacts enable row level security;
revoke all on table public.wow_prop_fitted_model_artifacts from anon, authenticated;
grant all on table public.wow_prop_fitted_model_artifacts to service_role;

-- Exact V17 artifact resolver. It first resolves the canonical capability tuple,
-- then separately verifies artifact feature-schema compatibility.
create or replace function public.wow_prop_certified_model_artifact_v2(
    p_sport text,
    p_league text,
    p_market_family text,
    p_stat_type text,
    p_period text,
    p_feature_schema_version text
) returns jsonb
language plpgsql
stable
set search_path = public
as $$
declare
    v_sport text := upper(trim(coalesce(p_sport, '')));
    v_league text := upper(trim(coalesce(p_league, '')));
    v_market_family text := upper(trim(coalesce(p_market_family, '')));
    v_stat_type text := upper(trim(coalesce(p_stat_type, '')));
    v_period text := upper(trim(coalesce(p_period, '')));
    v_count integer;
    v_row public.wow_prop_fitted_model_artifacts%rowtype;
begin
    if v_sport = '' or v_league = '' or v_market_family = '' or v_stat_type = '' or v_period = '' then
        return jsonb_build_object(
            'ok', false,
            'code', 'PROP_CAPABILITY_IDENTITY_INCOMPLETE',
            'sport', v_sport,
            'league', v_league,
            'market_family', v_market_family,
            'stat_type', v_stat_type,
            'period', v_period,
            'feature_schema_version', p_feature_schema_version,
            'specialist_selected', false,
            'specialist_invoked', false,
            'probability_publishable', false,
            'can_execute', false
        );
    end if;

    select count(*)
      into v_count
      from public.wow_prop_fitted_model_artifacts
     where upper(trim(sport)) = v_sport
       and upper(trim(league)) = v_league
       and upper(trim(market_family)) = v_market_family
       and upper(trim(stat_type)) = v_stat_type
       and upper(trim(period)) = v_period
       and active = true
       and promoted = true
       and lifecycle_state in ('PROSPECTIVE_CERTIFIED','CHAMPION');

    if v_count = 0 then
        return jsonb_build_object(
            'ok', false,
            'code', 'PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND',
            'provider_identity', 'WOW_PROP_FITTED_MODEL_V1',
            'sport', v_sport,
            'league', v_league,
            'market_family', v_market_family,
            'stat_type', v_stat_type,
            'period', v_period,
            'feature_schema_version', p_feature_schema_version,
            'specialist_selected', false,
            'specialist_invoked', false,
            'blocking_scope', 'CAPABILITY',
            'probability_publishable', false,
            'can_execute', false
        );
    elsif v_count > 1 then
        return jsonb_build_object(
            'ok', false,
            'code', 'SPECIALIST_ROUTING_CONFLICT',
            'provider_identity', 'WOW_PROP_FITTED_MODEL_V1',
            'sport', v_sport,
            'league', v_league,
            'market_family', v_market_family,
            'stat_type', v_stat_type,
            'period', v_period,
            'candidate_count', v_count,
            'specialist_selected', false,
            'specialist_invoked', false,
            'blocking_scope', 'CAPABILITY',
            'probability_publishable', false,
            'can_execute', false
        );
    end if;

    select *
      into v_row
      from public.wow_prop_fitted_model_artifacts
     where upper(trim(sport)) = v_sport
       and upper(trim(league)) = v_league
       and upper(trim(market_family)) = v_market_family
       and upper(trim(stat_type)) = v_stat_type
       and upper(trim(period)) = v_period
       and active = true
       and promoted = true
       and lifecycle_state in ('PROSPECTIVE_CERTIFIED','CHAMPION')
     limit 1;

    if coalesce(p_feature_schema_version, '') = ''
       or v_row.feature_schema_version <> p_feature_schema_version then
        return jsonb_build_object(
            'ok', false,
            'code', 'PROP_FEATURE_SCHEMA_MISMATCH',
            'provider_identity', v_row.provider_identity,
            'artifact_id', v_row.artifact_id,
            'model_family', v_row.model_family,
            'sport', v_row.sport,
            'league', v_row.league,
            'market_family', v_row.market_family,
            'stat_type', v_row.stat_type,
            'period', v_row.period,
            'artifact_feature_schema_version', v_row.feature_schema_version,
            'requested_feature_schema_version', p_feature_schema_version,
            'specialist_selected', true,
            'specialist_invoked', false,
            'blocking_scope', 'EVIDENCE',
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
        'league', v_row.league,
        'market_family', v_row.market_family,
        'stat_type', v_row.stat_type,
        'period', v_row.period,
        'feature_schema_version', v_row.feature_schema_version,
        'feature_transform_version', v_row.feature_transform_version,
        'specialist_version', v_row.specialist_version,
        'certification_id', v_row.certification_id,
        'lifecycle_state', v_row.lifecycle_state,
        'training_dataset_hash', v_row.training_dataset_hash,
        'training_code_sha', v_row.training_code_sha,
        'artifact_checksum', v_row.artifact_checksum,
        'artifact_sha256', v_row.artifact_checksum,
        'artifact_format', v_row.artifact_format,
        'artifact_payload', v_row.artifact_payload,
        'supported_line_min', v_row.supported_line_min,
        'supported_line_max', v_row.supported_line_max,
        'training_rows', v_row.training_rows,
        'validation_metrics', v_row.validation_metrics,
        'specialist_selected', true,
        'specialist_invoked', false,
        'blocking_scope', null,
        'probability_publishable', false,
        'can_execute', false
    );
end;
$$;

revoke all on function public.wow_prop_certified_model_artifact_v2(text,text,text,text,text,text)
    from public, anon, authenticated;
grant execute on function public.wow_prop_certified_model_artifact_v2(text,text,text,text,text,text)
    to service_role;

-- Backward-compatible resolver for existing runtime call sites. Its defaults are
-- server-owned and deterministic: league=sport, market_family=PLAYER_PROP, and
-- period derived from stat identity. New code should call the v2 resolver.
create or replace function public.wow_prop_certified_model_artifact(
    p_sport text,
    p_stat_type text,
    p_feature_schema_version text
) returns jsonb
language sql
stable
set search_path = public
as $$
    select public.wow_prop_certified_model_artifact_v2(
        p_sport,
        p_sport,
        'PLAYER_PROP',
        p_stat_type,
        case
            when upper(coalesce(p_stat_type, '')) like '%1IP%'
              or upper(coalesce(p_stat_type, '')) like '%FIRST_INNING%'
              or upper(coalesce(p_stat_type, '')) like '%1ST_INNING%'
                then 'FIRST_INNING'
            else 'FULL_GAME'
        end,
        p_feature_schema_version
    );
$$;

revoke all on function public.wow_prop_certified_model_artifact(text,text,text)
    from public, anon, authenticated;
grant execute on function public.wow_prop_certified_model_artifact(text,text,text)
    to service_role;

-- Exact registry truth for governance/health composition. Registry presence is
-- only one readiness signal; adapter_importable/scorer_resolvable/evidence
-- readiness remain runtime-owned and must not be manufactured here.
create or replace function public.wow_prop_registered_capabilities()
returns table (
    sport text,
    league text,
    market_family text,
    stat_type text,
    period text,
    provider_identity text,
    model_family text,
    artifact_id uuid,
    artifact_sha256 text,
    feature_schema_version text,
    calibrator_id text,
    lifecycle_state text,
    active boolean,
    promoted boolean,
    registered_capability boolean,
    probability_publishable boolean,
    can_execute boolean
)
language sql
stable
set search_path = public
as $$
    select
        upper(trim(a.sport)),
        upper(trim(a.league)),
        upper(trim(a.market_family)),
        upper(trim(a.stat_type)),
        upper(trim(a.period)),
        a.provider_identity,
        a.model_family,
        a.artifact_id,
        a.artifact_checksum,
        a.feature_schema_version,
        a.calibrator_version,
        a.lifecycle_state,
        a.active,
        a.promoted,
        (
            a.active
            and a.promoted
            and a.lifecycle_state in ('PROSPECTIVE_CERTIFIED','CHAMPION')
        ) as registered_capability,
        false as probability_publishable,
        false as can_execute
      from public.wow_prop_fitted_model_artifacts a
     where a.active = true;
$$;

revoke all on function public.wow_prop_registered_capabilities() from public, anon, authenticated;
grant execute on function public.wow_prop_registered_capabilities() to service_role;

-- Global PROP_PROBABILITY remains aggregate service health only. Preserve its
-- current status; annotate it so no caller may use it as exact NFL readiness.
update public.wow_runtime_capabilities
   set evidence = coalesce(evidence, '{}'::jsonb) || jsonb_build_object(
           'scope', 'AGGREGATE_SERVICE_HEALTH_ONLY',
           'exact_capability_required', true,
           'exact_capability_key', jsonb_build_array(
               'sport','league','market_family','stat_type','period'
           ),
           'exact_registry_function', 'wow_prop_registered_capabilities',
           'exact_resolver_function', 'wow_prop_certified_model_artifact_v2',
           'llp_player_props_allowed', false
       ),
       can_execute = false,
       updated_at = now()
 where capability_key = 'PROP_PROBABILITY';
