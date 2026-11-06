-- P0 V17 SOURCE CANDIDATE. NOT AN ACTIVE MIGRATION. NOT DEPLOYED.
-- NFL ONLY: authentic registered model -> V17_TERMINAL_REDUCER -> immutable admission.
-- NBA/NHL remain held until independently certified sport-specific source adapters exist.
-- Validation schema inspected: 2026-11-05, project wow-engine-validation.
-- Requires independent SIRT/QA/Release approval and verified production DB binding.
-- Keep public.wow_event_predictions MLB-only and unchanged.
BEGIN;

CREATE TABLE IF NOT EXISTS public.wow_v17_multisport_event_prediction_receipts (
  event_prediction_id uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
  created_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp(),
  schema_version text NOT NULL DEFAULT 'WOW_TEAM_EVENT_RECEIPT_V1'
    CHECK (schema_version='WOW_TEAM_EVENT_RECEIPT_V1'),
  sport text NOT NULL CHECK (sport IN ('NFL','NBA','NHL')),
  official_event_id text NOT NULL CHECK (length(official_event_id)>0),
  event_start_time timestamptz NOT NULL,
  home_team text NOT NULL,
  away_team text NOT NULL,
  selected_participant text NOT NULL,
  market_family text NOT NULL DEFAULT 'OUTRIGHT_WINNER'
    CHECK (market_family='OUTRIGHT_WINNER'),
  settlement_basis text NOT NULL,
  source_snapshot_id text NOT NULL,
  source_snapshot_timestamp timestamptz NOT NULL,
  score_snapshot_id uuid NOT NULL UNIQUE,
  model_artifact_id text NOT NULL,
  model_version text NOT NULL,
  model_artifact_sha256 text NOT NULL CHECK (model_artifact_sha256 ~ '^[0-9a-f]{64}$'),
  calibration_artifact_id text NOT NULL,
  calibration_version text NOT NULL,
  calibration_artifact_sha256 text NOT NULL CHECK (calibration_artifact_sha256 ~ '^[0-9a-f]{64}$'),
  model_timestamp timestamptz NOT NULL,
  locked_at timestamptz NOT NULL,
  terminal_reduced_at timestamptz NOT NULL,
  reducer_receipt_id uuid NOT NULL UNIQUE,
  reducer_receipt_sha256 text NOT NULL CHECK (reducer_receipt_sha256 ~ '^[0-9a-f]{64}$'),
  terminal_reducer_payload jsonb NOT NULL,
  terminal_authority text NOT NULL DEFAULT 'V17_TERMINAL_REDUCER'
    CHECK (terminal_authority='V17_TERMINAL_REDUCER'),
  terminal_status text NOT NULL DEFAULT 'PASS' CHECK (terminal_status='PASS'),
  terminal_label text NOT NULL DEFAULT 'FINAL_APPROVED'
    CHECK (terminal_label='FINAL_APPROVED'),
  calibrated_home_probability numeric NOT NULL,
  calibrated_home_lower_bound numeric NOT NULL,
  calibrated_home_upper_bound numeric NOT NULL,
  calibrated_away_probability numeric NOT NULL,
  calibrated_away_lower_bound numeric NOT NULL,
  calibrated_away_upper_bound numeric NOT NULL,
  rank_eligible boolean NOT NULL CHECK (rank_eligible=true),
  probability_publishable boolean NOT NULL CHECK (probability_publishable=true),
  can_execute boolean NOT NULL DEFAULT false CHECK (can_execute=false),
  CONSTRAINT v17_mr_distinct_teams CHECK (home_team<>away_team),
  CONSTRAINT v17_mr_exact_side CHECK (selected_participant IN (home_team,away_team)),
  CONSTRAINT v17_mr_basis CHECK (
    (sport IN ('NFL','NBA') AND settlement_basis='FULL_GAME_INCLUDING_OVERTIME')
    OR (sport='NHL' AND settlement_basis='FULL_GAME_MONEYLINE_INCLUDING_OVERTIME_SHOOTOUT')
  ),
  CONSTRAINT v17_mr_chronology CHECK (
    source_snapshot_timestamp <= model_timestamp AND model_timestamp <= locked_at
    AND locked_at <= terminal_reduced_at
    AND terminal_reduced_at <= created_at AND created_at < event_start_time
  ),
  CONSTRAINT v17_mr_home_bounds CHECK (
    calibrated_home_lower_bound > 0 AND calibrated_home_lower_bound <= calibrated_home_probability
    AND calibrated_home_probability <= calibrated_home_upper_bound AND calibrated_home_upper_bound < 1
  ),
  CONSTRAINT v17_mr_away_bounds CHECK (
    calibrated_away_lower_bound > 0 AND calibrated_away_lower_bound <= calibrated_away_probability
    AND calibrated_away_probability <= calibrated_away_upper_bound AND calibrated_away_upper_bound < 1
  ),
  CONSTRAINT v17_mr_pair CHECK (
    pg_catalog.abs(calibrated_home_probability + calibrated_away_probability - 1) <= 0.000001
  )
);

CREATE OR REPLACE FUNCTION public.wow_v17_multisport_receipt_identity()
RETURNS trigger LANGUAGE plpgsql SET search_path='' AS $f$
BEGIN
  NEW.event_prediction_id := pg_catalog.gen_random_uuid();
  NEW.created_at := pg_catalog.clock_timestamp();
  RETURN NEW;
END $f$;

CREATE TRIGGER trg_v17_multisport_receipt_identity
  BEFORE INSERT ON public.wow_v17_multisport_event_prediction_receipts
  FOR EACH ROW EXECUTE FUNCTION public.wow_v17_multisport_receipt_identity();

CREATE OR REPLACE FUNCTION public.wow_v17_multisport_receipt_immutable()
RETURNS trigger LANGUAGE plpgsql SET search_path='' AS $f$
BEGIN
  RAISE EXCEPTION 'V17_MULTISPORT_RECEIPT_IMMUTABLE' USING ERRCODE='55000';
END $f$;

CREATE TRIGGER trg_v17_multisport_receipt_immutable
  BEFORE UPDATE OR DELETE ON public.wow_v17_multisport_event_prediction_receipts
  FOR EACH ROW EXECUTE FUNCTION public.wow_v17_multisport_receipt_immutable();

ALTER TABLE public.wow_v17_multisport_event_prediction_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.wow_v17_multisport_event_prediction_receipts FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.wow_v17_multisport_event_prediction_receipts FROM PUBLIC;

-- P0 certification guard: reducer PASS does NOT itself certify a fitted
-- specialist. Missing/incomplete genuine forward grades OR missing independent
-- model certification must hold BOTH issuance and immutable readback.
CREATE OR REPLACE FUNCTION public.wow_v17_nfl_receipt_certified(p_score_snapshot_id uuid)
RETURNS boolean LANGUAGE plpgsql SET search_path='' AS $cert$
DECLARE
  r public.wow_nfl_event_predictions%ROWTYPE;
  a public.wow_nfl_event_fitted_model_artifacts%ROWTYPE;
  c public.wow_calibrators%ROWTYPE;
  f public.wow_nfl_forward_calibration_health%ROWTYPE;
  cal_sha text;
BEGIN
  SELECT * INTO r FROM public.wow_nfl_event_predictions
    WHERE score_snapshot_id=p_score_snapshot_id;
  IF NOT FOUND THEN RETURN false; END IF;
  SELECT * INTO a FROM public.wow_nfl_event_fitted_model_artifacts
    WHERE artifact_id=r.model_artifact_id;
  IF NOT FOUND OR a.active IS DISTINCT FROM true OR a.promoted IS DISTINCT FROM true
    OR a.lifecycle_state IS DISTINCT FROM 'CHAMPION'
    OR a.probability_publishable IS DISTINCT FROM true
    OR a.can_execute IS DISTINCT FROM false
    OR a.model_artifact_version IS DISTINCT FROM r.model_version
    OR a.artifact_checksum !~ '^[0-9a-f]{64}
-- approval, chosen team, model hash, status or reducer label.
-- Function obtains all authoritative fields from independent persisted tables.
CREATE OR REPLACE FUNCTION public.wow_v17_admit_nfl_event_receipt(p_score_snapshot_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $f$
DECLARE
  s public.wow_nfl_event_predictions%ROWTYPE;
  a public.wow_nfl_event_fitted_model_artifacts%ROWTYPE;
  c public.wow_calibrators%ROWTYPE;
  reduction jsonb;
  lock_time timestamptz;
  reduced_time timestamptz;
  reducer_id uuid;
  reducer_hash text;
  cal_hash text;
  existing public.wow_v17_multisport_event_prediction_receipts%ROWTYPE;
  issued public.wow_v17_multisport_event_prediction_receipts%ROWTYPE;
BEGIN
  IF p_score_snapshot_id IS NULL THEN
    RETURN pg_catalog.jsonb_build_object('status','HOLD','code','LLP_EVENT_PREDICTION_ID_UNVERIFIED','can_execute',false);
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_score_snapshot_id::text, 0));
  SELECT * INTO s FROM public.wow_nfl_event_predictions
    WHERE score_snapshot_id=p_score_snapshot_id FOR SHARE;
  IF NOT FOUND THEN
    RETURN pg_catalog.jsonb_build_object('status','HOLD','code','LLP_EVENT_PREDICTION_NOT_EXACTLY_ONE','can_execute',false);
  END IF;
  -- No retrospective issuance or blind idempotent success after event start.
  IF s.event_start_time_utc <= pg_catalog.clock_timestamp()
     OR s.source_snapshot_timestamp > s.model_timestamp
     OR s.model_timestamp >= s.event_start_time_utc
     OR s.selected_participant NOT IN (s.home_team,s.away_team)
     OR s.can_execute IS DISTINCT FROM false
     OR s.model_probability_publishable IS DISTINCT FROM true
     OR s.model_package_valid IS DISTINCT FROM true
     OR s.provenance_complete IS DISTINCT FROM true
     OR s.calibration_health_status <> 'PASS'
     OR s.blend_publishable IS DISTINCT FROM false THEN
    RETURN pg_catalog.jsonb_build_object('status','HOLD','code','LLP_EVENT_PREDICTION_RECEIPT_MISMATCH','can_execute',false);
  END IF;
  SELECT * INTO a FROM public.wow_nfl_event_fitted_model_artifacts
    WHERE artifact_id=s.model_artifact_id;
  IF NOT FOUND OR a.active IS DISTINCT FROM true OR a.promoted IS DISTINCT FROM true
     OR a.lifecycle_state <> 'CHAMPION' OR a.can_execute IS DISTINCT FROM false
     OR a.probability_publishable IS DISTINCT FROM true
     OR a.model_artifact_version<>s.model_version
     OR a.artifact_checksum !~ '^[0-9a-f]{64}$'
     OR a.artifact_checksum<>s.model_output_snapshot->>'artifact_checksum' THEN
    RETURN pg_catalog.jsonb_build_object('status','HOLD','code','LLP_EVENT_PREDICTION_RECEIPT_MISMATCH','can_execute',false);
  END IF;
  SELECT * INTO c FROM public.wow_calibrators WHERE calibrator_id=a.calibrator_id;
  IF NOT FOUND OR c.active IS DISTINCT FROM true OR c.promoted IS DISTINCT FROM true
     OR c.validation_status <> 'PASS' OR c.health_status <> 'PASS'
     OR c.sport <> 'NFL' OR c.market_family <> 'OUTRIGHT_WINNER'
     OR c.calibration_version<>s.calibration_version THEN
    RETURN pg_catalog.jsonb_build_object('status','HOLD','code','LLP_EVENT_PREDICTION_RECEIPT_MISMATCH','can_execute',false);
  END IF;
  -- Calibration hash is a DB-computed content digest of the actual certified
  -- calibrator row, NOT a caller-supplied substitute for a certifying receipt.
  cal_hash := pg_catalog.encode(extensions.digest(
    pg_catalog.convert_to(pg_catalog.to_jsonb(c)::text, 'UTF8'), 'sha256'
  ), 'hex');
  -- A duplicate may not bypass a newer material-update/reducer HOLD.
  -- Recheck the authoritative reducer BEFORE any existing-row admission.
  lock_time:=pg_catalog.clock_timestamp();
  reduction:=public.wow_v17_reduce_nfl_event_terminal_label(p_score_snapshot_id);
  reduced_time:=pg_catalog.clock_timestamp();
  IF reduction IS NULL OR reduction->>'status'<>'PASS'
    OR reduction->>'terminal_label'<>'FINAL_APPROVED'
    OR reduction->>'global_terminal_reducer'<>'V17_TERMINAL_REDUCER'
    OR reduction->>'can_execute'<>'false'
    OR reduction->>'probability_publishable'<>'true'
    OR reduction->>'rank_eligible'<>'true'
    OR reduced_time >= s.event_start_time_utc THEN
    RETURN pg_catalog.jsonb_build_object('status','HOLD','code','LLP_EVENT_PREDICTION_RECEIPT_MISMATCH','can_execute',false);
  END IF;
  -- Stable exact-once source score: conflicting duplicate cannot overwrite.
  SELECT * INTO existing FROM public.wow_v17_multisport_event_prediction_receipts
    WHERE score_snapshot_id=p_score_snapshot_id;
  IF FOUND THEN
    IF existing.sport='NFL' AND existing.official_event_id=s.official_event_id
       AND existing.home_team=s.home_team AND existing.away_team=s.away_team
       AND existing.selected_participant=s.selected_participant
       AND existing.model_artifact_sha256=a.artifact_checksum
       AND existing.calibration_artifact_sha256=cal_hash THEN
      RETURN pg_catalog.jsonb_build_object('status','ALREADY_PERSISTED',
        'event_prediction_id',existing.event_prediction_id,
        'score_snapshot_id',s.score_snapshot_id,
        'reducer_receipt_id',existing.reducer_receipt_id,
        'reducer_receipt_sha256',existing.reducer_receipt_sha256,
        'can_execute',false);
    END IF;
    RETURN pg_catalog.jsonb_build_object('status','HOLD','code','LLP_EVENT_PREDICTION_RECEIPT_MISMATCH','can_execute',false);
  END IF;
  reducer_id:=pg_catalog.gen_random_uuid();
  reducer_hash:=pg_catalog.encode(extensions.digest(
    pg_catalog.convert_to(pg_catalog.jsonb_build_object(
      'score_snapshot_id',s.score_snapshot_id,
      'reducer_receipt_id',reducer_id,
      'locked_at',lock_time,'terminal_reduced_at',reduced_time,
      'reducer',reduction
    )::text,'UTF8'),'sha256'), 'hex');
  INSERT INTO public.wow_v17_multisport_event_prediction_receipts (
    sport,official_event_id,event_start_time,home_team,away_team,
    selected_participant,market_family,settlement_basis,
    source_snapshot_id,source_snapshot_timestamp,score_snapshot_id,
    model_artifact_id,model_version,model_artifact_sha256,
    calibration_artifact_id,calibration_version,calibration_artifact_sha256,
    model_timestamp,locked_at,terminal_reduced_at,
    reducer_receipt_id,reducer_receipt_sha256,terminal_reducer_payload,
    terminal_authority,terminal_status,terminal_label,
    calibrated_home_probability,calibrated_home_lower_bound,calibrated_home_upper_bound,
    calibrated_away_probability,calibrated_away_lower_bound,calibrated_away_upper_bound,
    rank_eligible,probability_publishable,can_execute
  ) VALUES (
    'NFL',s.official_event_id,s.event_start_time_utc,s.home_team,s.away_team,
    s.selected_participant,'OUTRIGHT_WINNER','FULL_GAME_INCLUDING_OVERTIME',
    s.source_snapshot_id::text,s.source_snapshot_timestamp,s.score_snapshot_id,
    a.artifact_id::text,s.model_version,a.artifact_checksum,
    c.calibrator_id::text,s.calibration_version,cal_hash,
    s.model_timestamp,lock_time,reduced_time,reducer_id,reducer_hash,reduction,
    'V17_TERMINAL_REDUCER','PASS','FINAL_APPROVED',
    s.calibrated_home_probability,s.calibrated_home_lower_bound,s.calibrated_home_upper_bound,
    s.calibrated_away_probability,s.calibrated_away_lower_bound,s.calibrated_away_upper_bound,
    true,true,false
  ) RETURNING * INTO issued;
  RETURN pg_catalog.jsonb_build_object('status','PERSISTED',
    'event_prediction_id',issued.event_prediction_id,
    'score_snapshot_id',issued.score_snapshot_id,
    'reducer_receipt_id',issued.reducer_receipt_id,
    'reducer_receipt_sha256',issued.reducer_receipt_sha256,
    'can_execute',false);
END $f$;

-- Independent readback: re-evaluate the controlling specialist + reducer,
-- and verify immutable admission payload rather than trusting saved flags.
CREATE OR REPLACE FUNCTION public.wow_v17_verify_multisport_event_receipt(p_event_prediction_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $f$
DECLARE
  r public.wow_v17_multisport_event_prediction_receipts%ROWTYPE;
  s public.wow_nfl_event_predictions%ROWTYPE;
  a public.wow_nfl_event_fitted_model_artifacts%ROWTYPE;
  c public.wow_calibrators%ROWTYPE;
  present jsonb;
  cal_hash text;
  hash text;
BEGIN
  SELECT * INTO r FROM public.wow_v17_multisport_event_prediction_receipts
    WHERE event_prediction_id=p_event_prediction_id;
  IF NOT FOUND THEN
    RETURN pg_catalog.jsonb_build_object('status','HOLD','can_execute',false);
  END IF;
  -- NFL is the ONLY implemented producer/reducer. NBA/NHL never inherit NFL.
  IF r.sport<>'NFL' OR r.event_start_time<=pg_catalog.clock_timestamp() THEN
    RETURN pg_catalog.jsonb_build_object('status','HOLD','can_execute',false);
  END IF;
  SELECT * INTO s FROM public.wow_nfl_event_predictions
    WHERE score_snapshot_id=r.score_snapshot_id;
  IF NOT FOUND THEN
    RETURN pg_catalog.jsonb_build_object('status','HOLD','can_execute',false);
  END IF;
  SELECT * INTO a FROM public.wow_nfl_event_fitted_model_artifacts
    WHERE artifact_id=s.model_artifact_id;
  SELECT * INTO c FROM public.wow_calibrators WHERE calibrator_id=a.calibrator_id;
  IF a.artifact_id IS NULL OR c.calibrator_id IS NULL
     OR a.active IS DISTINCT FROM true OR a.promoted IS DISTINCT FROM true
     OR a.lifecycle_state<>'CHAMPION' OR c.active IS DISTINCT FROM true
     OR c.promoted IS DISTINCT FROM true OR c.validation_status<>'PASS'
     OR c.health_status<>'PASS'
     OR s.model_probability_publishable IS DISTINCT FROM true
     OR s.provenance_complete IS DISTINCT FROM true OR s.model_package_valid IS DISTINCT FROM true
     OR s.can_execute IS DISTINCT FROM false
     OR r.can_execute IS DISTINCT FROM false THEN
    RETURN pg_catalog.jsonb_build_object('status','HOLD','can_execute',false);
  END IF;
  cal_hash:=pg_catalog.encode(extensions.digest(
    pg_catalog.convert_to(pg_catalog.to_jsonb(c)::text,'UTF8'),'sha256'),'hex');
  hash:=pg_catalog.encode(extensions.digest(
    pg_catalog.convert_to(pg_catalog.jsonb_build_object(
      'score_snapshot_id',r.score_snapshot_id,
      'reducer_receipt_id',r.reducer_receipt_id,
      'locked_at',r.locked_at,'terminal_reduced_at',r.terminal_reduced_at,
      'reducer',r.terminal_reducer_payload
    )::text,'UTF8'),'sha256'),'hex');
  IF r.reducer_receipt_sha256<>hash
     OR r.calibration_artifact_sha256<>cal_hash
     OR r.calibration_artifact_id<>c.calibrator_id::text
     OR r.calibration_version<>c.calibration_version
     OR r.model_artifact_id<>a.artifact_id::text
     OR r.model_artifact_sha256<>a.artifact_checksum
     OR a.artifact_checksum<>s.model_output_snapshot->>'artifact_checksum'
     OR r.official_event_id<>s.official_event_id
     OR r.event_start_time<>s.event_start_time_utc
     OR r.selected_participant<>s.selected_participant
     OR r.home_team<>s.home_team OR r.away_team<>s.away_team
     OR r.model_timestamp<>s.model_timestamp
     OR r.source_snapshot_timestamp<>s.source_snapshot_timestamp
     OR r.source_snapshot_id<>s.source_snapshot_id::text
     OR r.model_version<>s.model_version OR r.calibration_version<>s.calibration_version
     OR r.calibrated_home_probability<>s.calibrated_home_probability
     OR r.calibrated_away_probability<>s.calibrated_away_probability
     OR r.calibrated_home_lower_bound<>s.calibrated_home_lower_bound
     OR r.calibrated_home_upper_bound<>s.calibrated_home_upper_bound
     OR r.calibrated_away_lower_bound<>s.calibrated_away_lower_bound
     OR r.calibrated_away_upper_bound<>s.calibrated_away_upper_bound
     OR r.terminal_reducer_payload->>'status'<>'PASS'
     OR r.terminal_reducer_payload->>'global_terminal_reducer'<>'V17_TERMINAL_REDUCER'
     OR r.terminal_reducer_payload->>'terminal_label'<>'FINAL_APPROVED' THEN
    RETURN pg_catalog.jsonb_build_object('status','HOLD','can_execute',false);
  END IF;
  present:=public.wow_v17_reduce_nfl_event_terminal_label(r.score_snapshot_id);
  IF r.terminal_reducer_payload IS DISTINCT FROM present
    OR present->>'status'<>'PASS' OR present->>'global_terminal_reducer'<>'V17_TERMINAL_REDUCER'
    OR present->>'terminal_label'<>'FINAL_APPROVED'
    OR present->>'can_execute'<>'false' OR present->>'probability_publishable'<>'true'
    OR present->>'rank_eligible'<>'true' THEN
    RETURN pg_catalog.jsonb_build_object('status','HOLD','can_execute',false);
  END IF;
  RETURN pg_catalog.jsonb_build_object(
    'status','PASS','terminal_label','FINAL_APPROVED',
    'terminal_authority','V17_TERMINAL_REDUCER',
    'event_prediction_id',r.event_prediction_id,
    'score_snapshot_id',r.score_snapshot_id,
    'reducer_receipt_id',r.reducer_receipt_id,
    'reducer_receipt_sha256',r.reducer_receipt_sha256,
    'probability_publishable',true,'rank_eligible',true,'can_execute',false
  );
END $f$;

-- Principle of least privilege. No direct app INSERT/UPDATE/DELETE.
REVOKE EXECUTE ON FUNCTION public.wow_v17_multisport_receipt_identity() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.wow_v17_multisport_receipt_immutable() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.wow_v17_admit_nfl_event_receipt(uuid) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.wow_v17_verify_multisport_event_receipt(uuid) FROM PUBLIC;
DO $f$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='anon') THEN
    REVOKE ALL ON public.wow_v17_multisport_event_prediction_receipts FROM anon;
    REVOKE EXECUTE ON FUNCTION public.wow_v17_admit_nfl_event_receipt(uuid) FROM anon;
    REVOKE EXECUTE ON FUNCTION public.wow_v17_verify_multisport_event_receipt(uuid) FROM anon;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='authenticated') THEN
    REVOKE ALL ON public.wow_v17_multisport_event_prediction_receipts FROM authenticated;
    REVOKE EXECUTE ON FUNCTION public.wow_v17_admit_nfl_event_receipt(uuid) FROM authenticated;
    REVOKE EXECUTE ON FUNCTION public.wow_v17_verify_multisport_event_receipt(uuid) FROM authenticated;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='service_role') THEN
    REVOKE ALL ON public.wow_v17_multisport_event_prediction_receipts FROM service_role;
    GRANT SELECT ON public.wow_v17_multisport_event_prediction_receipts TO service_role;
    GRANT EXECUTE ON FUNCTION public.wow_v17_admit_nfl_event_receipt(uuid) TO service_role;
    GRANT EXECUTE ON FUNCTION public.wow_v17_verify_multisport_event_receipt(uuid) TO service_role;
  END IF;
END $f$;
COMMIT;
-- No runtime flag change, no deployment, no model promotion, no real data write.
-- This source must not be applied before independent acceptance + release.

  THEN RETURN false; END IF;
  SELECT * INTO c FROM public.wow_calibrators WHERE calibrator_id=a.calibrator_id;
  IF NOT FOUND OR c.active IS DISTINCT FROM true OR c.promoted IS DISTINCT FROM true
    OR c.validation_status IS DISTINCT FROM 'PASS'
    OR c.health_status IS DISTINCT FROM 'PASS'
    OR c.sport IS DISTINCT FROM 'NFL'
    OR c.market_family IS DISTINCT FROM 'OUTRIGHT_WINNER'
    OR c.calibration_version IS DISTINCT FROM r.calibration_version
  THEN RETURN false; END IF;
  cal_sha:=pg_catalog.encode(extensions.digest(
    pg_catalog.convert_to(pg_catalog.to_jsonb(c)::text,'UTF8'),'sha256'),'hex');
  SELECT * INTO f FROM public.wow_nfl_forward_calibration_health
    ORDER BY generated_at DESC,created_at DESC LIMIT 1;
  IF NOT FOUND OR f.status IS DISTINCT FROM 'PASS'
    OR f.certification_recommendation IS DISTINCT FROM 'FORWARD_EVIDENCE_PASS'
    OR f.graded_n IS NULL OR f.minimum_forward_required IS NULL
    OR f.graded_n < pg_catalog.greatest(f.minimum_forward_required,100)
    OR f.can_execute IS DISTINCT FROM false
    OR f.blockers IS DISTINCT FROM '[]'::jsonb
  THEN RETURN false; END IF;
  RETURN EXISTS (
    SELECT 1 FROM public.wow_team_event_specialist_certifications cert
    WHERE cert.sport='NFL' AND cert.league_scope='NFL'
      AND cert.model_family=a.model_family
      AND cert.model_version=r.model_version
      AND cert.artifact_id=a.artifact_id::text
      AND cert.artifact_sha256=a.artifact_checksum
      AND cert.calibration_artifact_id=c.calibrator_id::text
      AND cert.calibration_sha256=cal_sha
      AND cert.independent_verification_status='PASS'
      AND cert.certification_status='CERTIFIED'
      AND cert.probability_publishable=true AND cert.can_execute=false
      AND cert.certified_at IS NOT NULL
      AND cert.certified_at<=pg_catalog.clock_timestamp()
      AND cert.verification_receipt IS NOT NULL
      AND cert.verification_receipt <> '{}'::jsonb
      AND NOT EXISTS (
        SELECT 1 FROM public.wow_team_event_specialist_certifications revoked
        WHERE revoked.supersedes_certification_id=cert.certification_id
          AND revoked.certification_status='REVOKED'
      )
  );
END $cert$;

-- A caller can present ONLY a source snapshot UUID, never odds, p, side,
-- approval, chosen team, model hash, status or reducer label.
-- Function obtains all authoritative fields from independent persisted tables.
CREATE OR REPLACE FUNCTION public.wow_v17_admit_nfl_event_receipt(p_score_snapshot_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $f$
DECLARE
  s public.wow_nfl_event_predictions%ROWTYPE;
  a public.wow_nfl_event_fitted_model_artifacts%ROWTYPE;
  c public.wow_calibrators%ROWTYPE;
  reduction jsonb;
  lock_time timestamptz;
  reduced_time timestamptz;
  reducer_id uuid;
  reducer_hash text;
  cal_hash text;
  existing public.wow_v17_multisport_event_prediction_receipts%ROWTYPE;
  issued public.wow_v17_multisport_event_prediction_receipts%ROWTYPE;
BEGIN
  IF p_score_snapshot_id IS NULL THEN
    RETURN pg_catalog.jsonb_build_object('status','HOLD','code','LLP_EVENT_PREDICTION_ID_UNVERIFIED','can_execute',false);
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_score_snapshot_id::text, 0));
  SELECT * INTO s FROM public.wow_nfl_event_predictions
    WHERE score_snapshot_id=p_score_snapshot_id FOR SHARE;
  IF NOT FOUND THEN
    RETURN pg_catalog.jsonb_build_object('status','HOLD','code','LLP_EVENT_PREDICTION_NOT_EXACTLY_ONE','can_execute',false);
  END IF;
  -- No retrospective issuance or blind idempotent success after event start.
  IF s.event_start_time_utc <= pg_catalog.clock_timestamp()
     OR s.source_snapshot_timestamp > s.model_timestamp
     OR s.model_timestamp >= s.event_start_time_utc
     OR s.selected_participant NOT IN (s.home_team,s.away_team)
     OR s.can_execute IS DISTINCT FROM false
     OR s.model_probability_publishable IS DISTINCT FROM true
     OR s.model_package_valid IS DISTINCT FROM true
     OR s.provenance_complete IS DISTINCT FROM true
     OR s.calibration_health_status <> 'PASS'
     OR s.blend_publishable IS DISTINCT FROM false THEN
    RETURN pg_catalog.jsonb_build_object('status','HOLD','code','LLP_EVENT_PREDICTION_RECEIPT_MISMATCH','can_execute',false);
  END IF;
  SELECT * INTO a FROM public.wow_nfl_event_fitted_model_artifacts
    WHERE artifact_id=s.model_artifact_id;
  IF NOT FOUND OR a.active IS DISTINCT FROM true OR a.promoted IS DISTINCT FROM true
     OR a.lifecycle_state <> 'CHAMPION' OR a.can_execute IS DISTINCT FROM false
     OR a.probability_publishable IS DISTINCT FROM true
     OR a.model_artifact_version<>s.model_version
     OR a.artifact_checksum !~ '^[0-9a-f]{64}$'
     OR a.artifact_checksum<>s.model_output_snapshot->>'artifact_checksum' THEN
    RETURN pg_catalog.jsonb_build_object('status','HOLD','code','LLP_EVENT_PREDICTION_RECEIPT_MISMATCH','can_execute',false);
  END IF;
  SELECT * INTO c FROM public.wow_calibrators WHERE calibrator_id=a.calibrator_id;
  IF NOT FOUND OR c.active IS DISTINCT FROM true OR c.promoted IS DISTINCT FROM true
     OR c.validation_status <> 'PASS' OR c.health_status <> 'PASS'
     OR c.sport <> 'NFL' OR c.market_family <> 'OUTRIGHT_WINNER'
     OR c.calibration_version<>s.calibration_version THEN
    RETURN pg_catalog.jsonb_build_object('status','HOLD','code','LLP_EVENT_PREDICTION_RECEIPT_MISMATCH','can_execute',false);
  END IF;
  -- Calibration hash is a DB-computed content digest of the actual certified
  -- calibrator row, NOT a caller-supplied substitute for a certifying receipt.
  cal_hash := pg_catalog.encode(extensions.digest(
    pg_catalog.convert_to(pg_catalog.to_jsonb(c)::text, 'UTF8'), 'sha256'
  ), 'hex');
  -- A duplicate may not bypass a newer material-update/reducer HOLD.
  -- Recheck the authoritative reducer BEFORE any existing-row admission.
  lock_time:=pg_catalog.clock_timestamp();
  reduction:=public.wow_v17_reduce_nfl_event_terminal_label(p_score_snapshot_id);
  reduced_time:=pg_catalog.clock_timestamp();
  IF reduction IS NULL OR reduction->>'status'<>'PASS'
    OR reduction->>'terminal_label'<>'FINAL_APPROVED'
    OR reduction->>'global_terminal_reducer'<>'V17_TERMINAL_REDUCER'
    OR reduction->>'can_execute'<>'false'
    OR reduction->>'probability_publishable'<>'true'
    OR reduction->>'rank_eligible'<>'true'
    OR reduced_time >= s.event_start_time_utc THEN
    RETURN pg_catalog.jsonb_build_object('status','HOLD','code','LLP_EVENT_PREDICTION_RECEIPT_MISMATCH','can_execute',false);
  END IF;
  -- Stable exact-once source score: conflicting duplicate cannot overwrite.
  SELECT * INTO existing FROM public.wow_v17_multisport_event_prediction_receipts
    WHERE score_snapshot_id=p_score_snapshot_id;
  IF FOUND THEN
    IF existing.sport='NFL' AND existing.official_event_id=s.official_event_id
       AND existing.home_team=s.home_team AND existing.away_team=s.away_team
       AND existing.selected_participant=s.selected_participant
       AND existing.model_artifact_sha256=a.artifact_checksum
       AND existing.calibration_artifact_sha256=cal_hash THEN
      RETURN pg_catalog.jsonb_build_object('status','ALREADY_PERSISTED',
        'event_prediction_id',existing.event_prediction_id,
        'score_snapshot_id',s.score_snapshot_id,
        'reducer_receipt_id',existing.reducer_receipt_id,
        'reducer_receipt_sha256',existing.reducer_receipt_sha256,
        'can_execute',false);
    END IF;
    RETURN pg_catalog.jsonb_build_object('status','HOLD','code','LLP_EVENT_PREDICTION_RECEIPT_MISMATCH','can_execute',false);
  END IF;
  reducer_id:=pg_catalog.gen_random_uuid();
  reducer_hash:=pg_catalog.encode(extensions.digest(
    pg_catalog.convert_to(pg_catalog.jsonb_build_object(
      'score_snapshot_id',s.score_snapshot_id,
      'reducer_receipt_id',reducer_id,
      'locked_at',lock_time,'terminal_reduced_at',reduced_time,
      'reducer',reduction
    )::text,'UTF8'),'sha256'), 'hex');
  INSERT INTO public.wow_v17_multisport_event_prediction_receipts (
    sport,official_event_id,event_start_time,home_team,away_team,
    selected_participant,market_family,settlement_basis,
    source_snapshot_id,source_snapshot_timestamp,score_snapshot_id,
    model_artifact_id,model_version,model_artifact_sha256,
    calibration_artifact_id,calibration_version,calibration_artifact_sha256,
    model_timestamp,locked_at,terminal_reduced_at,
    reducer_receipt_id,reducer_receipt_sha256,terminal_reducer_payload,
    terminal_authority,terminal_status,terminal_label,
    calibrated_home_probability,calibrated_home_lower_bound,calibrated_home_upper_bound,
    calibrated_away_probability,calibrated_away_lower_bound,calibrated_away_upper_bound,
    rank_eligible,probability_publishable,can_execute
  ) VALUES (
    'NFL',s.official_event_id,s.event_start_time_utc,s.home_team,s.away_team,
    s.selected_participant,'OUTRIGHT_WINNER','FULL_GAME_INCLUDING_OVERTIME',
    s.source_snapshot_id::text,s.source_snapshot_timestamp,s.score_snapshot_id,
    a.artifact_id::text,s.model_version,a.artifact_checksum,
    c.calibrator_id::text,s.calibration_version,cal_hash,
    s.model_timestamp,lock_time,reduced_time,reducer_id,reducer_hash,reduction,
    'V17_TERMINAL_REDUCER','PASS','FINAL_APPROVED',
    s.calibrated_home_probability,s.calibrated_home_lower_bound,s.calibrated_home_upper_bound,
    s.calibrated_away_probability,s.calibrated_away_lower_bound,s.calibrated_away_upper_bound,
    true,true,false
  ) RETURNING * INTO issued;
  RETURN pg_catalog.jsonb_build_object('status','PERSISTED',
    'event_prediction_id',issued.event_prediction_id,
    'score_snapshot_id',issued.score_snapshot_id,
    'reducer_receipt_id',issued.reducer_receipt_id,
    'reducer_receipt_sha256',issued.reducer_receipt_sha256,
    'can_execute',false);
END $f$;

-- Independent readback: re-evaluate the controlling specialist + reducer,
-- and verify immutable admission payload rather than trusting saved flags.
CREATE OR REPLACE FUNCTION public.wow_v17_verify_multisport_event_receipt(p_event_prediction_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $f$
DECLARE
  r public.wow_v17_multisport_event_prediction_receipts%ROWTYPE;
  s public.wow_nfl_event_predictions%ROWTYPE;
  a public.wow_nfl_event_fitted_model_artifacts%ROWTYPE;
  c public.wow_calibrators%ROWTYPE;
  present jsonb;
  cal_hash text;
  hash text;
BEGIN
  SELECT * INTO r FROM public.wow_v17_multisport_event_prediction_receipts
    WHERE event_prediction_id=p_event_prediction_id;
  IF NOT FOUND THEN
    RETURN pg_catalog.jsonb_build_object('status','HOLD','can_execute',false);
  END IF;
  -- NFL is the ONLY implemented producer/reducer. NBA/NHL never inherit NFL.
  IF r.sport<>'NFL' OR r.event_start_time<=pg_catalog.clock_timestamp() THEN
    RETURN pg_catalog.jsonb_build_object('status','HOLD','can_execute',false);
  END IF;
  SELECT * INTO s FROM public.wow_nfl_event_predictions
    WHERE score_snapshot_id=r.score_snapshot_id;
  IF NOT FOUND THEN
    RETURN pg_catalog.jsonb_build_object('status','HOLD','can_execute',false);
  END IF;
  SELECT * INTO a FROM public.wow_nfl_event_fitted_model_artifacts
    WHERE artifact_id=s.model_artifact_id;
  SELECT * INTO c FROM public.wow_calibrators WHERE calibrator_id=a.calibrator_id;
  IF a.artifact_id IS NULL OR c.calibrator_id IS NULL
     OR a.active IS DISTINCT FROM true OR a.promoted IS DISTINCT FROM true
     OR a.lifecycle_state<>'CHAMPION' OR c.active IS DISTINCT FROM true
     OR c.promoted IS DISTINCT FROM true OR c.validation_status<>'PASS'
     OR c.health_status<>'PASS'
     OR s.model_probability_publishable IS DISTINCT FROM true
     OR s.provenance_complete IS DISTINCT FROM true OR s.model_package_valid IS DISTINCT FROM true
     OR s.can_execute IS DISTINCT FROM false
     OR r.can_execute IS DISTINCT FROM false THEN
    RETURN pg_catalog.jsonb_build_object('status','HOLD','can_execute',false);
  END IF;
  cal_hash:=pg_catalog.encode(extensions.digest(
    pg_catalog.convert_to(pg_catalog.to_jsonb(c)::text,'UTF8'),'sha256'),'hex');
  hash:=pg_catalog.encode(extensions.digest(
    pg_catalog.convert_to(pg_catalog.jsonb_build_object(
      'score_snapshot_id',r.score_snapshot_id,
      'reducer_receipt_id',r.reducer_receipt_id,
      'locked_at',r.locked_at,'terminal_reduced_at',r.terminal_reduced_at,
      'reducer',r.terminal_reducer_payload
    )::text,'UTF8'),'sha256'),'hex');
  IF r.reducer_receipt_sha256<>hash
     OR r.calibration_artifact_sha256<>cal_hash
     OR r.calibration_artifact_id<>c.calibrator_id::text
     OR r.calibration_version<>c.calibration_version
     OR r.model_artifact_id<>a.artifact_id::text
     OR r.model_artifact_sha256<>a.artifact_checksum
     OR a.artifact_checksum<>s.model_output_snapshot->>'artifact_checksum'
     OR r.official_event_id<>s.official_event_id
     OR r.event_start_time<>s.event_start_time_utc
     OR r.selected_participant<>s.selected_participant
     OR r.home_team<>s.home_team OR r.away_team<>s.away_team
     OR r.model_timestamp<>s.model_timestamp
     OR r.source_snapshot_timestamp<>s.source_snapshot_timestamp
     OR r.source_snapshot_id<>s.source_snapshot_id::text
     OR r.model_version<>s.model_version OR r.calibration_version<>s.calibration_version
     OR r.calibrated_home_probability<>s.calibrated_home_probability
     OR r.calibrated_away_probability<>s.calibrated_away_probability
     OR r.calibrated_home_lower_bound<>s.calibrated_home_lower_bound
     OR r.calibrated_home_upper_bound<>s.calibrated_home_upper_bound
     OR r.calibrated_away_lower_bound<>s.calibrated_away_lower_bound
     OR r.calibrated_away_upper_bound<>s.calibrated_away_upper_bound
     OR r.terminal_reducer_payload->>'status'<>'PASS'
     OR r.terminal_reducer_payload->>'global_terminal_reducer'<>'V17_TERMINAL_REDUCER'
     OR r.terminal_reducer_payload->>'terminal_label'<>'FINAL_APPROVED' THEN
    RETURN pg_catalog.jsonb_build_object('status','HOLD','can_execute',false);
  END IF;
  present:=public.wow_v17_reduce_nfl_event_terminal_label(r.score_snapshot_id);
  IF r.terminal_reducer_payload IS DISTINCT FROM present
    OR present->>'status'<>'PASS' OR present->>'global_terminal_reducer'<>'V17_TERMINAL_REDUCER'
    OR present->>'terminal_label'<>'FINAL_APPROVED'
    OR present->>'can_execute'<>'false' OR present->>'probability_publishable'<>'true'
    OR present->>'rank_eligible'<>'true' THEN
    RETURN pg_catalog.jsonb_build_object('status','HOLD','can_execute',false);
  END IF;
  RETURN pg_catalog.jsonb_build_object(
    'status','PASS','terminal_label','FINAL_APPROVED',
    'terminal_authority','V17_TERMINAL_REDUCER',
    'event_prediction_id',r.event_prediction_id,
    'score_snapshot_id',r.score_snapshot_id,
    'reducer_receipt_id',r.reducer_receipt_id,
    'reducer_receipt_sha256',r.reducer_receipt_sha256,
    'probability_publishable',true,'rank_eligible',true,'can_execute',false
  );
END $f$;

-- Principle of least privilege. No direct app INSERT/UPDATE/DELETE.
REVOKE EXECUTE ON FUNCTION public.wow_v17_multisport_receipt_identity() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.wow_v17_multisport_receipt_immutable() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.wow_v17_admit_nfl_event_receipt(uuid) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.wow_v17_verify_multisport_event_receipt(uuid) FROM PUBLIC;
DO $f$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='anon') THEN
    REVOKE ALL ON public.wow_v17_multisport_event_prediction_receipts FROM anon;
    REVOKE EXECUTE ON FUNCTION public.wow_v17_admit_nfl_event_receipt(uuid) FROM anon;
    REVOKE EXECUTE ON FUNCTION public.wow_v17_verify_multisport_event_receipt(uuid) FROM anon;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='authenticated') THEN
    REVOKE ALL ON public.wow_v17_multisport_event_prediction_receipts FROM authenticated;
    REVOKE EXECUTE ON FUNCTION public.wow_v17_admit_nfl_event_receipt(uuid) FROM authenticated;
    REVOKE EXECUTE ON FUNCTION public.wow_v17_verify_multisport_event_receipt(uuid) FROM authenticated;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='service_role') THEN
    REVOKE ALL ON public.wow_v17_multisport_event_prediction_receipts FROM service_role;
    GRANT SELECT ON public.wow_v17_multisport_event_prediction_receipts TO service_role;
    GRANT EXECUTE ON FUNCTION public.wow_v17_admit_nfl_event_receipt(uuid) TO service_role;
    GRANT EXECUTE ON FUNCTION public.wow_v17_verify_multisport_event_receipt(uuid) TO service_role;
  END IF;
END $f$;
COMMIT;
-- No runtime flag change, no deployment, no model promotion, no real data write.
-- This source must not be applied before independent acceptance + release.
