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
  source_pr bigint CHECK (source_pr IS NULL OR source_pr > 0),
  source_sha text NOT NULL DEFAULT '' CHECK (source_sha = '' OR source_sha ~ '^[0-9a-f]{40}$'),
  draft boolean NOT NULL DEFAULT true,
  CONSTRAINT wow_writer_mutation_replay_identity CHECK (
    (source_pr IS NULL AND source_sha = '' AND draft IS TRUE)
    OR (source_pr IS NOT NULL AND source_sha ~ '^[0-9a-f]{40}$' AND draft IS FALSE)
  ),
  state text NOT NULL CHECK (state IN ('PENDING','CONFIRMED')),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  confirmed_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS wow_engineering_one_pending_mutation_per_incident
 ON public.wow_engineering_writer_mutation_intents (incident_id) WHERE state='PENDING';
ALTER TABLE public.wow_engineering_writer_mutation_intents ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.wow_engineering_writer_mutation_intents FROM PUBLIC,anon,authenticated;
-- Explicitly undo even Supabase default service_role DML grants.
-- Writes are permitted only through narrow, reviewed definer RPCs.
REVOKE ALL ON public.wow_engineering_writer_mutation_intents FROM service_role;
GRANT SELECT ON public.wow_engineering_writer_mutation_intents TO service_role;
-- Supabase default privileges may already have granted ALL to service_role.
-- Providers must never receive this still-trusted gateway credential.
REVOKE DELETE,TRUNCATE,TRIGGER,REFERENCES ON public.wow_engineering_writer_mutation_intents FROM service_role;
REVOKE DELETE,TRUNCATE,TRIGGER,REFERENCES ON public.wow_engineering_incident_writer_leases FROM service_role;

-- Irreversible admission tombstone. Expiry, renewal and provider restart must
-- never reopen an epoch which the finalizer has begun draining.
CREATE TABLE IF NOT EXISTS public.wow_engineering_closed_writer_epochs (
 incident_id bigint NOT NULL REFERENCES public.wow_engineering_incident_writer_leases(incident_id),
 epoch bigint NOT NULL CHECK (epoch>0), owner_id text NOT NULL,
 lease_group text NOT NULL, closed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY (incident_id,epoch)
);
ALTER TABLE public.wow_engineering_closed_writer_epochs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.wow_engineering_closed_writer_epochs FROM PUBLIC,anon,authenticated;
-- Direct tombstone insertion could bypass owner/epoch validation.
REVOKE ALL ON public.wow_engineering_closed_writer_epochs FROM service_role;
GRANT SELECT ON public.wow_engineering_closed_writer_epochs TO service_role;
REVOKE UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES ON public.wow_engineering_closed_writer_epochs FROM service_role;
CREATE OR REPLACE FUNCTION public.wow_engineering_preserve_closed_epoch()
 RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
 SET search_path=pg_catalog,public AS $$
BEGIN
 RAISE EXCEPTION 'WRITER_EPOCH_CLOSURE_IMMUTABLE' USING ERRCODE='55000';
END $$;
DROP TRIGGER IF EXISTS wow_engineering_closed_epoch_immutable ON public.wow_engineering_closed_writer_epochs;
CREATE TRIGGER wow_engineering_closed_epoch_immutable
 BEFORE UPDATE OR DELETE ON public.wow_engineering_closed_writer_epochs
 FOR EACH ROW EXECUTE FUNCTION public.wow_engineering_preserve_closed_epoch();

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
 IF TG_OP='DELETE' THEN
   RAISE EXCEPTION 'WRITER_LEASE_DELETE_FORBIDDEN' USING ERRCODE='55000';
 END IF;
 IF TG_OP='UPDATE' THEN
   IF NEW.incident_id IS DISTINCT FROM OLD.incident_id THEN
     RAISE EXCEPTION 'WRITER_LEASE_IDENTITY_IMMUTABLE' USING ERRCODE='55000';
   END IF;
   IF EXISTS (SELECT 1 FROM public.wow_engineering_writer_mutation_intents m
              WHERE m.incident_id=OLD.incident_id AND m.state='PENDING')
      AND (NEW.owner_id IS DISTINCT FROM OLD.owner_id
        OR NEW.epoch IS DISTINCT FROM OLD.epoch
        OR NEW.lease_group IS DISTINCT FROM OLD.lease_group
        OR NEW.branch IS DISTINCT FROM OLD.branch
        OR NEW.pr_number IS DISTINCT FROM OLD.pr_number
        OR NEW.state IS DISTINCT FROM OLD.state) THEN
     RAISE EXCEPTION 'WRITER_MUTATION_IN_FLIGHT' USING ERRCODE='55000';
   END IF;
   IF (NEW.owner_id IS DISTINCT FROM OLD.owner_id
       OR NEW.epoch IS DISTINCT FROM OLD.epoch
       OR NEW.lease_group IS DISTINCT FROM OLD.lease_group
       OR NEW.state IS DISTINCT FROM OLD.state) THEN
     IF NOT EXISTS (SELECT 1 FROM public.wow_engineering_closed_writer_epochs c
                    WHERE c.incident_id=OLD.incident_id AND c.epoch=OLD.epoch
                      AND c.owner_id=OLD.owner_id AND c.lease_group=OLD.lease_group) THEN
       RAISE EXCEPTION 'WRITER_EPOCH_NOT_CLOSED' USING ERRCODE='55000';
     END IF;
     IF NEW.epoch < OLD.epoch OR (NEW.state='HELD' AND NEW.epoch<=OLD.epoch) THEN
       RAISE EXCEPTION 'WRITER_EPOCH_NOT_MONOTONIC' USING ERRCODE='55000';
     END IF;
   END IF;
 END IF;
 IF NEW.state='HELD' AND EXISTS (
   SELECT 1 FROM public.wow_engineering_closed_writer_epochs c
   WHERE c.incident_id=NEW.incident_id AND c.epoch>=NEW.epoch
 ) AND (
   -- Legacy claim uses INSERT(epoch=1) ON CONFLICT DO UPDATE(epoch=old+1).
   -- Its BEFORE INSERT proposal is not the acquired epoch. Existing incident
   -- rows are checked by the UPDATE trigger against the actual increment.
   (TG_OP='INSERT' AND NOT EXISTS (
     SELECT 1 FROM public.wow_engineering_incident_writer_leases l
     WHERE l.incident_id=NEW.incident_id))
   OR (TG_OP='UPDATE' AND (NEW.epoch IS DISTINCT FROM OLD.epoch
        OR NEW.owner_id IS DISTINCT FROM OLD.owner_id
        OR NEW.lease_group IS DISTINCT FROM OLD.lease_group
        OR NEW.state IS DISTINCT FROM OLD.state))
 ) THEN
   RAISE EXCEPTION 'WRITER_EPOCH_CLOSED' USING ERRCODE='55000';
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
 BEFORE INSERT OR UPDATE OR DELETE ON public.wow_engineering_incident_writer_leases
 FOR EACH ROW EXECUTE FUNCTION public.wow_engineering_enforce_lease_fence();

CREATE OR REPLACE FUNCTION public.wow_engineering_begin_writer_mutation(
 p_mutation_id uuid, p_incident_id bigint, p_owner_id text, p_epoch bigint,
 p_lease_group text, p_operation text, p_branch text, p_sha text, p_digest text,
 p_source_pr bigint DEFAULT NULL, p_source_sha text DEFAULT '', p_draft boolean DEFAULT true
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog AS $$
DECLARE l public.wow_engineering_incident_writer_leases%rowtype;
        m public.wow_engineering_writer_mutation_intents%rowtype;
BEGIN
 -- A nullable predicate must never skip a denial in PL/pgSQL IF.
 IF (p_mutation_id IS NOT NULL AND p_incident_id>0 AND p_epoch>0
    AND p_owner_id ~ '^[A-Za-z0-9:._/-]+$' AND length(p_owner_id) BETWEEN 8 AND 256
    AND p_lease_group ~ '^(GLOBAL|P0_[A-Z0-9_]+)$'
    AND p_operation IN ('CREATE_BRANCH','PUSH_REF','CREATE_PR')
    AND p_source_sha IS NOT NULL AND p_draft IS NOT NULL
    AND (
       (p_source_pr IS NULL AND p_source_sha='' AND p_draft IS TRUE
         AND p_branch ~ ('^wow-engineering/fenced-' || p_incident_id::text ||
                         '-' || p_epoch::text || '-[a-z0-9-]{8,64}$'))
       OR
       (p_source_pr > 0 AND p_source_sha ~ '^[0-9a-f]{40}$'
         AND p_draft IS FALSE AND p_operation IN ('PUSH_REF','CREATE_PR')
         AND p_branch = ('wow-engineering/exact-replay-' || p_source_pr::text ||
                         '-' || left(p_source_sha,12)))
    ) AND
    p_sha ~ '^[0-9a-f]{40}$' AND p_digest ~ '^[0-9a-f]{64}$') IS NOT TRUE THEN
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
   IF m.incident_id IS DISTINCT FROM p_incident_id OR m.owner_id IS DISTINCT FROM p_owner_id OR
      m.epoch IS DISTINCT FROM p_epoch OR m.lease_group IS DISTINCT FROM p_lease_group OR
      m.operation IS DISTINCT FROM p_operation OR m.branch IS DISTINCT FROM p_branch OR m.sha IS DISTINCT FROM p_sha OR
      m.payload_digest IS DISTINCT FROM p_digest OR m.source_pr IS DISTINCT FROM p_source_pr OR
      m.source_sha IS DISTINCT FROM p_source_sha OR m.draft IS DISTINCT FROM p_draft THEN
     RETURN jsonb_build_object('status','DENIED');
   END IF;
   RETURN jsonb_build_object('status',m.state);
 END IF;
 IF (l.incident_id=p_incident_id AND l.owner_id=p_owner_id AND l.epoch=p_epoch
    AND l.lease_group=p_lease_group AND l.state='HELD'
    AND l.expires_at>clock_timestamp()) IS NOT TRUE THEN
   RETURN jsonb_build_object('status','DENIED');
 END IF;
 IF EXISTS (SELECT 1 FROM public.wow_engineering_closed_writer_epochs c
            WHERE c.incident_id=p_incident_id AND c.epoch>=p_epoch) THEN
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
  (mutation_id,incident_id,owner_id,epoch,lease_group,operation,branch,sha,
   payload_digest,source_pr,source_sha,draft,state)
 VALUES (p_mutation_id,p_incident_id,p_owner_id,p_epoch,p_lease_group,p_operation,
         p_branch,p_sha,p_digest,p_source_pr,p_source_sha,p_draft,'PENDING');
 RETURN jsonb_build_object('status','STARTED');
END $$;

-- Close admission BEFORE observing provider termination. Both close and begin
-- serialize before locking the row, so a concurrent write is either durably
-- PENDING or denied. DRAINED never releases the lease by itself.
CREATE OR REPLACE FUNCTION public.wow_engineering_close_writer_epoch(
 p_incident_id bigint,p_owner_id text,p_epoch bigint,p_lease_group text
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog AS $$
DECLARE l public.wow_engineering_incident_writer_leases%rowtype;
BEGIN
 IF (p_incident_id>0 AND p_epoch>0
     AND p_owner_id ~ '^[A-Za-z0-9:._/-]+$' AND length(p_owner_id) BETWEEN 8 AND 256
     AND p_lease_group ~ '^(GLOBAL|P0_[A-Z0-9_]+)$') IS NOT TRUE THEN
   RETURN jsonb_build_object('status','DENIED');
 END IF;
 PERFORM pg_catalog.pg_advisory_xact_lock(901734,1739);
 SELECT * INTO l FROM public.wow_engineering_incident_writer_leases
 WHERE incident_id=p_incident_id FOR UPDATE;
 IF (l.incident_id=p_incident_id AND l.owner_id=p_owner_id AND l.epoch=p_epoch
     AND l.lease_group=p_lease_group AND l.state='HELD') IS NOT TRUE THEN
   RETURN jsonb_build_object('status','DENIED');
 END IF;
 INSERT INTO public.wow_engineering_closed_writer_epochs
   (incident_id,epoch,owner_id,lease_group)
 VALUES (p_incident_id,p_epoch,p_owner_id,p_lease_group)
 ON CONFLICT (incident_id,epoch) DO NOTHING;
 IF NOT EXISTS (SELECT 1 FROM public.wow_engineering_closed_writer_epochs c
                WHERE c.incident_id=p_incident_id AND c.epoch=p_epoch
                  AND c.owner_id=p_owner_id AND c.lease_group=p_lease_group) THEN
   RETURN jsonb_build_object('status','DENIED');
 END IF;
 IF EXISTS (SELECT 1 FROM public.wow_engineering_writer_mutation_intents m
            WHERE m.incident_id=p_incident_id AND m.state='PENDING') THEN
   RETURN jsonb_build_object('status','DRAINING');
 END IF;
 RETURN jsonb_build_object('status','DRAINED');
END $$;

-- Caller must independently read GitHub exact branch/head/body before
-- completing; verified audit evidence is immutable.
CREATE OR REPLACE FUNCTION public.wow_engineering_confirm_writer_mutation(
 p_mutation_id uuid,p_incident_id bigint,p_owner_id text,p_epoch bigint,p_digest text
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog AS $$
BEGIN
 UPDATE public.wow_engineering_writer_mutation_intents
 SET state='CONFIRMED',confirmed_at=clock_timestamp()
 WHERE mutation_id=p_mutation_id AND incident_id=p_incident_id
   AND owner_id=p_owner_id AND epoch=p_epoch
   AND payload_digest=p_digest AND state='PENDING';
 RETURN FOUND;
END $$;

REVOKE ALL ON FUNCTION public.wow_engineering_enforce_lease_fence() FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.wow_engineering_preserve_closed_epoch() FROM PUBLIC,anon,authenticated;
REVOKE ALL ON FUNCTION public.wow_engineering_close_writer_epoch(bigint,text,bigint,text) FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION public.wow_engineering_close_writer_epoch(bigint,text,bigint,text) TO service_role;
REVOKE ALL ON FUNCTION public.wow_engineering_begin_writer_mutation(uuid,bigint,text,bigint,text,text,text,text,text,bigint,text,boolean) FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.wow_engineering_confirm_writer_mutation(uuid,bigint,text,bigint,text) FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION public.wow_engineering_begin_writer_mutation(uuid,bigint,text,bigint,text,text,text,text,text,bigint,text,boolean) TO service_role;
GRANT EXECUTE ON FUNCTION public.wow_engineering_confirm_writer_mutation(uuid,bigint,text,bigint,text) TO service_role;
-- Review production lease identity, full privilege inventory, legacy CLAIM
-- paths, live HELD rows, bounded 55P03 retries and rollback before activation.

-- Fail closed if the definer principal differs from the audited migration role.
-- This is a REVIEW_ONLY candidate; migration execution requires independent approval.
DO $wow_owner_guard$
DECLARE f regprocedure;
BEGIN
 FOREACH f IN ARRAY ARRAY[
  'public.wow_engineering_begin_writer_mutation(uuid,bigint,text,bigint,text,text,text,text,text,bigint,text,boolean)'::regprocedure,
  'public.wow_engineering_close_writer_epoch(bigint,text,bigint,text)'::regprocedure,
  'public.wow_engineering_confirm_writer_mutation(uuid,bigint,text,bigint,text)'::regprocedure
 ] LOOP
  IF (SELECT pg_catalog.pg_get_userbyid(proowner)
      FROM pg_catalog.pg_proc WHERE oid=f::oid) IS DISTINCT FROM 'postgres' THEN
    RAISE EXCEPTION 'WRITER_RPC_OWNER_UNTRUSTED: %', f USING ERRCODE='42501';
  END IF;
 END LOOP;
END $wow_owner_guard$;
