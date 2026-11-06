-- WOW V17 Class C research inventory ONLY. SELECT; no data mutation.
-- Return one bounded historical MLB dynamic team-state window per invocation.
-- Re-run for each date window; NEVER blindly treat repeated rows as games.
-- Output fields match pregame_provenance_gate.audit_d1_provenance.
SELECT
  d.training_row_id::text,
  d.sport,
  d.model_family,
  d.feature_schema_version,
  d.official_event_id,
  d.event_start_time,
  d.feature_as_of,
  d.archived_pregame_snapshot,
  d.historical_reconstruction,
  d.source_manifest_sha256,
  d.features,
  d.outcome_json
FROM public.wow_d1_training_rows AS d
WHERE d.sport = 'MLB'
  AND d.model_family = 'MLB_DYNAMIC_TEAM_STATE_LOGIT_V2'
  AND d.feature_schema_version = 'MLB_DYNAMIC_TEAM_STATE_FEATURES_V2'
  AND d.event_start_time >= timestamptz '2026-09-01T00:00:00Z'
  AND d.event_start_time < timestamptz '2026-10-01T00:00:00Z'
ORDER BY d.event_start_time, d.official_event_id, d.training_row_id
LIMIT 3000;
