-- Exact-version NFL immutable forward cohort producer-receipt inventory.
-- READ ONLY: structural checks only, not cryptographic digest validation,
-- scorer transform replay, independent model certification or publication.
-- Missing historical receipts are NOT reconstructed or retroactively added.
WITH linked AS (
  SELECT g.model_version, g.event_prediction_id, g.official_event_id,
         p.event_prediction_id AS prediction_record_id,
         p.model_artifact_id::text AS model_artifact_id,
         p.feature_row_hash, p.source_snapshot_id::text AS source_snapshot_id,
         p.calibration_version, p.calibration_method,
         p.model_timestamp, p.source_snapshot_timestamp, p.event_start_time_utc,
         p.model_output_snapshot->'calibration_operation_receipt' AS receipt
  FROM public.wow_nfl_forward_verified_grades g
  LEFT JOIN public.wow_nfl_event_predictions p
    ON p.event_prediction_id = g.event_prediction_id
), checked AS (
  SELECT *,
         (receipt IS NULL) AS receipt_missing,
         (receipt IS NOT NULL) AS receipt_present,
         (receipt IS NOT NULL AND (
            jsonb_typeof(receipt) IS DISTINCT FROM 'object'
            OR receipt->>'schema_version' IS DISTINCT FROM 'NFL_CALIBRATION_OPERATION_V1'
            OR receipt->>'producer' IS DISTINCT FROM 'nfl_event_model_v17.score_feature_row'
            OR receipt->>'status' IS DISTINCT FROM 'COMPLETED'
            OR receipt->>'can_execute' IS DISTINCT FROM 'false'
         )) AS receipt_schema_mismatch,
         (receipt IS NOT NULL AND (
            receipt->>'model_version' IS DISTINCT FROM model_version
            OR receipt->>'official_event_id' IS DISTINCT FROM official_event_id
            OR receipt->>'model_artifact_id' IS DISTINCT FROM model_artifact_id
            OR receipt->>'feature_row_hash' IS DISTINCT FROM feature_row_hash
            OR receipt->>'source_snapshot_id' IS DISTINCT FROM source_snapshot_id
            OR receipt->>'calibration_version' IS DISTINCT FROM calibration_version
            OR receipt->>'calibration_method' IS DISTINCT FROM calibration_method
         )) AS receipt_identity_mismatch,
         (receipt IS NOT NULL AND (
            receipt->>'evidence_sha256' IS NULL
            OR receipt->>'evidence_sha256' !~ '^[a-f0-9]{64}$'
         )) AS receipt_digest_field_malformed
  FROM linked
)
SELECT model_version, count(*)::int AS total_verified_grades,
       count(*) FILTER (WHERE prediction_record_id IS NULL)::int AS missing_prediction_rows,
       count(*) FILTER (WHERE receipt_missing)::int AS historical_rows_without_receipt,
       count(*) FILTER (WHERE receipt_present)::int AS newer_rows_with_receipt,
       count(*) FILTER (WHERE receipt_schema_mismatch)::int AS receipt_schema_mismatches,
       count(*) FILTER (WHERE receipt_identity_mismatch)::int AS receipt_identity_mismatches,
       count(*) FILTER (WHERE receipt_digest_field_malformed)::int AS receipt_digest_field_malformed_count,
       CASE WHEN count(*) FILTER (WHERE prediction_record_id IS NULL OR receipt_schema_mismatch OR receipt_identity_mismatch OR receipt_digest_field_malformed)>0
            THEN 'PRODUCER_RECEIPT_STRUCTURAL_GAPS'
            WHEN count(*) FILTER (WHERE receipt_missing)>0
            THEN 'LEGACY_RECEIPT_ERA_SPLIT_REQUIRES_INDEPENDENT_POLICY_REVIEW'
            ELSE 'RECEIPTS_PRESENT_REQUIRES_CANONICAL_PYTHON_VERIFICATION'
       END AS research_finding,
       FALSE AS producer_receipt_digest_verified,
       FALSE AS actual_producer_receipt_validated,
       FALSE AS certification_authorized,
       FALSE AS probability_publishable,
       FALSE AS can_execute
FROM checked
GROUP BY model_version
ORDER BY total_verified_grades DESC, model_version;
