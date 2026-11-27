-- PROTECTED Class C provenance | Incident #1939 | rollback candidate, NOT APPLIED
-- Removes only the two keys added by 20261125_nfl_champion_calibrator_digest_binding.sql.
-- Refuses once any specialist certificate references the digest, because
-- certificates are immutable and would otherwise point at a vanished binding.
do $rollback$
declare
  v_sha constant text := '21b6cc797f02f27088ed6e2fd674c465dd2d7b21d29e6e88835b7727923eb880';
  v_rows integer;
begin
  if exists (
    select 1 from public.wow_team_event_specialist_certifications
     where calibration_sha256 = v_sha
  ) then
    raise exception 'NFL_CALIBRATOR_DIGEST_ROLLBACK_REFUSED_CERTIFICATE_REFERENCES_DIGEST';
  end if;

  update public.wow_calibrators
     set fit_metrics_json = fit_metrics_json
         - 'calibration_artifact_payload'
         - 'calibration_artifact_sha256'
   where calibrator_id = '756379bd-f557-4508-8ecb-91a5d336ec64'
     and fit_metrics_json->>'calibration_artifact_sha256' = v_sha;
  get diagnostics v_rows = row_count;
  if v_rows <> 1 then
    raise exception 'NFL_CALIBRATOR_DIGEST_ROLLBACK_PRECONDITION_FAILED:%', v_rows;
  end if;
end
$rollback$;
