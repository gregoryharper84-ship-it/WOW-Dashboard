-- WOW V17 — WNBA prop prospective certification.
--
-- This is an exact-artifact lifecycle transition, not model creation. The four
-- candidates must already match the immutable-source replay artifacts registered
-- through the governed control plane. CI separately requires the deterministic
-- certification replay to return READY_FOR_LIFECYCLE_REVIEW before this change
-- is merged/applied.
--
-- Registry-level probability_publishable remains false by design. Runtime
-- sporting probability still requires exact evidence, model adapter,
-- calibration/bounds, and terminal reduction. can_execute remains false.

do $$
declare
    v_total integer := 0;
    v_exact integer := 0;
begin
    select count(*) into v_total
      from public.wow_prop_fitted_model_artifacts
     where upper(sport) = 'WNBA';

    -- Fresh CI/dev databases do not contain production model data. The schema
    -- migration remains replayable there without manufacturing artifacts.
    if v_total = 0 then
        raise notice 'WNBA_PROP_CERTIFICATION_SKIPPED_NO_CANDIDATES';
    else
        if v_total <> 4 then
            raise exception 'WNBA_PROP_CERTIFICATION_ROUTE_SET_INCOMPLETE: expected 4 rows, found %', v_total;
        end if;

        with expected(stat_type, model_artifact_version, artifact_checksum) as (
            values
              ('POINTS', 'WNBA_PTS_POISSON_LOGGLM_V1_2026_09_05', 'd04c79a4d982bebff6cbfa0f500928919bd65fc75923928b7bd32fceecb1e281'),
              ('REBOUNDS', 'WNBA_REB_POISSON_LOGGLM_V1_2026_09_05', '39d1b8a5515eeed58c5f8da174473fda01421c524744d8d8fb2b1ac903ae3ae7'),
              ('ASSISTS', 'WNBA_AST_POISSON_LOGGLM_V1_2026_09_05', 'e992f1ca66084c2099b2e7699862f82f124d11777a1947eff8e4beb6e0a7a1a4'),
              ('THREE_POINTERS_MADE', 'WNBA_3PM_POISSON_LOGGLM_V1_2026_09_05', '8e6d618fcf50b838303c3351faeda05a5c01d21812cd0b8c5aaafe3a6c0bda5f')
        )
        select count(*) into v_exact
          from public.wow_prop_fitted_model_artifacts a
          join expected e
            on upper(a.stat_type) = e.stat_type
           and a.model_artifact_version = e.model_artifact_version
           and a.artifact_checksum = e.artifact_checksum
         where upper(a.sport) = 'WNBA'
           and a.provider_identity = 'WOW_PROP_FITTED_MODEL_V1'
           and a.model_family = 'WNBA_PROP_POISSON_LOGGLM_V1'
           and a.feature_schema_version = 'PROP_FEATURES_V1'
           and a.calibrator_version = 'WNBA_PROP_PRECALIBRATION_BOOTSTRAP_V1'
           and a.lifecycle_state = 'CANDIDATE'
           and a.promoted = false
           and a.active = false
           and a.probability_publishable = false
           and a.can_execute = false
           and a.training_rows = 1300
           and coalesce(a.validation_metrics->>'validation_status','') = 'PASS'
           and jsonb_typeof(coalesce(a.validation_metrics->'blockers','[]'::jsonb)) = 'array'
           and jsonb_array_length(coalesce(a.validation_metrics->'blockers','[]'::jsonb)) = 0
           and coalesce(a.validation_metrics->'source'->'source_snapshot'->>'bundle_id','') = 'wnba-2026-20260904'
           and coalesce(a.validation_metrics->'source'->'source_snapshot'->>'provider','') = 'SPORTSDATAVERSE_WNBA_STATS'
           and coalesce(a.validation_metrics->'source'->'source_snapshot'->>'license_id','') = 'CC-BY-4.0'
           and coalesce((a.validation_metrics->'source'->'source_snapshot'->>'attribution_required')::boolean,false) = true
           and coalesce((a.validation_metrics->'source'->'source_snapshot'->>'grants_model_capability')::boolean,true) = false;

        if v_exact <> 4 then
            raise exception 'WNBA_PROP_CERTIFICATION_EXACT_ARTIFACT_GATE_FAILED: matched % of 4', v_exact;
        end if;

        update public.wow_prop_fitted_model_artifacts
           set lifecycle_state = 'PROSPECTIVE_CERTIFIED',
               promoted = true,
               active = true,
               certification_id = case upper(stat_type)
                   when 'POINTS' then 'WNBA-V17-PROSPECTIVE-20260915-POINTS'
                   when 'REBOUNDS' then 'WNBA-V17-PROSPECTIVE-20260915-REBOUNDS'
                   when 'ASSISTS' then 'WNBA-V17-PROSPECTIVE-20260915-ASSISTS'
                   when 'THREE_POINTERS_MADE' then 'WNBA-V17-PROSPECTIVE-20260915-3PM'
                   else certification_id
               end,
               probability_publishable = false,
               can_execute = false
         where upper(sport) = 'WNBA';
    end if;
end;
$$;

-- Keep the global prop capability summary derived from exact artifact truth.
-- AVAILABLE here means at least one scoped certified route exists; it never
-- grants cross-sport authority. Every score request still performs exact
-- sport/stat/schema artifact resolution.
create or replace function public.wow_v17_refresh_prop_probability_capability()
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_scopes jsonb := '[]'::jsonb;
    v_count integer := 0;
    v_status text := 'UNAVAILABLE';
begin
    select count(*),
           coalesce(
               jsonb_agg(
                   jsonb_build_object(
                       'sport', upper(sport),
                       'stat_type', upper(stat_type),
                       'feature_schema_version', feature_schema_version,
                       'model_family', model_family,
                       'model_artifact_version', model_artifact_version,
                       'calibrator_version', calibrator_version,
                       'lifecycle_state', lifecycle_state
                   ) order by upper(sport), upper(stat_type)
               ),
               '[]'::jsonb
           )
      into v_count, v_scopes
      from public.wow_prop_fitted_model_artifacts
     where active = true
       and promoted = true
       and lifecycle_state in ('PROSPECTIVE_CERTIFIED','CHAMPION')
       and probability_publishable = false
       and can_execute = false;

    if v_count > 0 then
        v_status := 'AVAILABLE';
    end if;

    insert into public.wow_runtime_capabilities(
        capability_key, capability_status, evidence, can_execute, updated_at
    ) values (
        'PROP_PROBABILITY',
        v_status,
        jsonb_build_object(
            'provider_identity', 'WOW_PROP_FITTED_MODEL_V1',
            'coverage_mode', 'SCOPED',
            'certified_scope_count', v_count,
            'available_scopes', v_scopes,
            'artifact_registry', 'wow_prop_fitted_model_artifacts',
            'scope_contract', 'PROP_PROBABILITY=AVAILABLE is not cross-sport authority; every sport/stat/schema request requires an exact active promoted certified artifact plus its fitted adapter and governed calibration package.',
            'llp_player_props_allowed', false,
            'probability_publishable', false,
            'can_execute', false
        ),
        false,
        now()
    )
    on conflict (capability_key) do update
       set capability_status = excluded.capability_status,
           evidence = coalesce(public.wow_runtime_capabilities.evidence, '{}'::jsonb) || excluded.evidence,
           can_execute = false,
           updated_at = now();

    return jsonb_build_object(
        'capability_key', 'PROP_PROBABILITY',
        'capability_status', v_status,
        'certified_scope_count', v_count,
        'available_scopes', v_scopes,
        'probability_publishable', false,
        'can_execute', false
    );
end;
$$;

revoke all on function public.wow_v17_refresh_prop_probability_capability() from public, anon, authenticated;
grant execute on function public.wow_v17_refresh_prop_probability_capability() to service_role;

select public.wow_v17_refresh_prop_probability_capability();
