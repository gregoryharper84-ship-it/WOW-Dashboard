-- WOW-PATCH-2026-08-30 — calibration/publication blocker formatter hardening.
-- Positive capability evidence is audit evidence, not a blocker. Preserve it
-- separately so the production adapter can classify FORWARD_SHADOW_NOT_COMPLETED
-- as CALIBRATION/PUBLICATION scoped without treating a success reason as failure.
-- Forward-shadow graduation criteria are unchanged. can_execute remains false.

-- Normalize the runtime capability row itself because the production route also
-- reads this evidence object directly. Generic `reason` keys are parsed as
-- failure reasons by legacy adapters, so positive readiness evidence must live
-- under a non-blocker audit key.
update public.wow_runtime_capabilities
set evidence = (evidence - 'reason') || jsonb_build_object(
        'evidence_basis', coalesce(evidence ->> 'evidence_basis', evidence ->> 'reason')
    ),
    updated_at = now()
where capability_key = 'PROP_PROBABILITY'
  and evidence ? 'reason'
  and evidence ->> 'reason' = 'CERTIFIED_PROP_ARTIFACT_AND_REAL_EVIDENCE_READY';

create or replace function public.wow_governed_probability_preflight()
returns jsonb
language sql
stable
set search_path to 'public'
as $function$
with latest_health as (
    select
        calibration_health_status,
        blockers,
        probability_publishable,
        forward_shadow_status,
        forward_shadow_n,
        eligible_shadow_n,
        graded_shadow_n,
        pending_shadow_n,
        assessed_at
    from public.wow_mlb_v2d_calibration_health
    order by assessed_at desc
    limit 1
), prop_capability as (
    select capability_status, evidence, updated_at
    from public.wow_runtime_capabilities
    where capability_key = 'PROP_PROBABILITY'
    limit 1
), state as (
    select
        coalesce(p.capability_status, 'UNAVAILABLE') as specialist_lane_capability,
        coalesce(h.calibration_health_status, 'UNKNOWN') as calibration_health_status,
        coalesce(h.blockers, array[]::text[]) as blockers,
        coalesce(h.probability_publishable, false) as calibration_publishable,
        h.forward_shadow_status,
        h.forward_shadow_n,
        h.eligible_shadow_n,
        h.graded_shadow_n,
        h.pending_shadow_n,
        h.assessed_at,
        case when p.evidence is null then null else p.evidence - 'reason' - 'evidence_basis' end as capability_evidence,
        coalesce(p.evidence ->> 'evidence_basis', p.evidence ->> 'reason') as capability_evidence_basis,
        p.updated_at as capability_updated_at
    from latest_health h
    full join prop_capability p on true
)
select jsonb_build_object(
    'ok', true,
    'specialist_model_capability', case when specialist_lane_capability = 'AVAILABLE' then 'AVAILABLE' else 'NOT_EVALUATED' end,
    'specialist_model_name', null,
    'specialist_model_status', 'NOT_EVALUATED',
    'calibration_health_status', calibration_health_status,
    'calibration_status', case when calibration_health_status in ('PASS','AVAILABLE','HEALTHY') then 'AVAILABLE' else 'UNKNOWN_OR_BLOCKED' end,
    'governed_probability_capability', specialist_lane_capability,
    'governed_publication_capability', case when specialist_lane_capability = 'AVAILABLE' and calibration_publishable then 'AVAILABLE' else 'UNAVAILABLE' end,
    'governed_publishable', specialist_lane_capability = 'AVAILABLE' and calibration_publishable,
    'probability_publishable', specialist_lane_capability = 'AVAILABLE' and calibration_publishable,
    'manual_lane_used', false,
    'manual_confidence_cap', null,
    'failed_contract_scope', case
        when blockers && array['FORWARD_SHADOW_NOT_COMPLETED']::text[] then jsonb_build_array('CALIBRATION','PUBLICATION')
        when calibration_health_status not in ('PASS','AVAILABLE','HEALTHY') then jsonb_build_array('CALIBRATION','PUBLICATION')
        else '[]'::jsonb
    end,
    'probability_claim_status', case
        when specialist_lane_capability = 'AVAILABLE' and calibration_publishable then 'GOVERNED_CALIBRATED_PUBLISHABLE'
        when specialist_lane_capability = 'AVAILABLE' then 'CALIBRATION_BLOCKED_NO_PUBLISH'
        else 'MODEL_UNAVAILABLE'
    end,
    'terminal_ceiling', case
        when specialist_lane_capability = 'AVAILABLE' and not calibration_publishable then 'MODEL_QUALIFIED_HOLD'
        when specialist_lane_capability = 'AVAILABLE' then 'FINAL_APPROVED'
        else 'MODEL_UNAVAILABLE'
    end,
    'blockers', to_jsonb(blockers),
    'forward_shadow_status', forward_shadow_status,
    'forward_shadow_n', forward_shadow_n,
    'eligible_shadow_n', eligible_shadow_n,
    'graded_shadow_n', graded_shadow_n,
    'pending_shadow_n', pending_shadow_n,
    'calibration_assessed_at', assessed_at,
    'capability_evidence', capability_evidence,
    'capability_evidence_basis', capability_evidence_basis,
    'capability_updated_at', capability_updated_at,
    'can_execute', false
)
from state;
$function$;
