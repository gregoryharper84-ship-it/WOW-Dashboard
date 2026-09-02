-- WOW v17 event-ledger schema baseline guard.
--
-- These event tables predate repository migration capture. Rather than silently
-- recreating or mutating production tables during cutover, this migration makes
-- the accepted legacy baseline explicit and fail-closed. Fresh environments must
-- first restore the canonical event-ledger baseline; production cutover may only
-- continue when the exact required objects are present.

DO $$
DECLARE
  missing text[] := ARRAY[]::text[];
BEGIN
  IF to_regclass('public.wow_event_predictions') IS NULL THEN missing := array_append(missing, 'wow_event_predictions'); END IF;
  IF to_regclass('public.wow_calibrators') IS NULL THEN missing := array_append(missing, 'wow_calibrators'); END IF;
  IF to_regclass('public.wow_event_evidence') IS NULL THEN missing := array_append(missing, 'wow_event_evidence'); END IF;
  IF to_regclass('public.wow_event_scoring_evidence') IS NULL THEN missing := array_append(missing, 'wow_event_scoring_evidence'); END IF;

  IF cardinality(missing) > 0 THEN
    RAISE EXCEPTION 'V17_EVENT_SCHEMA_BASELINE_MISSING: %', array_to_string(missing, ',');
  END IF;

  -- Critical event-prediction governance columns.
  PERFORM 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='wow_event_predictions' AND column_name='event_prediction_id' AND udt_name='uuid' AND is_nullable='NO';
  IF NOT FOUND THEN RAISE EXCEPTION 'V17_EVENT_SCHEMA_DRIFT: wow_event_predictions.event_prediction_id'; END IF;
  PERFORM 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='wow_event_predictions' AND column_name='scoring_snapshot_id' AND udt_name='uuid';
  IF NOT FOUND THEN RAISE EXCEPTION 'V17_EVENT_SCHEMA_DRIFT: wow_event_predictions.scoring_snapshot_id'; END IF;
  PERFORM 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='wow_event_predictions' AND column_name='probability_audit_result' AND udt_name='text';
  IF NOT FOUND THEN RAISE EXCEPTION 'V17_EVENT_SCHEMA_DRIFT: wow_event_predictions.probability_audit_result'; END IF;
  PERFORM 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='wow_event_predictions' AND column_name='event_mutex_status' AND udt_name='text';
  IF NOT FOUND THEN RAISE EXCEPTION 'V17_EVENT_SCHEMA_DRIFT: wow_event_predictions.event_mutex_status'; END IF;
  PERFORM 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='wow_event_predictions' AND column_name='rank_eligible' AND udt_name='bool' AND is_nullable='NO';
  IF NOT FOUND THEN RAISE EXCEPTION 'V17_EVENT_SCHEMA_DRIFT: wow_event_predictions.rank_eligible'; END IF;
  PERFORM 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='wow_event_predictions' AND column_name='probability_publishable' AND udt_name='bool' AND is_nullable='NO';
  IF NOT FOUND THEN RAISE EXCEPTION 'V17_EVENT_SCHEMA_DRIFT: wow_event_predictions.probability_publishable'; END IF;
  PERFORM 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='wow_event_predictions' AND column_name='terminal_label' AND udt_name='text';
  IF NOT FOUND THEN RAISE EXCEPTION 'V17_EVENT_SCHEMA_DRIFT: wow_event_predictions.terminal_label'; END IF;
  PERFORM 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='wow_event_predictions' AND column_name='terminal_ceiling' AND udt_name='text';
  IF NOT FOUND THEN RAISE EXCEPTION 'V17_EVENT_SCHEMA_DRIFT: wow_event_predictions.terminal_ceiling'; END IF;
  PERFORM 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='wow_event_predictions' AND column_name='can_execute' AND udt_name='bool' AND is_nullable='NO' AND column_default='false';
  IF NOT FOUND THEN RAISE EXCEPTION 'V17_EVENT_SCHEMA_DRIFT: wow_event_predictions.can_execute'; END IF;

  -- Calibration baseline.
  PERFORM 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='wow_calibrators' AND column_name='calibrator_id' AND udt_name='uuid' AND is_nullable='NO';
  IF NOT FOUND THEN RAISE EXCEPTION 'V17_EVENT_SCHEMA_DRIFT: wow_calibrators.calibrator_id'; END IF;
  PERFORM 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='wow_calibrators' AND column_name='active' AND udt_name='bool' AND is_nullable='NO';
  IF NOT FOUND THEN RAISE EXCEPTION 'V17_EVENT_SCHEMA_DRIFT: wow_calibrators.active'; END IF;
  PERFORM 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='wow_calibrators' AND column_name='validation_status' AND udt_name='text' AND is_nullable='NO';
  IF NOT FOUND THEN RAISE EXCEPTION 'V17_EVENT_SCHEMA_DRIFT: wow_calibrators.validation_status'; END IF;
  PERFORM 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='wow_calibrators' AND column_name='health_status' AND udt_name='text' AND is_nullable='NO';
  IF NOT FOUND THEN RAISE EXCEPTION 'V17_EVENT_SCHEMA_DRIFT: wow_calibrators.health_status'; END IF;

  -- Immutable scoring-evidence linkage baseline.
  PERFORM 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='wow_event_evidence' AND column_name='event_prediction_id' AND udt_name='uuid' AND is_nullable='NO';
  IF NOT FOUND THEN RAISE EXCEPTION 'V17_EVENT_SCHEMA_DRIFT: wow_event_evidence.event_prediction_id'; END IF;
  PERFORM 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='wow_event_scoring_evidence' AND column_name='event_prediction_id' AND udt_name='uuid' AND is_nullable='NO';
  IF NOT FOUND THEN RAISE EXCEPTION 'V17_EVENT_SCHEMA_DRIFT: wow_event_scoring_evidence.event_prediction_id'; END IF;
  PERFORM 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='wow_event_scoring_evidence' AND column_name='scoring_snapshot_id' AND udt_name='uuid' AND is_nullable='NO';
  IF NOT FOUND THEN RAISE EXCEPTION 'V17_EVENT_SCHEMA_DRIFT: wow_event_scoring_evidence.scoring_snapshot_id'; END IF;

  -- Critical constraints proving execution remains disabled.
  PERFORM 1 FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid JOIN pg_namespace n ON n.oid=r.relnamespace
    WHERE n.nspname='public' AND r.relname='wow_event_predictions' AND c.conname='chk_event_can_execute_false';
  IF NOT FOUND THEN RAISE EXCEPTION 'V17_EVENT_SCHEMA_DRIFT: chk_event_can_execute_false'; END IF;
  PERFORM 1 FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid JOIN pg_namespace n ON n.oid=r.relnamespace
    WHERE n.nspname='public' AND r.relname='wow_event_evidence' AND c.conname='chk_event_evidence_execute_false';
  IF NOT FOUND THEN RAISE EXCEPTION 'V17_EVENT_SCHEMA_DRIFT: chk_event_evidence_execute_false'; END IF;
  PERFORM 1 FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid JOIN pg_namespace n ON n.oid=r.relnamespace
    WHERE n.nspname='public' AND r.relname='wow_event_scoring_evidence' AND c.conname='chk_event_scoring_evidence_execute_false';
  IF NOT FOUND THEN RAISE EXCEPTION 'V17_EVENT_SCHEMA_DRIFT: chk_event_scoring_evidence_execute_false'; END IF;

  -- V17 bridge and shared final gate functions must exist together.
  IF to_regprocedure('public.wow_run_event_postmodel_gates(uuid)') IS NULL THEN
    -- Signatures may evolve; fall back to name existence below.
    PERFORM 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname='wow_run_event_postmodel_gates';
    IF NOT FOUND THEN RAISE EXCEPTION 'V17_EVENT_SCHEMA_BASELINE_MISSING: wow_run_event_postmodel_gates'; END IF;
  END IF;
  PERFORM 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname='wow_run_event_final_gates';
  IF NOT FOUND THEN RAISE EXCEPTION 'V17_EVENT_SCHEMA_BASELINE_MISSING: wow_run_event_final_gates'; END IF;
  PERFORM 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname='wow_v17_mlb_team_event_governance_bridge';
  IF NOT FOUND THEN RAISE EXCEPTION 'V17_EVENT_SCHEMA_BASELINE_MISSING: wow_v17_mlb_team_event_governance_bridge'; END IF;
  PERFORM 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname='wow_v17_team_failure_path_gate';
  IF NOT FOUND THEN RAISE EXCEPTION 'V17_EVENT_SCHEMA_BASELINE_MISSING: wow_v17_team_failure_path_gate'; END IF;
END
$$;
