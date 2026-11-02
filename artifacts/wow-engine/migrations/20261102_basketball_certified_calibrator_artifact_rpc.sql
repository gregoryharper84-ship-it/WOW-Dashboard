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
-- first. It adds `calibration_artifact_payload`, `calibration_artifact_sha256`,
-- the canonical serializer and the digest guard; this function selects those
-- columns and therefore fails at CREATE time if they are absent.
--
-- Authority. The legacy `wow_basketball_team_event_certification.capability_status`
-- flag is NOT certification authority and is not consulted here. The only
-- accepted authority is an immutable row in
-- `public.wow_team_event_specialist_certifications` -- the same record
-- `v17.fitted_team_event_certification.resolve_certification` returns -- that:
--   * is the active row for that sport/league scope,
--   * has no revocation record for the same artifact/model version,
--   * is CERTIFIED with independent_verification_status=PASS and
--     probability_publishable=true,
--   * names this exact calibrator UUID, digest, version and method.
--
-- Integrity. The returned payload digest is recomputed from the payload itself
-- with the same canonical serialization the Python artifact builder uses, so a
-- mutated `platt_a`/`platt_b`/residual with an unchanged declared digest cannot
-- be returned. `calibration_artifact_sha256` is only trusted after it matches
-- both the payload content and the certified digest.
--
-- This is service-internal model infrastructure. It does not certify, promote,
-- approve bounds, create a prediction, publish a probability, or enable
-- execution. Bounds approval is still decided in the scorer
-- (artifact_type, bounds_policy, bounds_preapproval_status, certification
-- binding, independent verification), never here.

drop function if exists public.wow_basketball_certified_calibrator_artifact(text, uuid);

create function public.wow_basketball_certified_calibrator_artifact(
  p_sport text,
  p_calibrator_id uuid
)
returns table (
  calibration_artifact_id uuid,
  certification_id uuid,
  sport text,
  model_artifact_version text,
  calibrator_version text,
  calibration_method text,
  calibration_sha256 text,
  independent_verification_status text,
  artifact_payload jsonb
)
language sql
stable
security definer
set search_path=''
as $$
  select
    c.calibrator_id,
    cert.certification_id,
    c.sport,
    c.model_artifact_version,
    c.calibrator_version,
    c.calibration_method,
    c.calibration_artifact_sha256,
    cert.independent_verification_status,
    c.calibration_artifact_payload
  from public.wow_basketball_team_event_calibrators c
  join public.wow_team_event_specialist_certifications cert
    on cert.sport = c.sport
   and cert.model_version = c.model_artifact_version
   and cert.calibration_artifact_id = c.calibrator_id::text
   and cert.calibration_sha256 = c.calibration_artifact_sha256
   and cert.calibration_method = c.calibration_method
  where c.calibrator_id = p_calibrator_id
    and c.sport = upper(trim(p_sport))
    and c.sport in ('NBA', 'WNBA')
    and c.promoted
    and c.active
    and c.calibration_artifact_payload is not null
    and c.calibration_artifact_sha256 ~ '^[0-9a-f]{64}$'
    -- V17 independent certification authority, fail closed on revocation.
    and cert.certification_status = 'CERTIFIED'
    and cert.independent_verification_status = 'PASS'
    and cert.probability_publishable is true
    and cert.can_execute is false
    and not exists (
      select 1
        from public.wow_team_event_specialist_certifications revoked
       where revoked.sport = cert.sport
         and revoked.artifact_id = cert.artifact_id
         and revoked.model_version = cert.model_version
         and revoked.certification_status = 'REVOKED'
    )
    -- Must be the same active certification row the scorer resolved.
    and cert.certification_id = (
      select active.certification_id
        from public.wow_team_event_specialist_certifications active
       where active.sport = cert.sport
         and active.league_scope = cert.league_scope
       order by active.created_at desc, active.certification_id desc
       limit 1
    )
    -- Shadow / review-required artifacts can never be returned as certified.
    and c.calibration_artifact_payload->>'artifact_type' = 'BINARY_PLATT_CALIBRATOR'
    and c.calibration_artifact_payload->>'sport' = c.sport
    and c.calibration_artifact_payload->>'calibration_version' = c.calibrator_version
    and c.calibration_artifact_payload->>'certification_status' = 'PASS'
    and c.calibration_artifact_payload->>'health_status' = 'PASS'
    and coalesce(c.calibration_artifact_payload->>'can_execute', 'false') = 'false'
    -- Content-derived digest, not a stored label.
    and encode(
          sha256(convert_to(public.wow_canonical_json_text(c.calibration_artifact_payload), 'UTF8')),
          'hex'
        ) = c.calibration_artifact_sha256
  limit 1;
$$;

revoke all on function public.wow_basketball_certified_calibrator_artifact(text, uuid) from public;
revoke all on function public.wow_basketball_certified_calibrator_artifact(text, uuid) from anon;
revoke all on function public.wow_basketball_certified_calibrator_artifact(text, uuid) from authenticated;
grant execute on function public.wow_basketball_certified_calibrator_artifact(text, uuid) to service_role;
