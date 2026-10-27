-- #1739 / #2136. REVIEW_ONLY. Never apply until protected exact-SHA review.
-- Builds on existing public.wow_engineering_incident_writer_leases.
-- Providers MUST NOT retain write tokens or service_role credentials.
-- A DB lock does not extend across GitHub I/O: durable PENDING intent does.
CREATE TABLE IF NOT EXISTS public.wow_engineering_writer_mutation_intents (
  mutation_id uuid PRIMARY KEY,
  incident_id bigint NOT NULL REFERENCES public.wow_engineering_incident_writer_leases(incident_id),
  owner_id text NOT NULL,
  epoch bigint NOT NULL CHECK (epoch > 0),
  lease_group text NOT NULL,
  operation text NOT NULL CHECK (operation IN ('CREATE_BRANCH','PUSH_REF','CREATE_PR')),
  branch text NOT NULL,
  sha text NOT NULL CHECK (sha ~ '^[0-9a-f]{40}$'),
  payload_digest text NOT NULL CHECK (payload_digest ~ '^[0-9a-f]{64}$'),
  state text NOT NULL CHECK (state IN ('PENDING','CONFIRMED')),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  confirmed_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS wow_engineering_one_pending_mutation_per_incident
 ON public.wow_engineering_writer_mutation_intents (incident_id) WHERE state='PENDING';
ALTER TABLE public.wow_engineering_writer_mutation_intents ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.wow_engineering_writer_mutation_intents FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE ON public.wow_engineering_writer_mutation_intents TO service_role;

-- Protect legacy claim/reclaim/release RPCs and GLOBAL-vs-domain exclusivity.
-- IMPORTANT: an UPDATE may already hold the row lock when its trigger fires.
-- Never BLOCK waiting on a global advisory lock inside that trigger: begin()
-- may already hold the global advisory lock and wait on this very row lock.
-- Fail closed with typed 55P03 instead. Caller must use bounded retry/reentry.
CREATE OR REPLACE FUNCTION public.wow_engineering_enforce_lease_fence()
 RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
 SET search_path=pg_catalog,public AS $$
DECLARE conflict boolean;
BEGIN
 IF NOT pg_catalog.pg_try_advisory_xact_lock(901734,1739) THEN
   RAISE EXCEPTION 'WRITER_LEASE_TRANSITION_RETRY' USING ERRCODE='55P03';
 END IF;
 IF TG_OP='UPDATE' THEN
   IF EXISTS (SELECT 1 FROM public.wow_engineering_writer_mutation_intents m
              WHERE m.incident_id=OLD.incident_id AND m.state='PENDING')
      AND (NEW.owner_id IS DISTINCT FROM OLD.owner_id
        OR NEW.epoch IS DISTINCT FROM OLD.epoch
        OR NEW.lease_group IS DISTINCT FROM OLD.lease_group
        OR NEW.state IS DISTINCT FROM OLD.state) THEN
     RAISE EXCEPTION 'WRITER_MUTATION_IN_FLIGHT' USING ERRCODE='55000';
   END IF;
 END IF;
 IF NEW.state='HELD' AND NEW.expires_at > clock_timestamp() THEN
   SELECT EXISTS (
     SELECT 1 FROM public.wow_engineering_incident_writer_leases l
     WHERE l.incident_id<>NEW.incident_id AND l.state='HELD'
       AND l.expires_at>clock_timestamp()
       AND (NEW.lease_group='GLOBAL' OR l.lease_group='GLOBAL'
            OR NEW.lease_group=l.lease_group)
   ) INTO conflict;
   IF NOT conflict THEN
     SELECT EXISTS (
       SELECT 1 FROM public.wow_engineering_writer_mutation_intents m
       WHERE m.incident_id<>NEW.incident_id AND m.state='PENDING'
         AND (NEW.lease_group='GLOBAL' OR m.lease_group='GLOBAL'
              OR NEW.lease_group=m.lease_group)
     ) INTO conflict;
   END IF;
   IF conflict THEN
     RAISE EXCEPTION 'WRITER_LEASE_GROUP_CONFLICT' USING ERRCODE='55000';
   END IF;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS wow_engineering_atomic_group_fence ON public.wow_engineering_incident_writer_leases;
CREATE TRIGGER wow_engineering_atomic_group_fence
 BEFORE INSERT OR UPDATE ON public.wow_engineering_incident_writer_leases
 FOR EACH ROW EXECUTE FUNCTION public.wow_engineering_enforce_lease_fence();

CREATE OR REPLACE FUNCTION public.wow_engineering_begin_writer_mutation(
 p_mutation_id uuid, p_incident_id bigint, p_owner_id text, p_epoch bigint,
 p_lease_group text, p_operation text, p_branch text, p_sha text, p_digest text
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER
 SET search_path=pg_catalog,public AS $$
DECLARE l public.wow_engineering_incident_writer_leases%rowtype;
        m public.wow_engineering_writer_mutation_intents%rowtype;
BEGIN
 IF p_mutation_id IS NULL OR p_incident_id IS NULL OR p_incident_id<=0 OR
    p_epoch IS NULL OR p_epoch<=0 OR p_owner_id IS NULL OR p_lease_group IS NULL OR
    p_operation NOT IN ('CREATE_BRANCH','PUSH_REF','CREATE_PR') OR
    p_branch !~ '^wow-engineering/fenced-[0-9]+-[0-9]+-[a-z0-9-]+$' OR
    p_sha !~ '^[0-9a-f]{40}$' OR p_digest !~ '^[0-9a-f]{64}$' THEN
   RETURN jsonb_build_object('status','DENIED');
 END IF;
 -- Serialize across different incident IDs before taking an incident row lock.
 PERFORM pg_catalog.pg_advisory_xact_lock(901734,1739);
 SELECT * INTO l FROM public.wow_engineering_incident_writer_leases
 WHERE incident_id=p_incident_id FOR UPDATE;
 -- Existing pending/confirmed intent is reconciliation-only, never re-POST.
 SELECT * INTO m FROM public.wow_engineering_writer_mutation_intents
 WHERE mutation_id=p_mutation_id FOR UPDATE;
 IF FOUND THEN
   IF m.incident_id<>p_incident_id OR m.owner_id<>p_owner_id OR
      m.epoch<>p_epoch OR m.lease_group<>p_lease_group OR
      m.operation<>p_operation OR m.branch<>p_branch OR m.sha<>p_sha OR
      m.payload_digest<>p_digest THEN
     RETURN jsonb_build_object('status','DENIED');
   END IF;
   RETURN jsonb_build_object('status',m.state);
 END IF;
 IF l.incident_id IS NULL OR l.owner_id<>p_owner_id OR l.epoch<>p_epoch
    OR l.lease_group<>p_lease_group OR l.state<>'HELD'
    OR l.expires_at<=clock_timestamp() THEN
   RETURN jsonb_build_object('status','DENIED');
 END IF;
 IF EXISTS (SELECT 1 FROM public.wow_engineering_incident_writer_leases other
            WHERE other.incident_id<>p_incident_id AND other.state='HELD'
              AND other.expires_at>clock_timestamp()
              AND (l.lease_group='GLOBAL' OR other.lease_group='GLOBAL'
                   OR other.lease_group=l.lease_group)) THEN
   RETURN jsonb_build_object('status','DENIED');
 END IF;
 IF EXISTS (SELECT 1 FROM public.wow_engineering_writer_mutation_intents y
            WHERE y.incident_id<>p_incident_id AND y.state='PENDING'
              AND (l.lease_group='GLOBAL' OR y.lease_group='GLOBAL'
                   OR y.lease_group=l.lease_group)) THEN
   RETURN jsonb_build_object('status','DENIED');
 END IF;
 IF EXISTS (SELECT 1 FROM public.wow_engineering_writer_mutation_intents x
            WHERE x.incident_id=p_incident_id AND x.state='PENDING') THEN
   RETURN jsonb_build_object('status','DENIED');
 END IF;
 INSERT INTO public.wow_engineering_writer_mutation_intents
  (mutation_id,incident_id,owner_id,epoch,lease_group,operation,branch,sha,payload_digest,state)
 VALUES (p_mutation_id,p_incident_id,p_owner_id,p_epoch,p_lease_group,p_operation,
         p_branch,p_sha,p_digest,'PENDING');
 RETURN jsonb_build_object('status','STARTED');
END $$;

-- Caller must independently read GitHub exact branch/head/body before
-- completing; verified audit evidence is immutable.
CREATE OR REPLACE FUNCTION public.wow_engineering_confirm_writer_mutation(
 p_mutation_id uuid,p_incident_id bigint,p_owner_id text,p_epoch bigint,p_digest text
) RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER
 SET search_path=pg_catalog,public AS $$
BEGIN
 UPDATE public.wow_engineering_writer_mutation_intents
 SET state='CONFIRMED',confirmed_at=clock_timestamp()
 WHERE mutation_id=p_mutation_id AND incident_id=p_incident_id
   AND owner_id=p_owner_id AND epoch=p_epoch
   AND payload_digest=p_digest AND state='PENDING';
 RETURN FOUND;
END $$;

REVOKE ALL ON FUNCTION public.wow_engineering_enforce_lease_fence() FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.wow_engineering_begin_writer_mutation(uuid,bigint,text,bigint,text,text,text,text,text) FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.wow_engineering_confirm_writer_mutation(uuid,bigint,text,bigint,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.wow_engineering_begin_writer_mutation(uuid,bigint,text,bigint,text,text,text,text,text) TO service_role;
GRANT EXECUTE ON FUNCTION public.wow_engineering_confirm_writer_mutation(uuid,bigint,text,bigint,text) TO service_role;
-- Review production lease identity, full privilege inventory, legacy CLAIM
-- paths, live HELD rows, bounded 55P03 retries and rollback before activation.
