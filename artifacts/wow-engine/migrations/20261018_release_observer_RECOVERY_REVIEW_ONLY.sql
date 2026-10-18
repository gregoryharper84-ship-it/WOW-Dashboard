-- P0 #1834 INDEPENDENT RECOVERY CANDIDATE — REVIEW ONLY, isolated CI use.
-- This file is NOT a production migration or proof of source trust.
-- Caller-provided provider facts MUST be verified by the separately governed
-- observation worker against Render API (complete inventory) + protected main.
-- The DB enforces role separation, immutable identity and forward-only state.
-- An expired deployer lease NEVER releases the outstanding intent or permits a POST.

CREATE OR REPLACE FUNCTION wow_release_control.record_provider_ack(
  p_service_id text, p_target_sha text, p_run_id text, p_fence bigint,
  p_provider_deploy_id text, p_provider_created_at timestamptz,
  p_complete_unique_observation boolean
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_state text;
BEGIN
  IF session_user::text <> 'wow_release_observer' THEN
    RAISE EXCEPTION 'RELEASE_OBSERVER_IDENTITY_UNVERIFIED';
  END IF;
  IF p_provider_deploy_id IS NULL OR p_provider_deploy_id !~ '^dep-[a-z0-9]{8,64}$'
     OR p_provider_created_at IS NULL OR p_complete_unique_observation IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'RELEASE_PROVIDER_OBSERVATION_INCOMPLETE';
  END IF;
  -- Reconciliation is allowed after lease expiry but only for the original
  -- immutable run/fence and target SHA; it does NOT create a new POST intent.
  UPDATE wow_release_control.release_intent i
    SET state='PROVIDER_ACK', provider_deploy_id=p_provider_deploy_id,
        observed_at=clock_timestamp()
    WHERE i.service_id=p_service_id AND i.target_sha=p_target_sha
      AND i.run_id=p_run_id AND i.fence=p_fence
      AND i.state='POST_UNCERTAIN'
      AND i.post_reserved_at <= p_provider_created_at
      AND EXISTS (
        SELECT 1 FROM wow_release_control.service_fence f
        WHERE f.service_id=i.service_id AND f.target_sha=i.target_sha
          AND f.run_id=i.run_id AND f.fence=i.fence
      )
    RETURNING i.state INTO v_state;
  IF v_state IS DISTINCT FROM 'PROVIDER_ACK' THEN
    RAISE EXCEPTION 'RELEASE_PROVIDER_ACK_IDENTITY_OR_STATE_HOLD';
  END IF;
  RETURN v_state;
END;
$$;

CREATE OR REPLACE FUNCTION wow_release_control.record_live_observation(
  p_service_id text, p_target_sha text, p_run_id text, p_fence bigint,
  p_provider_deploy_id text, p_provider_live_sha text,
  p_current_protected_main_sha text, p_render_status text,
  p_exhaustive_source_readback boolean
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_state text;
BEGIN
  IF session_user::text <> 'wow_release_observer' THEN
    RAISE EXCEPTION 'RELEASE_OBSERVER_IDENTITY_UNVERIFIED';
  END IF;
  IF p_provider_live_sha IS DISTINCT FROM p_target_sha
     OR p_current_protected_main_sha IS DISTINCT FROM p_target_sha
     OR p_render_status IS DISTINCT FROM 'live'
     OR p_exhaustive_source_readback IS DISTINCT FROM true
     OR p_provider_deploy_id IS NULL OR p_provider_deploy_id !~ '^dep-[a-z0-9]{8,64}$'
  THEN RAISE EXCEPTION 'RELEASE_LIVE_EVIDENCE_INCOMPLETE_OR_STALE'; END IF;
  UPDATE wow_release_control.release_intent i
    SET state='LIVE_OBSERVED', observed_at=clock_timestamp()
    WHERE i.service_id=p_service_id AND i.target_sha=p_target_sha
      AND i.run_id=p_run_id AND i.fence=p_fence
      AND i.provider_deploy_id=p_provider_deploy_id
      AND i.state='PROVIDER_ACK'
      AND EXISTS (
        SELECT 1 FROM wow_release_control.service_fence f
        WHERE f.service_id=i.service_id AND f.target_sha=i.target_sha
          AND f.run_id=i.run_id AND f.fence=i.fence
      )
    RETURNING i.state INTO v_state;
  IF v_state IS DISTINCT FROM 'LIVE_OBSERVED' THEN
    RAISE EXCEPTION 'RELEASE_LIVE_IDENTITY_OR_STATE_HOLD';
  END IF;
  RETURN v_state;
END;
$$;

CREATE OR REPLACE FUNCTION wow_release_control.independent_verify_release(
  p_service_id text, p_target_sha text, p_run_id text, p_fence bigint,
  p_provider_deploy_id text, p_acceptance_receipt_sha text,
  p_independent_qa_approval boolean, p_security_approval boolean,
  p_release_authority_approval boolean
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_state text;
BEGIN
  IF session_user::text <> 'wow_release_verifier' THEN
    RAISE EXCEPTION 'RELEASE_VERIFIER_IDENTITY_UNVERIFIED';
  END IF;
  IF p_acceptance_receipt_sha IS NULL
     OR p_acceptance_receipt_sha !~ '^[0-9a-f]{64}$'
     OR p_independent_qa_approval IS DISTINCT FROM true
     OR p_security_approval IS DISTINCT FROM true
     OR p_release_authority_approval IS DISTINCT FROM true
  THEN RAISE EXCEPTION 'RELEASE_INDEPENDENT_ACCEPTANCE_EVIDENCE_MISSING'; END IF;
  -- No promotion can be authorized from boolean values supplied by a caller.
  -- This function is intentionally a non-advancing HOLD until a distinct
  -- Security/QA/Release approved and cryptographically verified acceptance
  -- receipt reader exists. No DB role can self-certify via this candidate.
  RAISE EXCEPTION 'RELEASE_VERIFICATION_SIGNED_ATTESTATION_NOT_CERTIFIED_HOLD';
END;
$$;

REVOKE ALL ON FUNCTION wow_release_control.record_provider_ack(text,text,text,bigint,text,timestamptz,boolean)
  FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION wow_release_control.record_live_observation(text,text,text,bigint,text,text,text,text,boolean)
  FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION wow_release_control.independent_verify_release(text,text,text,bigint,text,text,boolean,boolean,boolean)
  FROM PUBLIC, anon, authenticated;

ALTER FUNCTION wow_release_control.record_provider_ack(text,text,text,bigint,text,timestamptz,boolean)
  OWNER TO wow_release_fence_owner;
ALTER FUNCTION wow_release_control.record_live_observation(text,text,text,bigint,text,text,text,text,boolean)
  OWNER TO wow_release_fence_owner;
ALTER FUNCTION wow_release_control.independent_verify_release(text,text,text,bigint,text,text,boolean,boolean,boolean)
  OWNER TO wow_release_fence_owner;

GRANT USAGE ON SCHEMA wow_release_control TO wow_release_observer, wow_release_verifier;
GRANT EXECUTE ON FUNCTION
  wow_release_control.record_provider_ack(text,text,text,bigint,text,timestamptz,boolean),
  wow_release_control.record_live_observation(text,text,text,bigint,text,text,text,text,boolean)
  TO wow_release_observer;
GRANT EXECUTE ON FUNCTION
  wow_release_control.independent_verify_release(text,text,text,bigint,text,text,boolean,boolean,boolean)
  TO wow_release_verifier;
