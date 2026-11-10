-- WOW V17 model-certification promotion preflight, READ ONLY.
-- Contract: validation schema as observed 2026-11-10, not the imagined v17_* tables.
-- This is an evidence inventory, NOT a promotion transaction, approval, or reducer receipt.
-- Do not infer fitted/calibration certification from a favorable holdout Brier score.
-- Run on the explicitly authorized validation project; production authority is not implied.
WITH requested_team(sport) AS (
    VALUES ('NBA'), ('NCAAB'), ('NCAAF'), ('NFL'), ('WNBA')
),
latest_team_cert AS (
    SELECT DISTINCT ON (sport, league_scope)
        sport, league_scope, certification_id, certification_status,
        independent_verification_status, probability_publishable,
        artifact_id, artifact_sha256, calibration_artifact_id, calibration_sha256
    FROM public.wow_team_event_specialist_certifications
    ORDER BY sport, league_scope, created_at DESC, certification_id DESC
),
basketball_model_counts AS (
    SELECT sport, count(*)::int AS fitted_artifact_count,
           count(*) FILTER (WHERE active AND promoted)::int AS active_promoted_artifacts
    FROM public.wow_basketball_team_event_model_artifacts
    GROUP BY sport
),
basketball_calibrator_counts AS (
    SELECT sport, count(*)::int AS fitted_calibrator_count,
           count(*) FILTER (WHERE active AND promoted)::int AS active_promoted_calibrators
    FROM public.wow_basketball_team_event_calibrators
    GROUP BY sport
),
ncaaf_model_counts AS (
    SELECT count(*)::int AS fitted_artifact_count,
           count(*) FILTER (WHERE active AND promoted)::int AS active_promoted_artifacts
    FROM public.wow_ncaaf_fitted_model_artifacts
),
ncaaf_calibrator_counts AS (
    SELECT count(*)::int AS fitted_calibrator_count,
           count(*) FILTER (WHERE active AND probability_publishable)::int AS active_promoted_calibrators
    FROM public.wow_ncaaf_calibrator_artifacts
),
latest_nfl_health AS (
    SELECT graded_n, minimum_forward_required, status, certification_recommendation
    FROM public.wow_nfl_forward_calibration_health
    ORDER BY generated_at DESC, health_id DESC
    LIMIT 1
),
prop_model_counts AS (
    -- Props certify per exact sport/stat family, never per sport as a whole.
    -- A certified MLB family cannot stand in for a different stat_type.
    SELECT sport, stat_type,
           count(*)::int AS fitted_artifact_count,
           count(*) FILTER (
               WHERE active AND promoted
                 AND lifecycle_state IN ('PROSPECTIVE_CERTIFIED','CHAMPION')
           )::int AS active_promoted_artifacts,
           count(*) FILTER (
               WHERE active AND promoted
                 AND lifecycle_state IN ('PROSPECTIVE_CERTIFIED','CHAMPION')
                 AND supported_line_min IS NOT NULL
                 AND supported_line_max IS NOT NULL
           )::int AS certified_line_range_artifacts
    FROM public.wow_prop_fitted_model_artifacts
    GROUP BY sport, stat_type
),
requested_props(sport) AS (
    VALUES ('MLB'), ('NBA'), ('NFL'), ('WNBA')
)
SELECT rt.sport, 'LLP_TEAM_BETTING_ENGINE'::text AS routed_lane,
       'MONEYLINE'::text AS market_key,
       CASE WHEN rt.sport IN ('NBA','WNBA') THEN COALESCE(bm.fitted_artifact_count,0)
            WHEN rt.sport = 'NCAAF' THEN nm.fitted_artifact_count
            ELSE NULL::int END AS fitted_artifact_count,
       CASE WHEN rt.sport IN ('NBA','WNBA') THEN COALESCE(bm.active_promoted_artifacts,0)
            WHEN rt.sport = 'NCAAF' THEN nm.active_promoted_artifacts
            ELSE NULL::int END AS active_promoted_artifacts,
       CASE WHEN rt.sport IN ('NBA','WNBA') THEN COALESCE(bc.fitted_calibrator_count,0)
            WHEN rt.sport = 'NCAAF' THEN nc.fitted_calibrator_count
            ELSE NULL::int END AS fitted_calibrator_count,
       cert.certification_id::text AS immutable_certification_id,
       cert.certification_status AS immutable_certification_status,
       cert.independent_verification_status,
       bs.capability_status AS basketball_candidate_status,
       bs.reason_code AS basketball_candidate_reason,
       CASE WHEN rt.sport = 'NFL'
                 AND (nh.graded_n IS NULL
                      OR nh.minimum_forward_required IS NULL
                      OR nh.certification_recommendation IS DISTINCT FROM 'FORWARD_EVIDENCE_PASS'
                      OR nh.graded_n < nh.minimum_forward_required)
            THEN 'DO_NOT_CERTIFY_YET'
            WHEN cert.certification_id IS NULL
            THEN 'TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED'
            WHEN cert.certification_status <> 'CERTIFIED'
                 OR cert.independent_verification_status <> 'PASS'
                 OR cert.probability_publishable IS NOT TRUE
                 OR NULLIF(cert.artifact_sha256,'') IS NULL
                 OR NULLIF(cert.calibration_sha256,'') IS NULL
            THEN 'TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED'
            WHEN bs.capability_status = 'SHADOW'
                 OR bs.reason_code = 'CERTIFICATION_REPLAY_REQUIRED'
            THEN 'CERTIFICATION_REPLAY_REQUIRED'
            ELSE 'RECEIPT_PRESENT_REQUIRES_ROUTE_REPLAY'
       END AS preflight_finding,
       FALSE AS preflight_authorizes_publication,
       FALSE AS can_execute,
       NULL::text AS prop_stat_family
FROM requested_team rt
LEFT JOIN latest_team_cert cert ON cert.sport = rt.sport AND cert.league_scope = rt.sport
LEFT JOIN basketball_model_counts bm ON bm.sport = rt.sport
LEFT JOIN basketball_calibrator_counts bc ON bc.sport = rt.sport
LEFT JOIN public.wow_basketball_team_event_certification bs ON bs.sport = rt.sport
CROSS JOIN ncaaf_model_counts nm
CROSS JOIN ncaaf_calibrator_counts nc
LEFT JOIN latest_nfl_health nh ON rt.sport='NFL'
UNION ALL
SELECT rp.sport, 'WOW_PROP_LANE'::text, 'EXACT_LINE_PER_STAT'::text,
       COALESCE(pm.fitted_artifact_count,0),
       COALESCE(pm.active_promoted_artifacts,0),
       NULL::int, NULL::text, NULL::text, NULL::text,
       NULL::text, NULL::text,
       CASE WHEN pm.stat_type IS NULL
                 OR COALESCE(pm.active_promoted_artifacts,0) = 0
            THEN 'PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND'
            WHEN COALESCE(pm.certified_line_range_artifacts,0) = 0
            THEN 'PROP_EXACT_LINE_RANGE_NOT_CERTIFIED'
            ELSE 'PROP_REGISTRY_FLAGS_PRESENT_REQUIRES_CERTIFIED_ROUTE_REPLAY'
       END,
       FALSE AS preflight_authorizes_publication,
       FALSE AS can_execute,
       pm.stat_type::text AS prop_stat_family
FROM requested_props rp
LEFT JOIN prop_model_counts pm ON upper(pm.sport)=upper(rp.sport)
ORDER BY routed_lane, sport, prop_stat_family;
