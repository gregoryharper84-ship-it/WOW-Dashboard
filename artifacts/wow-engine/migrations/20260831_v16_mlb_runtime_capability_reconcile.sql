-- WOW v16 Clean Core — reconcile the shared MLB event runtime capability after
-- prospective certification. This changes model availability only; it does not
-- ratify full publication, rank eligibility, money qualification, or execution.
--
-- The update is evidence-conditioned and idempotent. Missing artifact or
-- Calibration Health evidence fails closed by leaving the capability unchanged.

with artifact as (
  select a.*
  from public.wow_mlb_event_fitted_model_artifacts a
  where a.provider_identity='WOW_MLB_EVENT_FITTED_MODEL_V1'
    and a.model_artifact_version='MLB_V16_V2D_CONTEXT_SHARED_SIM_R1'
    and a.feature_schema_version='MLB_V2D_CONTEXT_V1'
    and a.lifecycle_state='PROSPECTIVE_CERTIFIED'
    and a.active=true
    and a.promoted=true
    and a.certification_id='V16-PROSPECTIVE-20260831-MLB-EVENT-R1'
    and jsonb_typeof(a.specialist_calibration_identity)='object'
    and a.specialist_calibration_identity<>'{}'::jsonb
  order by a.promoted_at desc nulls last,a.created_at desc
  limit 1
), health as (
  select h.*
  from public.wow_mlb_v2d_calibration_health h
  where h.calibration_health_status='PASS'
  order by h.assessed_at desc
  limit 1
), eligible as (
  select a.*,h.graded_shadow_n,h.pending_shadow_n,h.assessed_at as calibration_health_assessed_at
  from artifact a cross join health h
)
update public.wow_runtime_capabilities rc
set capability_status='AVAILABLE',
    updated_at=now(),
    evidence=jsonb_build_object(
      'provider_identity','WOW_MLB_EVENT_FITTED_MODEL_V1',
      'model_artifact_version',e.model_artifact_version,
      'feature_schema_version',e.feature_schema_version,
      'lifecycle_state',e.lifecycle_state,
      'certification_id',e.certification_id,
      'calibration_health_status','PASS',
      'calibration_health_assessed_at',e.calibration_health_assessed_at,
      'graded_forward_shadow_n',e.graded_shadow_n,
      'pending_forward_shadow_n',e.pending_shadow_n,
      'model_probability_publishable',true,
      'probability_publishable',false,
      'terminal_ceiling','MODEL_QUALIFIED_HOLD',
      'market_prior_weight',0.0,
      'minimum_simulations',50000,
      'can_execute',false
    ),
    can_execute=false
from eligible e
where rc.capability_key='MLB_EVENT_PROBABILITY';
