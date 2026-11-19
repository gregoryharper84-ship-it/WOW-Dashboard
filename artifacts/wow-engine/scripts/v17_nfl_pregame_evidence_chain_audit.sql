-- WOW V17 NFL exact-version pregame evidence-chain audit (read only).
-- A favorable row is NOT certification, promotion, terminal approval, or production proof.
-- Run only on an authorized validation database; do not turn audit output into a scorer flag.
WITH latest_health AS (
  SELECT generated_at, graded_n, minimum_forward_required, status,
         certification_recommendation, brier, log_loss, ece,
         calibration_bias, lower_bound_reliability_gap
  FROM public.wow_nfl_forward_calibration_health
  ORDER BY generated_at DESC, health_id DESC LIMIT 1
), linked AS (
  SELECT g.model_version, g.official_event_id, g.calibrated_probability,
         g.calibrated_lower_bound, g.outcome, g.brier, g.log_loss,
         (p.event_prediction_id IS NOT NULL
          AND p.official_event_id = g.official_event_id
          AND p.model_version = g.model_version
          AND p.created_at = g.prediction_created_at
          AND p.model_artifact_id = g.model_artifact_id
          AND p.can_execute IS FALSE
          AND p.model_package_valid IS TRUE
          AND p.provenance_complete IS TRUE
          AND p.model_probability_publishable IS TRUE) AS receipt_identity_ok,
         (g.prediction_created_at < g.event_start_time_utc
          AND p.model_timestamp < g.event_start_time_utc
          AND p.model_timestamp >= p.latest_material_update_timestamp
          AND p.model_timestamp >= p.source_snapshot_timestamp) AS pregame_timing_ok,
         (s.snapshot_id IS NOT NULL AND s.source_status = 'CAPTURED'
          AND s.fetched_at <= p.model_timestamp
          AND NULLIF(p.feature_row_hash,'') IS NOT NULL
          AND NULLIF(p.feature_order_hash,'') IS NOT NULL) AS source_feature_link_ok,
         (a.artifact_id IS NOT NULL
          AND a.model_artifact_version = g.model_version
          AND g.artifact_checksum = a.artifact_checksum
          AND a.active IS TRUE AND a.promoted IS TRUE) AS fitted_artifact_link_ok,
         (c.calibrator_id IS NOT NULL AND c.active IS TRUE
          AND c.promoted IS TRUE AND c.validation_status = 'PASS'
          AND c.health_status = 'PASS'
          AND c.sport = 'NFL'
          AND c.market_family = 'OUTRIGHT_WINNER'
          AND c.model_family = a.model_family) AS calibrator_link_ok,
         EXISTS (
           SELECT 1 FROM public.wow_team_event_specialist_certifications cert
           WHERE cert.sport = 'NFL' AND cert.league_scope = 'NFL'
             AND cert.model_version = g.model_version
             AND cert.artifact_id = a.artifact_id::text
             AND cert.artifact_sha256 = a.artifact_checksum
             AND cert.certification_status = 'CERTIFIED'
             AND cert.independent_verification_status = 'PASS'
             AND cert.probability_publishable IS TRUE
             AND cert.can_execute IS FALSE
             AND NULLIF(cert.calibration_sha256,'') IS NOT NULL
         ) AS independent_certification_receipt_ok
  FROM public.wow_nfl_forward_verified_grades g
  LEFT JOIN public.wow_nfl_event_predictions p
    ON p.event_prediction_id = g.event_prediction_id
  LEFT JOIN public.wow_nfl_source_snapshots s
    ON s.snapshot_id = p.source_snapshot_id
  LEFT JOIN public.wow_nfl_event_fitted_model_artifacts a
    ON a.artifact_id = g.model_artifact_id
  LEFT JOIN public.wow_calibrators c
    ON c.calibrator_id = a.calibrator_id
), per_version AS (
  SELECT model_version,
         count(*)::int AS verified_grades,
         count(DISTINCT official_event_id)::int AS distinct_events,
         round(avg(brier)::numeric,6) AS brier,
         round(avg(log_loss)::numeric,6) AS log_loss,
         round(avg(calibrated_probability)::numeric,6) AS mean_predicted,
         round(avg(outcome)::numeric,6) AS observed_hit_rate,
         round((avg(calibrated_probability)-avg(outcome))::numeric,6) AS calibration_bias,
         round((avg(outcome)-avg(calibrated_lower_bound))::numeric,6) AS lower_bound_reliability_gap,
         count(*) FILTER (WHERE receipt_identity_ok IS NOT TRUE)::int AS invalid_prediction_receipts,
         count(*) FILTER (WHERE pregame_timing_ok IS NOT TRUE)::int AS timing_or_freshness_gaps,
         count(*) FILTER (WHERE source_feature_link_ok IS NOT TRUE)::int AS source_feature_link_gaps,
         count(*) FILTER (WHERE fitted_artifact_link_ok IS NOT TRUE)::int AS fitted_artifact_link_gaps,
         count(*) FILTER (WHERE calibrator_link_ok IS NOT TRUE)::int AS calibrator_link_gaps,
         count(*) FILTER (WHERE independent_certification_receipt_ok IS NOT TRUE)::int AS certification_receipt_gaps
  FROM linked
  GROUP BY model_version
)
SELECT pv.*, h.generated_at AS global_health_generated_at,
       h.graded_n AS global_health_graded_n,
       h.status AS global_health_status,
       h.certification_recommendation AS global_certification_recommendation,
       CASE
         WHEN pv.invalid_prediction_receipts > 0
           OR pv.timing_or_freshness_gaps > 0
           OR pv.source_feature_link_gaps > 0
           OR pv.fitted_artifact_link_gaps > 0
           OR pv.calibrator_link_gaps > 0
           THEN 'PREGAME_EVIDENCE_CHAIN_GAPS'
         WHEN pv.certification_receipt_gaps > 0
           THEN 'INDEPENDENT_CERTIFICATION_RECEIPT_UNPROVEN'
         ELSE 'RECEIPTS_PRESENT_REQUIRES_PRODUCTION_ROUTE_REPLAY'
       END AS audit_finding,
       FALSE AS audit_authorizes_certification,
       FALSE AS audit_authorizes_publication,
       FALSE AS can_execute
FROM per_version pv CROSS JOIN latest_health h
ORDER BY pv.verified_grades DESC, pv.model_version;
