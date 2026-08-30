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

-- Canonicalize the one accepted MLB strikeout alias before exact artifact lookup.
-- This keeps strict artifact certification while allowing the established
-- controlling-specialist route alias used by the production acquisition probe.
create or replace function public.wow_prop_certified_model_artifact(
    p_sport text,
    p_stat_type text,
    p_feature_schema_version text
)
returns jsonb
language plpgsql
stable
set search_path to 'public'
as $function$
declare
    v_row public.wow_prop_fitted_model_artifacts%rowtype;
    v_stat_type text;
begin
    v_stat_type := case
        when upper(p_sport) = 'MLB' and upper(p_stat_type) = 'STRIKEOUTS' then 'PITCHER_STRIKEOUTS'
        else upper(p_stat_type)
    end;

    select * into v_row
      from public.wow_prop_fitted_model_artifacts
     where upper(sport) = upper(p_sport)
       and upper(stat_type) = v_stat_type
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
            'canonical_stat_type', v_stat_type,
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
        'requested_stat_type', p_stat_type,
        'canonical_stat_type', v_stat_type,
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
$function$;
