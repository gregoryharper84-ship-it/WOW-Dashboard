-- Add immutable shadow calibration-artifact evidence for NBA/WNBA team-event fits.
-- Class C evidence generation only. No certification, promotion, or probability publication.

alter table public.wow_basketball_team_event_calibrators
  add column if not exists calibration_artifact_payload jsonb,
  add column if not exists calibration_artifact_sha256 text;

alter table public.wow_basketball_team_event_calibrators
  drop constraint if exists wow_basketball_calibration_artifact_sha256_shape;

alter table public.wow_basketball_team_event_calibrators
  add constraint wow_basketball_calibration_artifact_sha256_shape
  check (
    calibration_artifact_sha256 is null
    or calibration_artifact_sha256 ~ '^[0-9a-f]{64}$'
  );
