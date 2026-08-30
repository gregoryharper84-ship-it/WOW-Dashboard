-- WOW v16 Clean Core — P0 calibration champion fallback.
-- 2026-08-30
--
-- Scope: separate serving-production health from challenger-promotion health.
-- An unfinished V2D challenger remains BLOCKED and its blocker remains visible,
-- but it must not invalidate an independently certified serving champion.
-- No champion is seeded by this migration. If no exact valid champion exists,
-- probability publication remains UNAVAILABLE.
--
-- Safety invariants:
--   can_execute=false
--   no market/Kalshi/no-vig substitution for a missing controlling model
--   no pregame fallback for LIVE requests
--   no downstream rewrite of FORWARD_SHADOW_NOT_COMPLETED to PASS

alter table public.wow_mlb_event_fitted_model_artifacts
    add column if not exists sport text not null default 'MLB',
    add column if not exists market_type text not null default 'MONEYLINE',
    add column if not exists settlement_basis text not null default 'FULL_GAME_OUTRIGHT',
    add column if not exists serving_mode text not null default 'PREGAME',
    add column if not exists state_schema_version text,
    add column if not exists governance_hash text,
    add column if not exists certification_expires_at timestamptz,
    add column if not exists revoked_at timestamptz;

alter table public.wow_mlb_event_fitted_model_artifacts
    drop constraint if exists wow_mlb_event_fitted_serving_mode;
alter table public.wow_mlb_event_fitted_model_artifacts
    add constraint wow_mlb_event_fitted_serving_mode
    check (serving_mode in ('PREGAME','LIVE'));

alter table public.wow_mlb_event_fitted_model_artifacts
    drop constraint if exists wow_mlb_event_fitted_live_state_schema;
alter table public.wow_mlb_event_fitted_model_artifacts
    add constraint wow_mlb_event_fitted_live_state_schema
    check (serving_mode <> 'LIVE' or state_schema_version is not null);

create index if not exists wow_mlb_event_fitted_serving_lookup
    on public.wow_mlb_event_fitted_model_artifacts (
        model_family,
        sport,
        market_type,
        settlement_basis,
        serving_mode,
        feature_schema_version,
        lifecycle_state,
        active,
        promoted,
        promoted_at desc
    );

-- Exact serving resolver used by Stage 0.5. It deliberately resolves the
-- serving version before checking serving calibration health. Challenger health
-- is audit evidence only unless that challenger is itself fully certified and
-- atomically promoted as CHAMPION by a separate certification path.
create or replace function public.wow_mlb_resolve_serving_probability_model(
    p_model_family text,
    p_feature_schema_version text,
    p_market_type text default 'MONEYLINE',
    p_settlement_basis text default 'FULL_GAME_OUTRIGHT',
    p_requested_mode text default 'PREGAME',
    p_model_timestamp timestamptz default null,
    p_latest_material_update_at timestamptz default null
) returns jsonb
language plpgsql
stable
security invoker
set search_path = public
as $$
declare
    h public.wow_mlb_v2d_calibration_health%rowtype;
    a public.wow_mlb_event_fitted_model_artifacts%rowtype;
    c public.wow_calibrators%rowtype;
    v_challenger_status text := 'NOT_PRESENT';
    v_challenger_blockers text[] := '{}';
    v_service_status text;
begin
    if p_model_family is null or btrim(p_model_family) = '' then
        return jsonb_build_object(
            'service_status','UNAVAILABLE',
            'blockers',jsonb_build_array('MODEL_FAMILY_UNRESOLVED'),
            'probability_publishable',false,
            'can_execute',false
        );
    end if;

    if p_feature_schema_version is null or btrim(p_feature_schema_version) = '' then
        return jsonb_build_object(
            'service_status','UNAVAILABLE',
            'blockers',jsonb_build_array('FEATURE_SCHEMA_VERSION_UNRESOLVED'),
            'probability_publishable',false,
            'can_execute',false
        );
    end if;

    if p_requested_mode not in ('PREGAME','LIVE') then
        return jsonb_build_object(
            'service_status','UNAVAILABLE',
            'blockers',jsonb_build_array('REQUESTED_MODE_UNSUPPORTED'),
            'probability_publishable',false,
            'can_execute',false
        );
    end if;

    select * into h
    from public.wow_mlb_v2d_calibration_health
    order by assessed_at desc
    limit 1;

    if found then
        v_challenger_blockers := coalesce(h.blockers, '{}'::text[]);
        if h.calibration_health_status = 'PASS'
           and h.forward_shadow_status = 'SUFFICIENT_FOR_REVIEW' then
            v_challenger_status := 'PROMOTION_REVIEW_ELIGIBLE';
        else
            v_challenger_status := 'BLOCKED';
        end if;
    end if;

    -- Stale probability packets are never rescued by a serving resolver.
    -- If scoring predates a material update, the caller must rerun.
    if p_model_timestamp is not null
       and p_latest_material_update_at is not null
       and p_model_timestamp < p_latest_material_update_at then
        return jsonb_build_object(
            'service_status','UNAVAILABLE',
            'challenger_status',v_challenger_status,
            'challenger_blockers',to_jsonb(v_challenger_blockers),
            'blockers',jsonb_build_array('STALE_MODEL_RESULT_RERUN_REQUIRED'),
            'probability_publishable',false,
            'can_execute',false
        );
    end if;

    -- Fail closed: exact matching dimensions only. A PRE-GAME champion cannot
    -- satisfy a LIVE request because serving_mode must equal p_requested_mode;
    -- LIVE artifacts additionally require a state_schema_version by constraint.
    select * into a
    from public.wow_mlb_event_fitted_model_artifacts x
    where x.provider_identity = 'WOW_MLB_EVENT_FITTED_MODEL_V1'
      and x.model_family = p_model_family
      and x.sport = 'MLB'
      and x.market_type = p_market_type
      and x.settlement_basis = p_settlement_basis
      and x.serving_mode = p_requested_mode
      and x.feature_schema_version = p_feature_schema_version
      and x.lifecycle_state = 'CHAMPION'
      and x.active = true
      and x.promoted = true
      and x.calibrator_id is not null
      and x.certification_id is not null
      and btrim(x.certification_id) <> ''
      and x.governance_hash is not null
      and btrim(x.governance_hash) <> ''
      and x.revoked_at is null
      and x.retired_at is null
      and (x.certification_expires_at is null or x.certification_expires_at > clock_timestamp())
      and (x.serving_mode <> 'LIVE' or x.state_schema_version is not null)
    order by x.promoted_at desc nulls last, x.created_at desc
    limit 1;

    if not found then
        return jsonb_build_object(
            'service_status','UNAVAILABLE',
            'challenger_status',v_challenger_status,
            'challenger_blockers',to_jsonb(v_challenger_blockers),
            'blockers',case
                when p_requested_mode = 'LIVE'
                    then jsonb_build_array('NO_VALID_CERTIFIED_LIVE_CHAMPION')
                else jsonb_build_array('NO_VALID_CERTIFIED_CHAMPION')
            end,
            'probability_publishable',false,
            'can_execute',false
        );
    end if;

    select * into c
    from public.wow_calibrators y
    where y.calibrator_id = a.calibrator_id
      and y.active = true
      and y.promoted = true
      and y.validation_status = 'PASS'
      and y.health_status = 'PASS'
      and y.sport = a.sport
      and y.market_family = a.market_type
      and y.model_family = a.model_family
      and y.calibration_version is not null
      and btrim(y.calibration_version) <> ''
      and y.source_data_hash is not null
      and btrim(y.source_data_hash) <> ''
      and y.split_hash is not null
      and btrim(y.split_hash) <> ''
    limit 1;

    if not found then
        return jsonb_build_object(
            'service_status','UNAVAILABLE',
            'challenger_status',v_challenger_status,
            'challenger_blockers',to_jsonb(v_challenger_blockers),
            'serving_model_version',a.model_artifact_version,
            'serving_certification_id',a.certification_id,
            'blockers',jsonb_build_array('SERVING_CALIBRATION_INVALID_OR_UNAVAILABLE'),
            'probability_publishable',false,
            'can_execute',false
        );
    end if;

    v_service_status := case
        when cardinality(v_challenger_blockers) > 0
            then 'DEGRADED_CHALLENGER_BLOCKED'
        else 'AVAILABLE'
    end;

    return jsonb_build_object(
        'service_status',v_service_status,
        'serving_model_family',a.model_family,
        'serving_model_version',a.model_artifact_version,
        'serving_calibration_version',c.calibration_version,
        'serving_calibration_health','PASS',
        'serving_certification_id',a.certification_id,
        'governance_hash',a.governance_hash,
        'feature_schema_version',a.feature_schema_version,
        'state_schema_version',a.state_schema_version,
        'sport',a.sport,
        'market_type',a.market_type,
        'settlement_basis',a.settlement_basis,
        'requested_mode',p_requested_mode,
        'challenger_status',v_challenger_status,
        'challenger_blockers',to_jsonb(v_challenger_blockers),
        'probability_publishable',true,
        'can_execute',false
    );
end;
$$;

-- Stage 0.5 checks the resolved serving version. It never calls the challenger
-- health row as if that row were the serving model's calibration health.
create or replace function public.wow_mlb_stage_0_5_calibration_precheck(
    p_model_family text,
    p_feature_schema_version text,
    p_market_type text default 'MONEYLINE',
    p_settlement_basis text default 'FULL_GAME_OUTRIGHT',
    p_requested_mode text default 'PREGAME',
    p_model_timestamp timestamptz default null,
    p_latest_material_update_at timestamptz default null
) returns jsonb
language plpgsql
stable
security invoker
set search_path = public
as $$
declare
    r jsonb;
begin
    r := public.wow_mlb_resolve_serving_probability_model(
        p_model_family,
        p_feature_schema_version,
        p_market_type,
        p_settlement_basis,
        p_requested_mode,
        p_model_timestamp,
        p_latest_material_update_at
    );

    return r || jsonb_build_object(
        'stage','0.5',
        'calibration_precheck_status',case
            when coalesce((r->>'probability_publishable')::boolean,false)
                 and r->>'serving_calibration_health' = 'PASS'
                then 'PASS'
            else 'BLOCKED'
        end,
        'can_execute',false
    );
end;
$$;

revoke all on function public.wow_mlb_resolve_serving_probability_model(
    text,text,text,text,text,timestamptz,timestamptz
) from anon, authenticated;
revoke all on function public.wow_mlb_stage_0_5_calibration_precheck(
    text,text,text,text,text,timestamptz,timestamptz
) from anon, authenticated;
