-- Read-only point-in-time summary of Kalshi Weather shadow ledger.
-- Use a read-only DB role; no DDL/DML; Supabase validation is not a release-control authority.
-- Supabase SQL output is one row with a JSONB `report` object.
SELECT jsonb_build_object(
  'as_of', transaction_timestamp(),
  'counts', jsonb_build_object(
    'rules', (SELECT count(*) FROM public.wow_kalshi_weather_contract_rules),
    'rules_with_settlement_source', (SELECT count(*) FROM public.wow_kalshi_weather_contract_rules WHERE NULLIF(trim(settlement_source_name), '') IS NOT NULL),
    'sources', (SELECT count(*) FROM public.wow_kalshi_weather_source_snapshots),
    'predictions', (SELECT count(*) FROM public.wow_kalshi_weather_predictions),
    'numeric_probability_pairs', (SELECT count(*) FROM public.wow_kalshi_weather_predictions WHERE p_yes BETWEEN 0 AND 1 AND p_no BETWEEN 0 AND 1 AND abs(p_yes + p_no - 1) <= 0.00001),
    'publishable_predictions', (SELECT count(*) FROM public.wow_kalshi_weather_predictions WHERE probability_publishable IS TRUE),
    'calibration_profiles', (SELECT count(*) FROM public.wow_kalshi_weather_calibration_profiles),
    'certified_calibration_profiles', (SELECT count(*) FROM public.wow_kalshi_weather_calibration_profiles WHERE certified IS TRUE AND certification_evidence IS NOT NULL),
    'outcomes', (SELECT count(*) FROM public.wow_kalshi_weather_outcomes),
    'scored_outcomes', (SELECT count(*) FROM public.wow_kalshi_weather_outcomes WHERE brier_score IS NOT NULL),
    'market_snapshots', (SELECT count(*) FROM public.wow_kalshi_weather_market_snapshots),
    'friction_verified_snapshots', (SELECT count(*) FROM public.wow_kalshi_weather_market_snapshots WHERE friction_model_verified IS TRUE)
  ),
  'latest', jsonb_build_object(
    'rules', (SELECT max(acquired_at) FROM public.wow_kalshi_weather_contract_rules),
    'sources', (SELECT max(retrieved_at) FROM public.wow_kalshi_weather_source_snapshots),
    'predictions', (SELECT max(decision_time) FROM public.wow_kalshi_weather_predictions),
    'outcomes', (SELECT max(settled_at) FROM public.wow_kalshi_weather_outcomes),
    'market_snapshots', (SELECT max(retrieved_at) FROM public.wow_kalshi_weather_market_snapshots)
  )
) AS report;
