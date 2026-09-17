-- WOW V17 MLB probability-publication handoff repair.
--
-- Root cause: the probability-only governance bridge can receive a valid fitted
-- MLB score snapshot and then advance directly into identity/source/readiness/
-- probability-audit/final-refresh gates without first invoking the certified
-- MLB event-governance evidence hydration handoff. The scorer probability is
-- therefore real, while downstream publication state can remain incomplete and
-- every row becomes non-rank-eligible.
--
-- This patch inserts the existing certified hydration helper before reconciliation
-- and publication gates. It does not fabricate evidence, weaken any gate, alter
-- calibrated probabilities/lower bounds, or authorize execution.
-- can_execute remains false.

DO $do$
DECLARE
  d text;
  old_sequence text;
  new_sequence text;
BEGIN
  SELECT pg_get_functiondef(
    'public.wow_v17_mlb_probability_only_governance_bridge(uuid,text,text,text,text,text)'::regprocedure
  ) INTO d;

  IF d IS NULL THEN
    RAISE EXCEPTION 'V17_MLB_PROBABILITY_ONLY_BRIDGE_MISSING';
  END IF;

  IF strpos(d, 'wow_v17_hydrate_mlb_event_governance_evidence(event_id,p_score_snapshot_id') = 0 THEN
    old_sequence :=
      'perform public.wow_v17_reconcile_probability_only_snapshot(event_id); identity:=public.wow_evaluate_event_identity_lock(event_id,now());';

    new_sequence :=
      'perform public.wow_v17_hydrate_mlb_event_governance_evidence(event_id,p_score_snapshot_id,''{}''::jsonb,p_decision_intent); '
      || 'perform public.wow_v17_reconcile_probability_only_snapshot(event_id); '
      || 'identity:=public.wow_evaluate_event_identity_lock(event_id,now());';

    IF strpos(d, old_sequence) > 0 THEN
      d := replace(d, old_sequence, new_sequence);
    ELSIF strpos(d, 'identity:=public.wow_evaluate_event_identity_lock(event_id,now());') > 0 THEN
      d := replace(
        d,
        'identity:=public.wow_evaluate_event_identity_lock(event_id,now());',
        'perform public.wow_v17_hydrate_mlb_event_governance_evidence(event_id,p_score_snapshot_id,''{}''::jsonb,p_decision_intent); '
        || 'perform public.wow_v17_reconcile_probability_only_snapshot(event_id); '
        || 'identity:=public.wow_evaluate_event_identity_lock(event_id,now());'
      );
    ELSE
      RAISE EXCEPTION 'V17_MLB_PROBABILITY_ONLY_BRIDGE_PATCH_POINT_NOT_FOUND';
    END IF;
  END IF;

  EXECUTE d;
END
$do$;

-- Fail-closed postcondition: the bridge definition must now contain the certified
-- hydration handoff before identity/source/readiness and final publication gates.
DO $verify$
DECLARE
  d text;
  hydrate_pos integer;
  identity_pos integer;
  source_pos integer;
  audit_pos integer;
  refresh_pos integer;
BEGIN
  SELECT pg_get_functiondef(
    'public.wow_v17_mlb_probability_only_governance_bridge(uuid,text,text,text,text,text)'::regprocedure
  ) INTO d;

  hydrate_pos := strpos(d, 'wow_v17_hydrate_mlb_event_governance_evidence(event_id,p_score_snapshot_id');
  identity_pos := strpos(d, 'wow_evaluate_event_identity_lock(event_id,now())');
  source_pos := strpos(d, 'wow_refresh_event_source_completeness(event_id,now())');
  audit_pos := strpos(d, 'wow_v17_audit_probability_only_event(event_id)');
  refresh_pos := strpos(d, 'wow_v17_probability_only_final_refresh(event_id');

  IF hydrate_pos = 0 THEN
    RAISE EXCEPTION 'V17_MLB_PUBLICATION_HANDOFF_HYDRATION_MISSING';
  END IF;
  IF identity_pos = 0 OR source_pos = 0 OR audit_pos = 0 OR refresh_pos = 0 THEN
    RAISE EXCEPTION 'V17_MLB_PUBLICATION_GATE_CHAIN_INCOMPLETE';
  END IF;
  IF NOT (hydrate_pos < identity_pos AND hydrate_pos < source_pos AND hydrate_pos < audit_pos AND hydrate_pos < refresh_pos) THEN
    RAISE EXCEPTION 'V17_MLB_PUBLICATION_HANDOFF_ORDER_INVALID';
  END IF;
  IF strpos(d, 'can_execute') = 0 THEN
    RAISE EXCEPTION 'V17_MLB_CAN_EXECUTE_GUARD_MISSING';
  END IF;
END
$verify$;
