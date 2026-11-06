-- P0 V17 SOURCE CANDIDATE. NOT AN ACTIVE MIGRATION. NOT DEPLOYED.
-- NFL ONLY. Reducer-owned authenticated receipt producer + private origin ledger.
--
-- This file closes the trust-root gap that the admission candidate deliberately
-- leaves open (LLP_REDUCER_ORIGIN_RECEIPT_UNAVAILABLE):
--   * public.wow_v17_admit_nfl_event_receipt  (consumer, already merged in source)
--   * public.wow_v17_reduce_nfl_event_terminal_label  (must EMIT the receipt)
--   * wow_terminal_reducer.origin_receipts    (immutable reducer-owned ledger)
--   * wow_terminal_reducer.verify_nfl_reducer_receipt(uuid,uuid,text) (attestor)
--
-- The admission layer is NOT modified here and still never mints receipt
-- identity or digest. Identity is minted exactly once by the reducer's own
-- ledger; every later call returns the same identity/digest only while the
-- terminal reduction payload is byte-identical. A material change after
-- issuance fails closed instead of silently re-binding a new receipt.
--
-- INDEPENDENCE / ACCEPTANCE STATUS:
--   * Private schema is not readable or writable by anon/authenticated/
--     service_role. Only the migration owner and the SECURITY DEFINER
--     reducer/attestor run with access.
--   * This file does NOT constitute independent SIRT acceptance, a model
--     certification, a deployment, or permission to publish probabilities.
--   * Nothing here enables live execution: can_execute remains false.
--
-- Requires: core PostgreSQL 11+ (pg_catalog.sha256) and a migration owner
-- that can own a private schema. No extension dependency on the trust root.
BEGIN;

CREATE SCHEMA IF NOT EXISTS wow_terminal_reducer;

-- Append-only reducer origin ledger. One receipt per source score snapshot.
CREATE TABLE IF NOT EXISTS wow_terminal_reducer.origin_receipts (
  score_snapshot_id uuid PRIMARY KEY,
  reducer_receipt_id uuid NOT NULL UNIQUE DEFAULT pg_catalog.gen_random_uuid(),
  reducer_receipt_sha256 text NOT NULL UNIQUE
    CHECK (reducer_receipt_sha256 ~ '^[0-9a-f]{64}$'),
  reduction_payload jsonb NOT NULL,
  terminal_reduced_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp(),
  issued_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp(),
  issuer text NOT NULL DEFAULT 'V17_TERMINAL_REDUCER'
    CHECK (issuer = 'V17_TERMINAL_REDUCER'),
  -- The ledger may only ever hold a genuinely approved terminal reduction,
  -- and never a payload that already carries receipt fields (no recursion).
  CONSTRAINT wow_tr_origin_pass_only CHECK (
    reduction_payload->>'status' = 'PASS'
    AND reduction_payload->>'terminal_label' = 'FINAL_APPROVED'
    AND reduction_payload->>'global_terminal_reducer' = 'V17_TERMINAL_REDUCER'
    AND reduction_payload->>'probability_publishable' = 'true'
    AND reduction_payload->>'rank_eligible' = 'true'
    AND reduction_payload->>'can_execute' = 'false'
    AND NOT (reduction_payload ? 'reducer_receipt_id')
    AND NOT (reduction_payload ? 'reducer_receipt_sha256')
  )
);

CREATE OR REPLACE FUNCTION wow_terminal_reducer.reject_mutation()
RETURNS trigger LANGUAGE plpgsql SET search_path = '' AS $f$
BEGIN
  RAISE EXCEPTION 'WOW_TERMINAL_REDUCER_ORIGIN_RECEIPT_IMMUTABLE'
    USING ERRCODE = '55000';
END $f$;

CREATE OR REPLACE TRIGGER trg_wow_tr_origin_receipts_immutable
  BEFORE UPDATE OR DELETE ON wow_terminal_reducer.origin_receipts
  FOR EACH ROW EXECUTE FUNCTION wow_terminal_reducer.reject_mutation();

CREATE OR REPLACE TRIGGER trg_wow_tr_origin_receipts_no_truncate
  BEFORE TRUNCATE ON wow_terminal_reducer.origin_receipts
  FOR EACH STATEMENT EXECUTE FUNCTION wow_terminal_reducer.reject_mutation();

-- Single canonical digest definition shared by issuer and attestor. Binds the
-- source score snapshot AND the exact terminal reduction payload, so a ledger
-- row cannot be replayed onto a different score or a later material state.
CREATE OR REPLACE FUNCTION wow_terminal_reducer.nfl_receipt_digest(
  p_score_snapshot_id uuid,
  p_reduction jsonb
)
RETURNS text LANGUAGE sql IMMUTABLE SET search_path = '' AS $f$
  -- pg_catalog.sha256 is core PostgreSQL (11+); the trust root does not
  -- depend on the pgcrypto extension being installed.
  SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
    pg_catalog.jsonb_build_object(
      'purpose', 'WOW_V17_NFL_TERMINAL_REDUCER_RECEIPT_V1',
      'score_snapshot_id', p_score_snapshot_id::text,
      'reduction', p_reduction
    )::text, 'UTF8')), 'hex')
$f$;

-- Pure terminal reduction. Gate logic is carried over verbatim from the
-- deployed 20260913 definition; this function never mints receipt identity.
CREATE OR REPLACE FUNCTION wow_terminal_reducer.reduce_nfl_event_terminal_label(
  p_score_snapshot_id uuid
)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path to ''
AS $function$
declare
  r public.wow_nfl_event_predictions%rowtype;
  a public.wow_nfl_event_fitted_model_artifacts%rowtype;
  c public.wow_calibrators%rowtype;
  v_blockers text[] := '{}';
  v_label text := 'MODEL_QUALIFIED_HOLD';
begin
  select * into r from public.wow_nfl_event_predictions where score_snapshot_id=p_score_snapshot_id;
  if not found then
    return jsonb_build_object('status','HOLD','terminal_label','MODEL_QUALIFIED_HOLD','blockers',jsonb_build_array('NFL_SCORE_SNAPSHOT_NOT_FOUND'),'global_terminal_reducer','V17_TERMINAL_REDUCER','probability_publishable',false,'rank_eligible',false,'can_execute',false);
  end if;
  select * into a from public.wow_nfl_event_fitted_model_artifacts where artifact_id=r.model_artifact_id;
  select * into c from public.wow_calibrators where calibrator_id=a.calibrator_id;

  if r.event_start_time_utc <= now() then v_blockers:=array_append(v_blockers,'EVENT_NOT_PREGAME'); end if;
  if not r.model_package_valid then v_blockers:=array_append(v_blockers,'MODEL_PACKAGE_INVALID'); end if;
  if not r.provenance_complete then v_blockers:=array_append(v_blockers,'PROVENANCE_INCOMPLETE'); end if;
  if not r.model_probability_publishable then v_blockers:=array_append(v_blockers,'MODEL_PROBABILITY_NOT_PUBLISHABLE'); end if;
  if r.calibration_health_status <> 'PASS' then v_blockers:=array_append(v_blockers,'CALIBRATION_HEALTH_NOT_PASS'); end if;
  if r.latest_material_update_timestamp is not null and r.model_timestamp < r.latest_material_update_timestamp then v_blockers:=array_append(v_blockers,'MODEL_STALE_AFTER_MATERIAL_UPDATE'); end if;
  if a.artifact_id is null or not a.active or not a.promoted or a.lifecycle_state <> 'CHAMPION' then v_blockers:=array_append(v_blockers,'NFL_CHAMPION_ARTIFACT_NOT_ACTIVE'); end if;
  if c.calibrator_id is null or not c.active or not c.promoted or c.validation_status <> 'PASS' or c.health_status <> 'PASS' then v_blockers:=array_append(v_blockers,'NFL_CALIBRATOR_NOT_ACTIVE_PASS'); end if;
  if r.can_execute then v_blockers:=array_append(v_blockers,'CAN_EXECUTE_MUST_BE_FALSE'); end if;
  if r.blend_publishable then v_blockers:=array_append(v_blockers,'UNCERTIFIED_BLEND_PUBLICATION_BLOCKED'); end if;

  if cardinality(v_blockers)=0 then v_label:='FINAL_APPROVED'; end if;
  return jsonb_build_object(
    'status',case when v_label='FINAL_APPROVED' then 'PASS' else 'HOLD' end,
    'terminal_label',v_label,
    'blockers',to_jsonb(v_blockers),
    'global_terminal_reducer','V17_TERMINAL_REDUCER',
    'probability_publishable',(v_label='FINAL_APPROVED'),
    'rank_eligible',(v_label='FINAL_APPROVED'),
    'can_execute',false
  );
end;
$function$;

-- Reducer-owned issuer. Idempotent while the terminal reduction is unchanged;
-- returns NULL (fail closed) for HOLD, for pre-minted caller fields, or for a
-- stale receipt that no longer matches the current material state.
CREATE OR REPLACE FUNCTION wow_terminal_reducer.issue_nfl_reducer_receipt(
  p_score_snapshot_id uuid,
  p_reduction jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $f$
DECLARE
  v_existing wow_terminal_reducer.origin_receipts%ROWTYPE;
  v_sha text;
BEGIN
  IF p_score_snapshot_id IS NULL OR p_reduction IS NULL
     OR p_reduction->>'status' IS DISTINCT FROM 'PASS'
     OR p_reduction->>'terminal_label' IS DISTINCT FROM 'FINAL_APPROVED'
     OR p_reduction->>'global_terminal_reducer' IS DISTINCT FROM 'V17_TERMINAL_REDUCER'
     OR p_reduction->>'probability_publishable' IS DISTINCT FROM 'true'
     OR p_reduction->>'rank_eligible' IS DISTINCT FROM 'true'
     OR p_reduction->>'can_execute' IS DISTINCT FROM 'false'
     OR p_reduction ? 'reducer_receipt_id'
     OR p_reduction ? 'reducer_receipt_sha256' THEN
    RETURN NULL;
  END IF;

  -- Serialize issuance per source score snapshot; admission may race.
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'wow_terminal_reducer:' || p_score_snapshot_id::text, 0));

  SELECT * INTO v_existing FROM wow_terminal_reducer.origin_receipts
    WHERE score_snapshot_id = p_score_snapshot_id;
  IF FOUND THEN
    IF v_existing.reduction_payload IS DISTINCT FROM p_reduction THEN
      -- A previously issued receipt cannot be re-minted or re-bound.
      RETURN NULL;
    END IF;
    RETURN pg_catalog.jsonb_build_object(
      'reducer_receipt_id', v_existing.reducer_receipt_id::text,
      'reducer_receipt_sha256', v_existing.reducer_receipt_sha256);
  END IF;

  v_sha := wow_terminal_reducer.nfl_receipt_digest(p_score_snapshot_id, p_reduction);
  INSERT INTO wow_terminal_reducer.origin_receipts (
    score_snapshot_id, reducer_receipt_sha256, reduction_payload
  ) VALUES (
    p_score_snapshot_id, v_sha, p_reduction
  ) RETURNING * INTO v_existing;

  RETURN pg_catalog.jsonb_build_object(
    'reducer_receipt_id', v_existing.reducer_receipt_id::text,
    'reducer_receipt_sha256', v_existing.reducer_receipt_sha256);
END $f$;

-- Independently deployed attestor consumed by admission and readback.
-- Recomputes the canonical digest from the ledger row, so a tampered digest
-- cannot be attested even by direct privileged ledger manipulation.
CREATE OR REPLACE FUNCTION wow_terminal_reducer.verify_nfl_reducer_receipt(
  p_score_snapshot_id uuid,
  p_reducer_receipt_id uuid,
  p_reducer_receipt_sha256 text
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $f$
  SELECT coalesce((
    SELECT true
    FROM wow_terminal_reducer.origin_receipts o
    WHERE o.score_snapshot_id = p_score_snapshot_id
      AND o.reducer_receipt_id = p_reducer_receipt_id
      AND o.reducer_receipt_sha256 = p_reducer_receipt_sha256
      AND o.issuer = 'V17_TERMINAL_REDUCER'
      AND o.reducer_receipt_sha256 =
          wow_terminal_reducer.nfl_receipt_digest(
            o.score_snapshot_id, o.reduction_payload)
      AND o.reduction_payload->>'status' = 'PASS'
      AND o.reduction_payload->>'terminal_label' = 'FINAL_APPROVED'
      AND o.reduction_payload->>'global_terminal_reducer' = 'V17_TERMINAL_REDUCER'
      AND o.reduction_payload->>'probability_publishable' = 'true'
      AND o.reduction_payload->>'rank_eligible' = 'true'
      AND o.reduction_payload->>'can_execute' = 'false'
  ), false)
$f$;

-- Terminal reducer entrypoint. Same gate logic as before; a PASS now also
-- carries the reducer-owned receipt. If the reducer cannot authenticate its
-- own origin evidence, it returns a HOLD rather than an unsigned PASS.
CREATE OR REPLACE FUNCTION public.wow_v17_reduce_nfl_event_terminal_label(
  p_score_snapshot_id uuid
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $f$
DECLARE
  v_reduction jsonb;
  v_receipt jsonb;
BEGIN
  v_reduction := wow_terminal_reducer.reduce_nfl_event_terminal_label(p_score_snapshot_id);
  IF v_reduction IS NULL OR v_reduction->>'status' IS DISTINCT FROM 'PASS' THEN
    RETURN v_reduction;
  END IF;
  v_receipt := wow_terminal_reducer.issue_nfl_reducer_receipt(
    p_score_snapshot_id, v_reduction);
  IF v_receipt IS NULL THEN
    RETURN v_reduction || pg_catalog.jsonb_build_object(
      'status', 'HOLD',
      'terminal_label', 'MODEL_QUALIFIED_HOLD',
      'probability_publishable', false,
      'rank_eligible', false,
      'can_execute', false,
      'blockers', coalesce(v_reduction->'blockers', '[]'::jsonb)
        || '["REDUCER_RECEIPT_STALE_OR_UNAVAILABLE"]'::jsonb
    );
  END IF;
  RETURN v_reduction || v_receipt;
END $f$;

-- Least privilege. The private origin schema and issuer are not application
-- surfaces; only the SECURITY DEFINER entrypoints reach them.
REVOKE ALL ON SCHEMA wow_terminal_reducer FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA wow_terminal_reducer FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION wow_terminal_reducer.reject_mutation() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION wow_terminal_reducer.nfl_receipt_digest(uuid, jsonb) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION wow_terminal_reducer.reduce_nfl_event_terminal_label(uuid) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION wow_terminal_reducer.issue_nfl_reducer_receipt(uuid, jsonb) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION wow_terminal_reducer.verify_nfl_reducer_receipt(uuid, uuid, text) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.wow_v17_reduce_nfl_event_terminal_label(uuid) FROM PUBLIC;

DO $f$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'anon') THEN
    REVOKE ALL ON SCHEMA wow_terminal_reducer FROM anon;
    REVOKE ALL ON ALL TABLES IN SCHEMA wow_terminal_reducer FROM anon;
    REVOKE EXECUTE ON FUNCTION wow_terminal_reducer.nfl_receipt_digest(uuid, jsonb) FROM anon;
    REVOKE EXECUTE ON FUNCTION wow_terminal_reducer.reduce_nfl_event_terminal_label(uuid) FROM anon;
    REVOKE EXECUTE ON FUNCTION wow_terminal_reducer.issue_nfl_reducer_receipt(uuid, jsonb) FROM anon;
    REVOKE EXECUTE ON FUNCTION wow_terminal_reducer.verify_nfl_reducer_receipt(uuid, uuid, text) FROM anon;
    REVOKE EXECUTE ON FUNCTION public.wow_v17_reduce_nfl_event_terminal_label(uuid) FROM anon;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'authenticated') THEN
    REVOKE ALL ON SCHEMA wow_terminal_reducer FROM authenticated;
    REVOKE ALL ON ALL TABLES IN SCHEMA wow_terminal_reducer FROM authenticated;
    REVOKE EXECUTE ON FUNCTION wow_terminal_reducer.nfl_receipt_digest(uuid, jsonb) FROM authenticated;
    REVOKE EXECUTE ON FUNCTION wow_terminal_reducer.reduce_nfl_event_terminal_label(uuid) FROM authenticated;
    REVOKE EXECUTE ON FUNCTION wow_terminal_reducer.issue_nfl_reducer_receipt(uuid, jsonb) FROM authenticated;
    REVOKE EXECUTE ON FUNCTION wow_terminal_reducer.verify_nfl_reducer_receipt(uuid, uuid, text) FROM authenticated;
    REVOKE EXECUTE ON FUNCTION public.wow_v17_reduce_nfl_event_terminal_label(uuid) FROM authenticated;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'service_role') THEN
    REVOKE ALL ON SCHEMA wow_terminal_reducer FROM service_role;
    REVOKE ALL ON ALL TABLES IN SCHEMA wow_terminal_reducer FROM service_role;
    REVOKE EXECUTE ON FUNCTION wow_terminal_reducer.nfl_receipt_digest(uuid, jsonb) FROM service_role;
    REVOKE EXECUTE ON FUNCTION wow_terminal_reducer.reduce_nfl_event_terminal_label(uuid) FROM service_role;
    REVOKE EXECUTE ON FUNCTION wow_terminal_reducer.issue_nfl_reducer_receipt(uuid, jsonb) FROM service_role;
    REVOKE EXECUTE ON FUNCTION wow_terminal_reducer.verify_nfl_reducer_receipt(uuid, uuid, text) FROM service_role;
    GRANT EXECUTE ON FUNCTION public.wow_v17_reduce_nfl_event_terminal_label(uuid) TO service_role;
  END IF;
END $f$;

COMMIT;
-- No runtime flag change, no deployment, no model promotion, no real data write.
-- Independent acceptance remains REQUIRED before this candidate is deployed or
-- before any receipt may support a published probability.
