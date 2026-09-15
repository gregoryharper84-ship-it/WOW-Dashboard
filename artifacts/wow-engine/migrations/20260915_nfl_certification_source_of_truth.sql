-- WOW V17 — repair NFL certification source-of-truth drift.
--
-- The production NFL champion uses NFL_EVENT_PREGAME_PRIOR_V1, while the
-- historical P0 capability seed still referenced NFL_EVENT_FEATURES_V1. That
-- stale schema made a real CHAMPION artifact appear MODEL_UNAVAILABLE.
--
-- This migration does not train, certify, or fabricate a model. It only derives
-- runtime capability from the exact certified-artifact RPC plus an active PASS
-- calibrator. Wager execution remains forbidden.

create or replace function public.wow_v17_refresh_nfl_event_capability()
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_schema constant text := 'NFL_EVENT_PREGAME_PRIOR_V1';
    v_artifact jsonb;
    v_calibrator_ready boolean := false;
    v_ready boolean := false;
    v_status text := 'UNAVAILABLE';
    v_terminal text := 'MODEL_UNAVAILABLE';
    v_evidence jsonb;
begin
    v_artifact := public.wow_nfl_event_certified_model_artifact(v_schema);

    if coalesce((v_artifact->>'ok')::boolean, false)
       and nullif(v_artifact->>'calibrator_id', '') is not null then
        select exists (
            select 1
              from public.wow_calibrators c
             where c.calibrator_id = (v_artifact->>'calibrator_id')::uuid
               and c.sport = 'NFL'
               and c.market_family = 'OUTRIGHT_WINNER'
               and c.model_family = v_artifact->>'model_family'
               and c.active = true
               and c.promoted = true
               and c.validation_status = 'PASS'
               and c.health_status = 'PASS'
        ) into v_calibrator_ready;
    end if;

    v_ready := coalesce((v_artifact->>'ok')::boolean, false) and v_calibrator_ready;
    if v_ready then
        v_status := 'AVAILABLE';
        v_terminal := 'MODEL_QUALIFIED_HOLD';
    end if;

    v_evidence := jsonb_build_object(
        'provider_identity', 'WOW_NFL_EVENT_FITTED_MODEL_V1',
        'controlling_specialist', 'wow.nfl-game-win-probability-expert',
        'feature_schema_version', v_schema,
        'fitted_model_ready', coalesce((v_artifact->>'ok')::boolean, false),
        'certified_artifact_ready', coalesce((v_artifact->>'ok')::boolean, false),
        'calibrator_ready', v_calibrator_ready,
        'model_artifact_version', v_artifact->>'model_artifact_version',
        'model_family', v_artifact->>'model_family',
        'certification_id', v_artifact->>'certification_id',
        'calibrator_id', v_artifact->>'calibrator_id',
        'lifecycle_state', v_artifact->>'lifecycle_state',
        'model_probability_publishable', v_ready,
        'probability_publishable', false,
        'terminal_label_if_scored_now', v_terminal,
        'source_of_truth', 'CERTIFIED_ARTIFACT_PLUS_ACTIVE_PASS_CALIBRATOR',
        'can_execute', false
    );

    insert into public.wow_runtime_capabilities(
        capability_key, capability_status, evidence, can_execute, updated_at
    ) values (
        'NFL_EVENT_PROBABILITY', v_status, v_evidence, false, now()
    )
    on conflict (capability_key) do update
       set capability_status = excluded.capability_status,
           evidence = coalesce(public.wow_runtime_capabilities.evidence, '{}'::jsonb) || excluded.evidence,
           can_execute = false,
           updated_at = now();

    return jsonb_build_object(
        'capability_key', 'NFL_EVENT_PROBABILITY',
        'capability_status', v_status,
        'feature_schema_version', v_schema,
        'artifact_state', v_artifact,
        'calibrator_ready', v_calibrator_ready,
        'terminal_label_if_scored_now', v_terminal,
        'probability_publishable', false,
        'can_execute', false
    );
end;
$$;

revoke all on function public.wow_v17_refresh_nfl_event_capability() from public, anon, authenticated;
grant execute on function public.wow_v17_refresh_nfl_event_capability() to service_role;

create or replace function public.wow_v17_refresh_nfl_event_capability_trigger()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    perform public.wow_v17_refresh_nfl_event_capability();
    return null;
end;
$$;

-- Artifact lifecycle changes must immediately reconcile the capability row.
drop trigger if exists wow_nfl_artifact_refresh_capability on public.wow_nfl_event_fitted_model_artifacts;
create trigger wow_nfl_artifact_refresh_capability
after insert or update or delete on public.wow_nfl_event_fitted_model_artifacts
for each statement execute function public.wow_v17_refresh_nfl_event_capability_trigger();

-- Calibrator health/promotion changes can invalidate or restore the champion.
drop trigger if exists wow_nfl_calibrator_refresh_capability on public.wow_calibrators;
create trigger wow_nfl_calibrator_refresh_capability
after insert or update or delete on public.wow_calibrators
for each statement execute function public.wow_v17_refresh_nfl_event_capability_trigger();

create or replace function public.wow_nfl_event_p0_readiness()
returns jsonb
language plpgsql
stable
security invoker
set search_path = public
as $$
declare
    v_schema constant text := 'NFL_EVENT_PREGAME_PRIOR_V1';
    a jsonb;
    c public.wow_runtime_capabilities%rowtype;
    s public.wow_specialist_registry%rowtype;
    v_calibrator_ready boolean := false;
begin
    a := public.wow_nfl_event_certified_model_artifact(v_schema);
    select * into c
      from public.wow_runtime_capabilities
     where capability_key = 'NFL_EVENT_PROBABILITY';
    select * into s
      from public.wow_specialist_registry
     where sport = 'NFL' and market_family = 'OUTRIGHT_WINNER';

    if coalesce((a->>'ok')::boolean, false)
       and nullif(a->>'calibrator_id', '') is not null then
        select exists (
            select 1
              from public.wow_calibrators cal
             where cal.calibrator_id = (a->>'calibrator_id')::uuid
               and cal.sport = 'NFL'
               and cal.market_family = 'OUTRIGHT_WINNER'
               and cal.active = true
               and cal.promoted = true
               and cal.validation_status = 'PASS'
               and cal.health_status = 'PASS'
        ) into v_calibrator_ready;
    end if;

    return jsonb_build_object(
        'ok', true,
        'provider_identity', 'WOW_NFL_EVENT_FITTED_MODEL_V1',
        'feature_schema_version', v_schema,
        'controlling_specialist', coalesce(s.controlling_specialist, 'wow.nfl-game-win-probability-expert'),
        'specialist_route_ready', coalesce(s.active, false),
        'artifact_state', a,
        'calibrator_ready', v_calibrator_ready,
        'capability_status', coalesce(c.capability_status, 'UNAVAILABLE'),
        'model_status', case
            when coalesce((a->>'ok')::boolean, false)
             and v_calibrator_ready
             and coalesce(c.capability_status, 'UNAVAILABLE') = 'AVAILABLE'
              then 'AVAILABLE'
            else 'MODEL_UNAVAILABLE'
        end,
        'probability_publishable', false,
        'can_execute', false
    );
end;
$$;

revoke all on function public.wow_nfl_event_p0_readiness() from anon, authenticated;

-- Reconcile the currently serving champion now; future lifecycle changes are
-- handled by the triggers above.
select public.wow_v17_refresh_nfl_event_capability();
