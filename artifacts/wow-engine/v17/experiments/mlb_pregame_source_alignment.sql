-- WOW V17 Class C research inventory ONLY. SELECT; no writes.
-- A source snapshot captured AFTER the model timestamp CANNOT certify the
-- model's original inputs even if captured before the game begins.
SELECT
  p.event_prediction_id::text AS prediction_id,
  p.official_event_id,
  p.event_start_time,
  p.model_timestamp,
  p.source_snapshot_id::text,
  s.captured_at AS source_captured_at,
  s.manifest_hash AS source_manifest_hash,
  s.coverage_status,
  p.model_inputs_hash
FROM public.wow_event_predictions AS p
JOIN public.wow_event_outcomes AS o ON o.event_prediction_id = p.event_prediction_id
LEFT JOIN public.wow_event_source_snapshots AS s
  ON s.source_snapshot_id = p.source_snapshot_id
WHERE p.sport = 'MLB'
  AND p.market_family = 'OUTRIGHT_WINNER'
  AND p.calibration_status = 'PASS'
  AND p.final_refresh_status = 'PASS'
  AND p.terminal_label = 'FINAL_APPROVED'
  AND p.rank_eligible IS TRUE AND p.probability_publishable IS TRUE
  AND p.model_timestamp < p.event_start_time
  AND p.model_timestamp >= p.latest_material_update_timestamp
  AND p.created_at < p.event_start_time
  AND p.calibrated_home_probability BETWEEN 0 AND 1
  AND o.void IS NOT TRUE
  AND o.official_winner IN (p.home_team, p.away_team)
ORDER BY p.event_start_time, p.model_timestamp, p.event_prediction_id;
