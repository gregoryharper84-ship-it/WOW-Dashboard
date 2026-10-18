-- REVIEW CANDIDATE ONLY. DO NOT APPLY before production release DB and role
-- provenance, ownership, and exclusive Render-writer inventory are certified.
-- Not public/Data-API exposed. Grant NOTHING from this migration.
CREATE SCHEMA IF NOT EXISTS wow_release_control;
REVOKE ALL ON SCHEMA wow_release_control FROM PUBLIC, anon, authenticated;

CREATE TABLE IF NOT EXISTS wow_release_control.service_fence (
  service_id text PRIMARY KEY CHECK (service_id ~ '^srv-[a-z0-9]{8,64}$'),
  fence bigint NOT NULL DEFAULT 0 CHECK (fence >= 0),
  owner text,
  run_id text,
  target_sha text,
  lease_expires_at timestamptz NOT NULL DEFAULT '-infinity'::timestamptz,
  CHECK (target_sha IS NULL OR target_sha ~ '^[0-9a-f]{40}$')
);

CREATE TABLE IF NOT EXISTS wow_release_control.release_intent (
  service_id text NOT NULL REFERENCES wow_release_control.service_fence(service_id),
  target_sha text NOT NULL CHECK (target_sha ~ '^[0-9a-f]{40}$'),
  run_id text NOT NULL,
  owner text NOT NULL,
  fence bigint NOT NULL CHECK (fence > 0),
  state text NOT NULL CHECK (state IN
    ('RESERVED','POST_UNCERTAIN','PROVIDER_ACK','LIVE_OBSERVED','VERIFIED')),
  post_reserved_at timestamptz,
  provider_deploy_id text UNIQUE,
  observed_at timestamptz,
  verified_at timestamptz,
  verification_receipt_sha text,
  PRIMARY KEY (service_id, target_sha),
  CHECK (state = 'RESERVED' OR post_reserved_at IS NOT NULL),
  CHECK (state NOT IN ('PROVIDER_ACK','LIVE_OBSERVED','VERIFIED') OR provider_deploy_id IS NOT NULL),
  CHECK (state <> 'VERIFIED' OR
    (verified_at IS NOT NULL AND verification_receipt_sha ~ '^[0-9a-f]{64}$'))
);

-- Database-enforced guard in addition to the service_fence SELECT ... FOR UPDATE.
-- This index blocks multiple concurrently unresolved intents PER SERVICE and
-- is deliberately independent of lease expiry. VERIFIED is not unresolved.
CREATE UNIQUE INDEX IF NOT EXISTS release_one_unresolved_per_service
 ON wow_release_control.release_intent(service_id)
 WHERE state IN ('RESERVED','POST_UNCERTAIN','PROVIDER_ACK','LIVE_OBSERVED');

-- Defensive immutable identity + forward-only state transition. Privileged
-- table owners can replace DDL; production identity and migration access must
-- therefore be independently attested before these controls are relied upon.
CREATE OR REPLACE FUNCTION wow_release_control.protect_intent()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'RELEASE_INTENT_DELETE_FORBIDDEN';
  END IF;
  IF (OLD.service_id, OLD.target_sha, OLD.run_id, OLD.owner, OLD.fence)
     IS DISTINCT FROM
     (NEW.service_id, NEW.target_sha, NEW.run_id, NEW.owner, NEW.fence) THEN
    RAISE EXCEPTION 'RELEASE_INTENT_IDENTITY_IMMUTABLE';
  END IF;
  IF NOT (
     (OLD.state='RESERVED' AND NEW.state='POST_UNCERTAIN') OR
     (OLD.state='POST_UNCERTAIN' AND NEW.state='PROVIDER_ACK') OR
     (OLD.state='PROVIDER_ACK' AND NEW.state='LIVE_OBSERVED') OR
     (OLD.state='LIVE_OBSERVED' AND NEW.state='VERIFIED')
  ) THEN
    RAISE EXCEPTION 'RELEASE_INTENT_STATE_NOT_MONOTONIC';
  END IF;
  IF OLD.post_reserved_at IS NOT NULL
     AND NEW.post_reserved_at IS DISTINCT FROM OLD.post_reserved_at THEN
    RAISE EXCEPTION 'RELEASE_POST_RESERVATION_IMMUTABLE';
  END IF;
  IF OLD.provider_deploy_id IS NOT NULL
     AND NEW.provider_deploy_id IS DISTINCT FROM OLD.provider_deploy_id THEN
    RAISE EXCEPTION 'RELEASE_PROVIDER_ID_IMMUTABLE';
  END IF;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION wow_release_control.protect_intent() FROM PUBLIC, anon, authenticated;
DROP TRIGGER IF EXISTS protect_release_intent ON wow_release_control.release_intent;
CREATE TRIGGER protect_release_intent BEFORE UPDATE OR DELETE
 ON wow_release_control.release_intent FOR EACH ROW
 EXECUTE FUNCTION wow_release_control.protect_intent();

REVOKE ALL ON ALL TABLES IN SCHEMA wow_release_control FROM PUBLIC, anon, authenticated;
-- No GRANT to a release principal here. Restricted DB identity and separate
-- certified migration must establish limited DML/function rights.
