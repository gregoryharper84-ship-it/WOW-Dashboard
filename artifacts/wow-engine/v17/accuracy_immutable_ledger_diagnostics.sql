-- WOW V17 research-only ledger diagnostics (PostgreSQL / Supabase).
-- SELECT only. No model training, probabilities, registry, terminal or wager writes.
-- Requires read-only, authorized access. SOURCE AUTHENTICITY and exact official
-- stats should still be independently verified before certification.
-- A completed model cannot be assumed from a saved market/implied probability.
WITH outcome_counts AS (
    SELECT prediction_id, count(*) AS n
    FROM public.wow_outcomes
    GROUP BY prediction_id
),
joined AS (
    SELECT
        p.prediction_id, p.sport, p.stat_type,
        p.model_artifact_version, p.model_artifact_checksum,
        p.specialist_version, p.calibration_version,
        p.event_start_time, p.model_timestamp,
        p.created_at, p.locked_at,
        p.source_snapshot_id,
        p.calibrated_probability AS prob,
        p.calibrated_probability_lower_bound AS lower_bound,
        COALESCE(c.n, 0) AS outcome_count,
        o.official_result, o.hit, o.push, o.void,
        o.settlement_source, o.settlement_timestamp,
        CASE
            WHEN p.created_at IS NULL OR p.model_timestamp IS NULL
                 OR p.locked_at IS NULL OR p.event_start_time IS NULL
                 OR p.created_at >= p.event_start_time
                 OR p.model_timestamp >= p.event_start_time
                 OR p.locked_at > p.event_start_time
                 OR p.source_snapshot_id IS NULL THEN 'INVALID_PREGAME_LOCK'
            WHEN p.model_artifact_version IS NULL OR p.model_artifact_checksum IS NULL
                 OR p.specialist_version IS NULL THEN 'MISSING_MODEL_LINEAGE'
            WHEN p.calibrated_probability IS NULL
                 OR p.calibrated_probability NOT BETWEEN 0 AND 1
                 OR p.calibrated_probability_lower_bound IS NULL
                 OR p.calibrated_probability_lower_bound NOT BETWEEN 0 AND p.calibrated_probability
                 THEN 'MISSING_OR_INVALID_CALIBRATED_PACKAGE'
            WHEN COALESCE(c.n, 0) <> 1 THEN 'OFFICIAL_SETTLEMENT_NOT_UNIQUE'
            WHEN o.settlement_timestamp IS NULL OR o.settlement_timestamp < p.event_start_time
                 OR nullif(trim(o.settlement_source), '') IS NULL
                 THEN 'SETTLEMENT_PROVENANCE_INCOMPLETE'
            WHEN o.push IS TRUE OR o.void IS TRUE
                 THEN 'EXCLUDED_PUSH_OR_VOID'
            WHEN o.official_result NOT IN ('HIT', 'MISS')
                 OR o.hit IS DISTINCT FROM (o.official_result = 'HIT')
                 THEN 'SETTLEMENT_OUTCOME_CONFLICT'
            ELSE 'RECONCILED_LEDGER_GRADE'
        END AS audit_code
    FROM public.wow_predictions AS p
    LEFT JOIN outcome_counts AS c ON c.prediction_id = p.prediction_id
    LEFT JOIN public.wow_outcomes AS o
      ON o.prediction_id = p.prediction_id AND c.n = 1
),
graded AS (
    SELECT *,
       CASE WHEN audit_code = 'RECONCILED_LEDGER_GRADE'
            THEN CASE WHEN official_result = 'HIT' THEN 1.0 ELSE 0.0 END
            ELSE NULL END AS outcome
    FROM joined
)
SELECT
    sport, stat_type,
    COALESCE(model_artifact_version, 'UNLINKED') AS model_artifact_version,
    count(*) AS prediction_rows,
    count(*) FILTER (WHERE audit_code = 'RECONCILED_LEDGER_GRADE') AS graded_rows,
    count(*) FILTER (WHERE audit_code <> 'RECONCILED_LEDGER_GRADE') AS held_rows,
    count(*) FILTER (WHERE audit_code = 'INVALID_PREGAME_LOCK') AS invalid_pregame,
    count(*) FILTER (WHERE audit_code = 'MISSING_MODEL_LINEAGE') AS missing_lineage,
    count(*) FILTER (WHERE audit_code = 'MISSING_OR_INVALID_CALIBRATED_PACKAGE') AS invalid_calibration,
    count(*) FILTER (WHERE audit_code = 'OFFICIAL_SETTLEMENT_NOT_UNIQUE') AS nonunique_outcome,
    count(*) FILTER (WHERE audit_code = 'SETTLEMENT_PROVENANCE_INCOMPLETE') AS source_gaps,
    count(*) FILTER (WHERE audit_code = 'EXCLUDED_PUSH_OR_VOID') AS push_void,
    count(*) FILTER (WHERE audit_code = 'SETTLEMENT_OUTCOME_CONFLICT') AS settlement_conflict,
    round(avg(power(prob - outcome, 2)) FILTER
        (WHERE audit_code = 'RECONCILED_LEDGER_GRADE'), 8) AS brier,
    round(avg(-outcome * ln(greatest(prob, 0.000000000001))
        -(1-outcome) * ln(greatest(1-prob, 0.000000000001))) FILTER
        (WHERE audit_code = 'RECONCILED_LEDGER_GRADE'), 8) AS log_loss,
    round(avg(prob - outcome) FILTER
        (WHERE audit_code = 'RECONCILED_LEDGER_GRADE'), 8) AS signed_calibration_bias
FROM graded
GROUP BY sport, stat_type, COALESCE(model_artifact_version, 'UNLINKED')
ORDER BY sport, stat_type, model_artifact_version;
