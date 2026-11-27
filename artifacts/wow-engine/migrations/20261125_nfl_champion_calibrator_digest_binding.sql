-- PROTECTED Class C provenance | Incident #1939 | migration candidate, NOT APPLIED
-- Owner decision 2026-11-25 01:20 UTC (Greg): bind the existing NFL champion
-- calibrator to its content-addressed artifact digest so the champion's
-- forward evidence can be independently certified. Apply only with Greg's
-- approval of the exact PR head.
--
-- What this does: adds exactly two keys to the champion calibrator's
-- fit_metrics_json, the same keys #2825 writes for future fits:
--   calibration_artifact_payload  (schema NFL_PLATT_CALIBRATION_ARTIFACT_V1)
--   calibration_artifact_sha256   (sha256 of the canonical payload text)
-- The payload was reconstructed from stored rows only (artifact payload,
-- calibrator row, and the training dataset whose hash reproduces
-- 5d603a87...345a); see claude/plans/2026-11-24-nfl-champion-calibrator-digest-decision.md.
--
-- What this does not do: change coefficients, bounds, thresholds, probability,
-- lifecycle, the terminal reducer, or publication. It issues no certificate.
-- Rows stay MODEL_QUALIFIED_HOLD until an independent certificate whose
-- calibration_sha256 equals this digest exists. can_execute stays false.
--
-- Fails closed (no change) unless exactly one row matches every guard, the
-- digest is not already present, and the payload text hashes to the digest.
-- Rollback: 20261125_nfl_champion_calibrator_digest_binding_rollback.sql
-- (refuses once any certificate references the digest).
do $migration$
declare
  v_text constant text := $cal${"bounds_method_version":"CALIBRATION_HOLDOUT_ERROR_MARGIN_V1","calibration_method":"PLATT_TIME_SPLIT_V1","calibration_rows":285,"calibration_version":"NFL_PLATT_TIME_SPLIT_V1_5d603a873fbb","fit_end":"2025-02-09","fit_start":"2021-10-07","model_artifact_sha256":"aa41eefbbe93f94f9af0401a2c0a7bd9c9505569e8c3754ac396d552a0ead04b","model_artifact_version":"NFL_EVENT_LOGREG_PLATT_V1_5d603a873fbb","platt_a":0.9358407245138914,"platt_b":-0.05740265013467314,"schema_version":"NFL_PLATT_CALIBRATION_ARTIFACT_V1","temporal_split_sha256":"5079cace62c021c7b5be2b8e04aacc6ca9cfa2ff06b7ff9ceb478419d9c43768","training_code_sha":"fa26799bf294f1bd63b7d5643c8a06a3a44a2f97","training_dataset_sha256":"5d603a873fbb5f9885256a3e4100f16b8c3654a060d60b2d5190793fcc0e345a","uncertainty_margin":0.05591808310857731,"validation_metrics_sha256":"19221d6a42fdafb54984f37f8b36a3c6969940a56e07fa42aa8093a2bba54e91"}$cal$;
  v_sha constant text := '21b6cc797f02f27088ed6e2fd674c465dd2d7b21d29e6e88835b7727923eb880';
  v_rows integer;
  v_readback text;
begin
  if encode(sha256(convert_to(v_text, 'UTF8')), 'hex') <> v_sha then
    raise exception 'NFL_CALIBRATOR_DIGEST_SELF_CHECK_FAILED';
  end if;

  update public.wow_calibrators c
     set fit_metrics_json = c.fit_metrics_json
         || jsonb_build_object(
              'calibration_artifact_payload', v_text::jsonb,
              'calibration_artifact_sha256', v_sha)
   where c.calibrator_id = '756379bd-f557-4508-8ecb-91a5d336ec64'
     and c.active is true
     and c.promoted is true
     and c.platt_a = 0.9358407245138914
     and c.platt_b = -0.05740265013467314
     and c.source_data_hash = '5d603a873fbb5f9885256a3e4100f16b8c3654a060d60b2d5190793fcc0e345a'
     and c.split_hash = '5079cace62c021c7b5be2b8e04aacc6ca9cfa2ff06b7ff9ceb478419d9c43768'
     and c.training_n = 285
     and not (c.fit_metrics_json ? 'calibration_artifact_sha256')
     and not (c.fit_metrics_json ? 'calibration_artifact_payload')
     and exists (
       select 1
         from public.wow_nfl_event_fitted_model_artifacts a
        where a.calibrator_id = c.calibrator_id
          and a.artifact_id = '7fee1209-0bed-4bd0-aa0f-fc7a881eff80'
          and a.artifact_checksum = 'aa41eefbbe93f94f9af0401a2c0a7bd9c9505569e8c3754ac396d552a0ead04b'
          and a.training_dataset_hash = '5d603a873fbb5f9885256a3e4100f16b8c3654a060d60b2d5190793fcc0e345a'
          and a.training_code_sha = 'fa26799bf294f1bd63b7d5643c8a06a3a44a2f97'
          and a.lifecycle_state = 'CHAMPION'
          and a.active is true
          and a.can_execute is false);
  get diagnostics v_rows = row_count;
  if v_rows <> 1 then
    raise exception 'NFL_CALIBRATOR_DIGEST_BINDING_PRECONDITION_FAILED:%', v_rows;
  end if;

  select fit_metrics_json->>'calibration_artifact_sha256' into v_readback
    from public.wow_calibrators
   where calibrator_id = '756379bd-f557-4508-8ecb-91a5d336ec64';
  if v_readback is distinct from v_sha then
    raise exception 'NFL_CALIBRATOR_DIGEST_READBACK_MISMATCH';
  end if;
end
$migration$;
