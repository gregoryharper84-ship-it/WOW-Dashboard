-- Restore the internal NBA/WNBA certified-calibrator lookup used by
-- v17/nba_current_pregame_scorer.py::resolve_nba_calibrator_artifact.
--
-- Why this exists: the scorer has always called
-- `wow_basketball_certified_calibrator_artifact`, but no migration ever defined
-- it. Read-only inspection of the production project on 2026-11-02 found only
-- `wow_basketball_certified_model_artifact` in pg_proc, so the call failed at
-- PostgREST and was reported as a scorer infrastructure failure instead of a
-- calibrator-artifact answer.
--
-- Dependency order: apply 20261007_basketball_calibration_artifact_evidence.sql
-- first. It adds `calibration_artifact_payload` and `calibration_artifact_sha256`
-- to public.wow_basketball_team_event_calibrators; this function selects them
-- and therefore fails at CREATE time if those columns are absent.
--
-- This is service-internal model infrastructure. It does not certify, promote,
-- approve bounds, create a prediction, publish a probability, or enable
-- execution. It cannot return a row for a sport that is not CERTIFIED, and it
-- cannot return a shadow/REVIEW_REQUIRED payload: the certification join plus
-- the exact calibrator_version match means an unreviewed artifact has no
-- certified calibrator_version to join on. Bounds approval is still decided in
-- the scorer (artifact_type, bounds_policy, bounds_preapproval_status,
-- certification binding, independent verification), never here.

create or replace function public.wow_basketball_certified_calibrator_artifact(
  p_sport text,
  p_calibrator_id uuid
)
returns table (
  calibration_artifact_id uuid,
  sport text,
  model_artifact_version text,
  calibrator_version text,
  calibration_method text,
  calibration_sha256 text,
  artifact_payload jsonb
)
language sql
stable
security definer
set search_path=''
as $$
  select
    c.calibrator_id,
    c.sport,
    c.model_artifact_version,
    c.calibrator_version,
    c.calibration_method,
    c.calibration_artifact_sha256,
    c.calibration_artifact_payload
  from public.wow_basketball_team_event_calibrators c
  join public.wow_basketball_team_event_certification cert
    on cert.sport = c.sport
   and cert.model_artifact_version = c.model_artifact_version
   and cert.calibrator_version = c.calibrator_version
  where c.calibrator_id = p_calibrator_id
    and c.sport = upper(p_sport)
    and c.promoted
    and c.active
    and cert.capability_status = 'CERTIFIED'
    and c.calibration_artifact_payload is not null
    and c.calibration_artifact_sha256 ~ '^[0-9a-f]{64}$'
  limit 1;
$$;

revoke all on function public.wow_basketball_certified_calibrator_artifact(text, uuid) from public;
revoke all on function public.wow_basketball_certified_calibrator_artifact(text, uuid) from anon;
revoke all on function public.wow_basketball_certified_calibrator_artifact(text, uuid) from authenticated;
grant execute on function public.wow_basketball_certified_calibrator_artifact(text, uuid) to service_role;
