-- WOW-PATCH-2026-08-30-CALIBRATION-PUBLICATION-LANE-SEPARATION
-- Internal control-plane capability projection. This does NOT modify forward-
-- shadow graduation, calibration-health criteria, model artifacts, or execution.

create or replace function public.wow_scoped_probability_capability(
  p_capability_key text
) returns jsonb
language plpgsql
stable
set search_path to ''
as $function$
declare
  c public.wow_runtime_capabilities%rowtype;
  h public.wow_mlb_v2d_calibration_health%rowtype;
  g jsonb;
  v_source_status text := 'UNAVAILABLE';
  v_global_status text := 'UNAVAILABLE';
  v_health_status text := 'UNAVAILABLE';
  v_health_blockers text[] := '{}';
  v_evidence_text text := '';
  v_forward_shadow_only boolean := false;
  v_model_invalidating boolean := false;
  v_publication_lock boolean := false;
begin
  select c0.* into c
  from public.wow_runtime_capabilities c0
  where c0.capability_key = p_capability_key
  limit 1;

  if found then
    v_source_status := coalesce(c.capability_status, 'UNAVAILABLE');
    v_evidence_text := upper(coalesce(c.evidence::text, ''));
  end if;

  g := public.wow_governed_deployment_state();
  v_global_status := coalesce(g->>'governed_probability_capability', 'UNAVAILABLE');

  select h0.* into h
  from public.wow_mlb_v2d_calibration_health h0
  order by h0.assessed_at desc
  limit 1;

  if found then
    v_health_status := coalesce(h.calibration_health_status, 'UNAVAILABLE');
    v_health_blockers := coalesce(h.blockers, '{}');
  else
    v_health_status := coalesce(g->>'calibration_health_status', 'UNAVAILABLE');
  end if;

  v_forward_shadow_only :=
    'FORWARD_SHADOW_NOT_COMPLETED' = any(v_health_blockers)
    and not exists (
      select 1
      from unnest(v_health_blockers) b
      where b <> 'FORWARD_SHADOW_NOT_COMPLETED'
    );

  v_model_invalidating :=
    position('MODEL_UNAVAILABLE' in v_evidence_text) > 0
    or position('SPECIALIST_ROUTING_UNAVAILABLE' in v_evidence_text) > 0
    or position('CERTIFIED_MODEL_ARTIFACT_NOT_FOUND' in v_evidence_text) > 0
    or position('MODEL_REGISTRY_UNAVAILABLE' in v_evidence_text) > 0
    or position('DATA_PROVIDER_OUTAGE' in v_evidence_text) > 0;

  v_publication_lock :=
    v_source_status = 'AVAILABLE'
    and v_global_status <> 'AVAILABLE'
    and v_health_status = 'BLOCKED'
    and v_forward_shadow_only
    and not v_model_invalidating;

  if v_publication_lock then
    return jsonb_build_object(
      'capability_key', p_capability_key,
      'source_capability_status', v_source_status,
      'routing_capability_status', 'AVAILABLE_FOR_RESEARCH',
      'specialist_model_capability', 'ROUTE_DEPENDENT',
      'calibration_capability', 'BLOCKED_OR_UNKNOWN',
      'governed_probability_capability', v_global_status,
      'governed_publication_capability', 'UNAVAILABLE',
      'governed_publishable', false,
      'money_qualification_capability', 'BLOCKED',
      'failed_contract_scope', jsonb_build_array('CALIBRATION', 'PUBLICATION'),
      'probability_claim_status', 'CALIBRATION_BLOCKED_NO_PUBLISH',
      'terminal_ceiling', 'MODEL_QUALIFIED_HOLD',
      'blockers', to_jsonb(v_health_blockers),
      'calibration_health_status', v_health_status,
      'calibration_health_assessed_at', h.assessed_at,
      'source_evidence', coalesce(c.evidence, '{}'::jsonb),
      'can_execute', false
    );
  end if;

  if v_source_status = 'AVAILABLE'
     and v_global_status = 'AVAILABLE'
     and coalesce((g->>'probability_publishable')::boolean, false) then
    return jsonb_build_object(
      'capability_key', p_capability_key,
      'source_capability_status', v_source_status,
      'routing_capability_status', 'AVAILABLE',
      'specialist_model_capability', 'ROUTE_DEPENDENT',
      'calibration_capability', 'AVAILABLE',
      'governed_probability_capability', v_global_status,
      'governed_publication_capability', 'AVAILABLE',
      'governed_publishable', true,
      'money_qualification_capability', 'ROUTE_DEPENDENT',
      'failed_contract_scope', '[]'::jsonb,
      'probability_claim_status', 'GOVERNED_CALIBRATED_PUBLISHABLE',
      'terminal_ceiling', null,
      'blockers', '[]'::jsonb,
      'calibration_health_status', v_health_status,
      'calibration_health_assessed_at', h.assessed_at,
      'source_evidence', coalesce(c.evidence, '{}'::jsonb),
      'can_execute', false
    );
  end if;

  return jsonb_build_object(
    'capability_key', p_capability_key,
    'source_capability_status', v_source_status,
    'routing_capability_status', case when v_source_status = 'AVAILABLE' then 'AVAILABLE_ROUTE_PUBLICATION_SCOPE_UNRESOLVED' else 'UNAVAILABLE' end,
    'specialist_model_capability', 'ROUTE_DEPENDENT',
    'calibration_capability', 'UNKNOWN_OR_UNAVAILABLE',
    'governed_probability_capability', v_global_status,
    'governed_publication_capability', 'UNAVAILABLE',
    'governed_publishable', false,
    'money_qualification_capability', 'BLOCKED',
    'failed_contract_scope', jsonb_build_array('GLOBAL'),
    'probability_claim_status', case when v_model_invalidating then 'MODEL_UNAVAILABLE' else 'CALIBRATION_BLOCKED_NO_PUBLISH' end,
    'terminal_ceiling', case when v_model_invalidating then 'MODEL_UNAVAILABLE' else 'RESEARCH_INTEREST' end,
    'blockers', to_jsonb(v_health_blockers),
    'calibration_health_status', v_health_status,
    'calibration_health_assessed_at', h.assessed_at,
    'source_evidence', coalesce(c.evidence, '{}'::jsonb),
    'can_execute', false
  );
end;
$function$;

-- Internal backend RPC only. Do not expose capability-control internals to
-- anonymous/authenticated Data API callers.
revoke all on function public.wow_scoped_probability_capability(text) from public;
revoke all on function public.wow_scoped_probability_capability(text) from anon;
revoke all on function public.wow_scoped_probability_capability(text) from authenticated;
grant execute on function public.wow_scoped_probability_capability(text) to service_role;
