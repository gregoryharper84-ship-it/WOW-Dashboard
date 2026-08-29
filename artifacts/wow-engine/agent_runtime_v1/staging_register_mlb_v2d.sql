-- Idempotent staging registration for the real MLB V2D frozen research artifact.
-- This file MUST NOT make the capability AVAILABLE or mark the artifact certified.
-- Publication/promotion remains governed by forward-shadow health + ratification.

insert into wow.model_artifacts(
  artifact_id,provider_id,model_family,model_version,feature_schema_version,
  storage_uri,sha256,training_cutoff,evaluation_summary,certification_status,
  certified_at,retired_at
) values (
  'd233092f-3d73-4986-8d76-33f51a9a4fee',
  'WOW_MLB_V2D_SERVER_BRIDGE',
  'MLB_FULL_GAME_OUTRIGHT_WINNER',
  'MLB_V2D_2024_TRAIN_2022_2024_HOME_PRIOR_R1',
  'a8a64564c9bcd5327d4651e552c79754',
  'supabase://wow-engine-validation/public/wow_mlb_v2d_frozen_spec/d233092f-3d73-4986-8d76-33f51a9a4fee',
  'bc261423ce103c8871246b567ecbe4c6e77f94e3b0c7645cf99d80b051be30ad',
  '2024-08-09T23:59:59Z',
  '{"training_rows":3330,"training_data_sha256":"2a1bbcbd670e9c93de675ac16692779972cb3c7b8fb2000a65c0090b0c321bd7","calibration_id":"0756545d-4ef5-47b7-950a-53567f0bf9fe","calibration_method":"LOGIT_INTERCEPT_POOLED_2022_2024","source_status":"RESEARCH_FROZEN","production_feature_ready":false,"probability_publishable":false}'::jsonb,
  'RESEARCH_FROZEN_NOT_PUBLISHABLE',null,null
)
on conflict(artifact_id) do update
set provider_id=excluded.provider_id,
    model_family=excluded.model_family,
    model_version=excluded.model_version,
    feature_schema_version=excluded.feature_schema_version,
    storage_uri=excluded.storage_uri,
    sha256=excluded.sha256,
    training_cutoff=excluded.training_cutoff,
    evaluation_summary=excluded.evaluation_summary,
    certification_status='RESEARCH_FROZEN_NOT_PUBLISHABLE',
    certified_at=null;

insert into wow.capability_registry(
  capability_id,sport,market_family,stat_family,period,provider_id,model_family,
  artifact_id,calibrator_id,status,valid_from,valid_to
) values (
  '8aa3a1b8-e3c8-4f6e-a9dc-3d9924c89001',
  'MLB','OUTRIGHT_WINNER',null,'FULL_GAME','WOW_MLB_V2D_SERVER_BRIDGE',
  'MLB_FULL_GAME_OUTRIGHT_WINNER','d233092f-3d73-4986-8d76-33f51a9a4fee',
  '0756545d-4ef5-47b7-950a-53567f0bf9fe','UNAVAILABLE','2026-08-29T00:00:00Z',null
)
on conflict(capability_id) do update
set provider_id=excluded.provider_id,
    model_family=excluded.model_family,
    artifact_id=excluded.artifact_id,
    calibrator_id=excluded.calibrator_id,
    status='UNAVAILABLE',
    valid_to=null;
