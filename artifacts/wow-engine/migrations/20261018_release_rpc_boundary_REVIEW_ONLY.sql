-- REVIEW ONLY. Apply solely in the short-lived PostgreSQL CI test instance.
-- Production migration / roles / credentials / enabled API require independent
-- Security, CODEOWNER, QA, and Release authorization.
-- Prerequisite: create NOLOGIN wow_release_fence_owner with narrow DML on
-- private journal tables. The runtime wow_release_controller must authenticate
-- as a restricted database session_user (a dedicated LOGIN principal, not
-- the privileged owner); the ephemeral CI workflow alone provisions its LOGIN
-- and a disposable password. Production identity/TLS/secret custody is NOT
-- established here and requires independent Security/Release authorization.
-- After review, an independently approved production migration must bind only
-- the authenticated release-controller principal to EXECUTE, not journal DML.
CREATE OR REPLACE FUNCTION wow_release_control.reserve_release(
  p_service_id text, p_target_sha text, p_run_id text, p_ttl_seconds integer DEFAULT 180
) RETURNS TABLE(lease_fence bigint, reservation_state text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
  v_fence bigint;
  v_active boolean;
  v_new_fence bigint;
BEGIN
  IF session_user::text <> 'wow_release_controller' THEN
    RAISE EXCEPTION 'RELEASE_RPC_CALLER_IDENTITY_UNVERIFIED';
  END IF;
  IF p_service_id IS NULL OR p_service_id !~ '^srv-[a-z0-9]{8,64}$'
      OR p_target_sha IS NULL OR p_target_sha !~ '^[0-9a-f]{40}$'
      OR p_run_id IS NULL OR p_run_id !~ '^[0-9a-zA-Z_.:-]{1,128}$'
      OR p_ttl_seconds IS NULL OR p_ttl_seconds NOT BETWEEN 60 AND 900 THEN
    RAISE EXCEPTION 'RELEASE_RPC_INPUT_INVALID';
  END IF;
  INSERT INTO wow_release_control.service_fence(service_id)
    VALUES (p_service_id) ON CONFLICT (service_id) DO NOTHING;
  SELECT f.fence, f.lease_expires_at > clock_timestamp()
    INTO v_fence, v_active
    FROM wow_release_control.service_fence f
    WHERE f.service_id = p_service_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'RELEASE_RPC_SERVICE_FENCE_MISSING'; END IF;
  IF v_active THEN RAISE EXCEPTION 'RELEASE_SERVICE_LEASE_BUSY'; END IF;
  IF EXISTS (SELECT 1 FROM wow_release_control.release_intent i
             WHERE i.service_id=p_service_id AND i.state IN
             ('RESERVED','POST_UNCERTAIN','PROVIDER_ACK','LIVE_OBSERVED'))
  THEN RAISE EXCEPTION 'RELEASE_PREVIOUS_INTENT_UNRESOLVED_NO_REPOST'; END IF;
  IF EXISTS (SELECT 1 FROM wow_release_control.release_intent i
             WHERE i.service_id=p_service_id AND i.target_sha=p_target_sha)
  THEN RAISE EXCEPTION 'RELEASE_EXISTING_INTENT_NO_REPOST'; END IF;
  UPDATE wow_release_control.service_fence f SET
    fence=f.fence+1, owner=session_user::text, run_id=p_run_id,
    target_sha=p_target_sha,
    lease_expires_at=clock_timestamp()+(p_ttl_seconds*interval '1 second')
    WHERE f.service_id=p_service_id AND f.fence=v_fence
    RETURNING f.fence INTO v_new_fence;
  IF v_new_fence IS NULL THEN RAISE EXCEPTION 'RELEASE_FENCE_CAS_FAILED'; END IF;
  INSERT INTO wow_release_control.release_intent
    (service_id,target_sha,run_id,owner,fence,state)
    VALUES (p_service_id,p_target_sha,p_run_id,session_user::text,v_new_fence,'RESERVED')
    ON CONFLICT (service_id,target_sha) DO NOTHING;
  IF NOT FOUND THEN RAISE EXCEPTION 'RELEASE_EXISTING_INTENT_NO_REPOST'; END IF;
  RETURN QUERY SELECT v_new_fence, 'RESERVED'::text;
END;
$$;

CREATE OR REPLACE FUNCTION wow_release_control.reserve_render_post(
  p_service_id text, p_target_sha text, p_run_id text, p_fence bigint
) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_state text;
BEGIN
  IF session_user::text <> 'wow_release_controller' THEN
    RAISE EXCEPTION 'RELEASE_RPC_CALLER_IDENTITY_UNVERIFIED';
  END IF;
  UPDATE wow_release_control.release_intent i
    SET state='POST_UNCERTAIN',post_reserved_at=clock_timestamp()
    WHERE i.service_id=p_service_id AND i.target_sha=p_target_sha
      AND i.run_id=p_run_id AND i.owner=session_user::text AND i.fence=p_fence
      AND i.state='RESERVED'
      AND EXISTS (SELECT 1 FROM wow_release_control.service_fence f
                  WHERE f.service_id=i.service_id
                  AND f.target_sha=i.target_sha AND f.run_id=i.run_id
                  AND f.owner=session_user::text AND f.fence=i.fence
                  AND f.lease_expires_at>clock_timestamp())
    RETURNING i.state INTO v_state;
  IF v_state IS DISTINCT FROM 'POST_UNCERTAIN' THEN
    RAISE EXCEPTION 'RELEASE_POST_ALREADY_RESERVED_OR_FENCED';
  END IF;
  RETURN true;
END;
$$;

CREATE OR REPLACE FUNCTION wow_release_control.renew_release_lease(
  p_service_id text, p_target_sha text, p_run_id text,
  p_fence bigint, p_ttl_seconds integer DEFAULT 180
) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_fence bigint;
BEGIN
  IF session_user::text <> 'wow_release_controller' THEN
    RAISE EXCEPTION 'RELEASE_RPC_CALLER_IDENTITY_UNVERIFIED';
  END IF;
  IF p_ttl_seconds IS NULL OR p_ttl_seconds NOT BETWEEN 60 AND 900 THEN
    RAISE EXCEPTION 'RELEASE_TTL_OUT_OF_RANGE';
  END IF;
  UPDATE wow_release_control.service_fence f
    SET lease_expires_at=clock_timestamp()+(p_ttl_seconds*interval '1 second')
    WHERE f.service_id=p_service_id AND f.target_sha=p_target_sha
      AND f.run_id=p_run_id AND f.owner=session_user::text
      AND f.fence=p_fence AND f.lease_expires_at>clock_timestamp()
    RETURNING f.fence INTO v_fence;
  IF v_fence IS NULL THEN RAISE EXCEPTION 'RELEASE_LEASE_EXPIRED_OR_FENCED'; END IF;
  RETURN true;
END;
$$;

-- Protect the definer: no superuser postgres runtime, no public/API roles.
-- Roles below are isolated-test identities, not production DB grants.
REVOKE ALL ON FUNCTION wow_release_control.reserve_release(text,text,text,integer)
 FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION wow_release_control.reserve_render_post(text,text,text,bigint)
 FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION wow_release_control.renew_release_lease(text,text,text,bigint,integer)
 FROM PUBLIC, anon, authenticated;

ALTER FUNCTION wow_release_control.reserve_release(text,text,text,integer)
 OWNER TO wow_release_fence_owner;
ALTER FUNCTION wow_release_control.reserve_render_post(text,text,text,bigint)
 OWNER TO wow_release_fence_owner;
ALTER FUNCTION wow_release_control.renew_release_lease(text,text,text,bigint,integer)
 OWNER TO wow_release_fence_owner;

GRANT USAGE ON SCHEMA wow_release_control TO wow_release_controller;
GRANT EXECUTE ON FUNCTION wow_release_control.reserve_release(text,text,text,integer),
  wow_release_control.reserve_render_post(text,text,text,bigint),
  wow_release_control.renew_release_lease(text,text,text,bigint,integer)
  TO wow_release_controller;
REVOKE CREATE ON SCHEMA wow_release_control FROM wow_release_fence_owner;
