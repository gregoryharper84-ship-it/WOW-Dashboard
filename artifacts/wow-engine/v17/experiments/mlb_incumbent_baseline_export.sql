-- Research-only SELECT. Never write or modify production ledgers.
-- Export the JSON result of this query to a locally controlled file, then run
-- mlb_incumbent_baseline_audit.py --input ... --audited-at ...
-- The Python side independently checks immutable evidence and strict timing.
-- LEFT JOIN ensures unsettled predictions are visible as typed exclusions.
SELECT
  p.event_prediction_id::text AS prediction_id,
  p.official_event_id,
  p.sport,
  p.market_family,
  p.event_start_time,
  p.created_at,
  p.model_timestamp,
  p.latest_material_update_timestamp,
  p.model_valid_after_latest_update,
  p.home_team,
  p.away_team,
  p.calibrated_home_probability,
  p.probability_publishable,
  p.rank_eligible,
  p.can_execute,
  o.official_winner,
  o.void,
  o.settlement_source,
  o.settlement_timestamp
FROM public.wow_event_predictions AS p
LEFT JOIN public.wow_event_outcomes AS o
  ON o.event_prediction_id = p.event_prediction_id
WHERE p.sport = 'MLB'
  AND p.market_family = 'OUTRIGHT_WINNER'
  AND p.event_start_time < now()
ORDER BY p.event_start_time, p.model_timestamp, p.event_prediction_id;
