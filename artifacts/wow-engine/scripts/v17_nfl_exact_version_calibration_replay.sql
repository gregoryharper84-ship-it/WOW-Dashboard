-- Replay exact-version NFL forward calibration metrics from immutable verified grades.
-- READ ONLY. This is model-quality evidence, NOT independent certification or approval.
WITH rows AS (
  SELECT model_version, official_event_id, event_prediction_id, calibrated_probability AS p,
         calibrated_lower_bound AS lower_bound, outcome AS y,
         brier AS stored_brier, log_loss AS stored_log_loss,
         (calibrated_probability > 0 AND calibrated_probability < 1
          AND outcome IN (0,1)
          AND calibrated_lower_bound BETWEEN 0 AND calibrated_probability
         ) AS valid_probability
  FROM public.wow_nfl_forward_verified_grades
), scored AS (
  SELECT *,
         CASE WHEN valid_probability IS TRUE
              THEN power(p-y,2) END AS replay_brier,
         CASE WHEN valid_probability IS TRUE
              THEN -(y*ln(p)+(1-y)*ln(1-p)) END AS replay_log_loss,
         CASE WHEN valid_probability IS TRUE
              THEN least(9,floor(p*10)::int) END AS decile
  FROM rows
), bins AS (
  SELECT model_version, decile, count(*)::double precision AS n,
         avg(p) AS mean_p, avg(y::double precision) AS win_rate
  FROM scored WHERE valid_probability IS TRUE
  GROUP BY model_version,decile
), counts AS (
  SELECT model_version,count(*)::double precision AS n
  FROM scored WHERE valid_probability IS TRUE GROUP BY model_version
), ece AS (
  SELECT bins.model_version,
         sum(bins.n / counts.n * abs(bins.mean_p-bins.win_rate)) AS replay_ece
  FROM bins JOIN counts USING(model_version)
  GROUP BY bins.model_version
), metrics AS (
  SELECT model_version, count(*)::int AS grades,
         count(DISTINCT official_event_id)::int AS events,
         count(DISTINCT event_prediction_id)::int AS predictions,
         count(*) FILTER (WHERE NULLIF(btrim(model_version),'') IS NULL)::int AS missing_model_versions,
         count(*) FILTER (WHERE valid_probability IS NOT TRUE)::int AS invalid_probabilities,
         count(*) FILTER (WHERE valid_probability IS TRUE AND
                   (abs(replay_brier-stored_brier) > 1e-9 OR
                    abs(replay_log_loss-stored_log_loss) > 1e-9 OR
                    stored_brier IS NULL OR stored_log_loss IS NULL))::int AS metric_row_mismatches,
         avg(replay_brier) AS replay_brier,
         avg(replay_log_loss) AS replay_log_loss,
         avg(p) FILTER (WHERE valid_probability IS TRUE) AS mean_probability,
         avg(y::double precision) FILTER (WHERE valid_probability IS TRUE) AS observed_hit_rate,
         avg(p-y) FILTER (WHERE valid_probability IS TRUE) AS calibration_bias,
         avg(y-lower_bound) FILTER (WHERE valid_probability IS TRUE) AS lower_bound_reliability_gap
  FROM scored GROUP BY model_version
)
SELECT metrics.*, ece.replay_ece,
       CASE WHEN missing_model_versions <> 0 OR grades <> events OR grades <> predictions
                 OR invalid_probabilities <> 0 OR metric_row_mismatches <> 0
            THEN 'EXACT_VERSION_REPLAY_INPUT_OR_METRIC_CONFLICT'
            WHEN grades < 100 THEN 'EXACT_VERSION_FORWARD_SAMPLE_INSUFFICIENT'
            WHEN replay_brier >= 0.25 OR replay_log_loss >= ln(2)
              OR replay_ece > 0.12 OR abs(calibration_bias) > 0.08
              OR lower_bound_reliability_gap < -0.03
            THEN 'EXACT_VERSION_CALIBRATION_METRIC_THRESHOLD_NOT_MET'
            ELSE 'EXACT_VERSION_METRICS_WITHIN_EXISTING_THRESHOLDS_REQUIRES_INDEPENDENT_REVIEW'
       END AS research_finding,
       FALSE AS certification_approved, FALSE AS probability_publishable,
       FALSE AS can_execute
FROM metrics LEFT JOIN ece USING(model_version)
ORDER BY grades DESC, model_version;
