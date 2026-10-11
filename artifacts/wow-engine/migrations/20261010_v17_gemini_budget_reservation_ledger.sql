-- WOW Gemini standby: centrally durable spend reservation and append-only events.
-- NOT ENABLED: all budget rows start disabled, zero money, no third-party calls.
-- Owner-reviewed controls: service_role only; no anon/authenticated grant or policy.
-- Class B proposed migration; run in a reviewed Postgres/Supabase environment only.
CREATE TABLE IF NOT EXISTS public.wow_gemini_budget_controls (
  project_key text PRIMARY KEY CHECK (project_key ~ '^[a-zA-Z0-9_-]{1,80}$'),
  enabled boolean NOT NULL DEFAULT false,
  cap_microusd bigint NOT NULL DEFAULT 0 CHECK (cap_microusd >= 0),
  max_single_request_microusd bigint NOT NULL DEFAULT 0
    CHECK (max_single_request_microusd >= 0),
  approved_by text,
  approved_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT wow_gemini_budget_authorization CHECK (
    (NOT enabled) OR (
      cap_microusd > 0 AND max_single_request_microusd > 0
      AND max_single_request_microusd <= cap_microusd
      AND approved_by IS NOT NULL AND length(trim(approved_by)) > 0
      AND approved_at IS NOT NULL
    )
  )
);
CREATE TABLE IF NOT EXISTS public.wow_gemini_spend_reservations (
  reservation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_key text NOT NULL REFERENCES public.wow_gemini_budget_controls(project_key),
  manifest_sha256 text NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
  kind text NOT NULL CHECK (kind IN ('BATCH_INLINE','CACHE_CREATE','STANDARD')),
  budget_day date NOT NULL,
  reserved_microusd bigint NOT NULL CHECK (reserved_microusd > 0),
  approval_ref text NOT NULL CHECK (length(approval_ref) BETWEEN 3 AND 160),
  tariff_sha256 text NOT NULL CHECK (tariff_sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now(),
  can_execute boolean NOT NULL DEFAULT false CHECK (can_execute=false),
  UNIQUE (project_key, manifest_sha256)
);
CREATE INDEX IF NOT EXISTS wow_gemini_reservations_budget_idx
  ON public.wow_gemini_spend_reservations (project_key, budget_day);

CREATE TABLE IF NOT EXISTS public.wow_gemini_spend_events (
  event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  reservation_id uuid NOT NULL REFERENCES public.wow_gemini_spend_reservations(reservation_id),
  stage text NOT NULL CHECK (stage IN
    ('RESERVED','PROVIDER_CONFIRMED','OUTCOME_UNKNOWN','RECONCILED','TERMINAL_HOLD')),
  provider_name text,
  recorded_at timestamptz NOT NULL DEFAULT now(),
  can_execute boolean NOT NULL DEFAULT false CHECK (can_execute=false),
  UNIQUE (reservation_id, stage)
);
CREATE INDEX IF NOT EXISTS wow_gemini_spend_events_lookup_idx
  ON public.wow_gemini_spend_events (reservation_id, event_id DESC);

ALTER TABLE public.wow_gemini_budget_controls ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.wow_gemini_spend_reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.wow_gemini_spend_events ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.wow_gemini_budget_controls FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON public.wow_gemini_spend_reservations FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON public.wow_gemini_spend_events FROM PUBLIC, anon, authenticated, service_role;
-- Read-only visibility to the server; mutating access ONLY via atomic RPCs.
GRANT SELECT ON public.wow_gemini_budget_controls TO service_role;
GRANT SELECT ON public.wow_gemini_spend_reservations TO service_role;
GRANT SELECT ON public.wow_gemini_spend_events TO service_role;

-- Append-only event history; RPCs can insert but no one can rewrite provenance.
CREATE OR REPLACE FUNCTION public.wow_gemini_events_immutable()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path = public, pg_temp AS $$
BEGIN
  RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='WOW_GEMINI_EVENTS_IMMUTABLE';
END $$;
REVOKE ALL ON FUNCTION public.wow_gemini_events_immutable() FROM PUBLIC, anon, authenticated;
DROP TRIGGER IF EXISTS wow_gemini_events_guard ON public.wow_gemini_spend_events;
CREATE TRIGGER wow_gemini_events_guard BEFORE UPDATE OR DELETE OR TRUNCATE
 ON public.wow_gemini_spend_events FOR EACH STATEMENT
 EXECUTE FUNCTION public.wow_gemini_events_immutable();
-- No SECURITY DEFINER in the exposed public schema. SECURITY INVOKER RPCs
-- rely on the service_role's scoped INSERT permission, never a broad public grant.
GRANT INSERT ON public.wow_gemini_spend_reservations TO service_role;
GRANT INSERT ON public.wow_gemini_spend_events TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.wow_gemini_spend_events_event_id_seq TO service_role;

CREATE OR REPLACE FUNCTION public.wow_gemini_reserve_spend(
  p_project_key text, p_manifest_sha256 text, p_kind text,
  p_reserved_microusd bigint, p_approval_ref text, p_tariff_sha256 text
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path = public, pg_temp AS $$
DECLARE
  ctl public.wow_gemini_budget_controls%ROWTYPE;
  total_reserved bigint;
  rid uuid;
  today_utc date := (now() at time zone 'UTC')::date;
BEGIN
  IF p_project_key IS NULL OR p_project_key !~ '^[a-zA-Z0-9_-]{1,80}$'
    OR p_manifest_sha256 IS NULL OR p_manifest_sha256 !~ '^[0-9a-f]{64}$'
    OR p_tariff_sha256 IS NULL OR p_tariff_sha256 !~ '^[0-9a-f]{64}$'
    OR p_kind IS NULL OR p_kind NOT IN ('BATCH_INLINE','CACHE_CREATE','STANDARD')
    OR p_reserved_microusd IS NULL OR p_reserved_microusd <= 0
    OR p_approval_ref IS NULL OR length(p_approval_ref) NOT BETWEEN 3 AND 160
  THEN
    RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='GEMINI_RESERVATION_INPUT_INVALID';
  END IF;
  -- Serialize all budget reservations for the same project, including
  -- distinct manifest hashes; this prevents simultaneous cap oversubscription.
  SELECT * INTO ctl FROM public.wow_gemini_budget_controls
    WHERE project_key=p_project_key FOR UPDATE;
  IF NOT FOUND OR NOT ctl.enabled THEN
    RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='GEMINI_BUDGET_DISABLED';
  END IF;
  IF ctl.max_single_request_microusd < p_reserved_microusd THEN
    RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='GEMINI_REQUEST_BUDGET_EXCEEDED';
  END IF;
  IF EXISTS(SELECT 1 FROM public.wow_gemini_spend_reservations
            WHERE project_key=p_project_key AND manifest_sha256=p_manifest_sha256) THEN
    RAISE EXCEPTION USING ERRCODE='23505', MESSAGE='GEMINI_DUPLICATE_MANIFEST';
  END IF;
  SELECT coalesce(sum(reserved_microusd), 0) INTO total_reserved
    FROM public.wow_gemini_spend_reservations
    WHERE project_key=p_project_key AND budget_day=today_utc;
  IF total_reserved > ctl.cap_microusd - p_reserved_microusd THEN
    RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='GEMINI_DAILY_BUDGET_EXCEEDED';
  END IF;
  INSERT INTO public.wow_gemini_spend_reservations
    (project_key, manifest_sha256, kind, budget_day, reserved_microusd,
     approval_ref, tariff_sha256)
    VALUES (p_project_key, p_manifest_sha256, p_kind, today_utc,
            p_reserved_microusd, p_approval_ref, p_tariff_sha256)
    RETURNING reservation_id INTO rid;
  INSERT INTO public.wow_gemini_spend_events(reservation_id,stage)
    VALUES (rid,'RESERVED');
  RETURN jsonb_build_object('status','RESERVED','reservation_id',rid,
    'manifest_sha256',p_manifest_sha256,'reserved_microusd',p_reserved_microusd,
    'can_execute',false);
END $$;
REVOKE ALL ON FUNCTION public.wow_gemini_reserve_spend(text,text,text,bigint,text,text)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.wow_gemini_reserve_spend(text,text,text,bigint,text,text)
  TO service_role;

CREATE OR REPLACE FUNCTION public.wow_gemini_record_spend_stage(
  p_reservation_id uuid, p_stage text, p_provider_name text DEFAULT NULL
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path = public, pg_temp AS $$
DECLARE
  prior text;
  kind_name text;
  existing_provider text;
BEGIN
  IF p_stage NOT IN ('PROVIDER_CONFIRMED','OUTCOME_UNKNOWN','RECONCILED','TERMINAL_HOLD') THEN
    RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='GEMINI_STAGE_INVALID';
  END IF;
  -- Lock the associated budget-control row to serialize concurrent stage calls.
  PERFORM 1 FROM public.wow_gemini_budget_controls b
    JOIN public.wow_gemini_spend_reservations r USING (project_key)
    WHERE r.reservation_id=p_reservation_id FOR UPDATE OF b;
  SELECT r.kind, e.stage, e.provider_name INTO kind_name,prior,existing_provider
    FROM public.wow_gemini_spend_reservations r
    JOIN LATERAL (SELECT stage,provider_name FROM public.wow_gemini_spend_events
      WHERE reservation_id=r.reservation_id ORDER BY event_id DESC LIMIT 1) e ON TRUE
    WHERE r.reservation_id=p_reservation_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='GEMINI_RESERVATION_MISSING';
  END IF;
  IF NOT (
    (prior='RESERVED' AND p_stage IN ('PROVIDER_CONFIRMED','OUTCOME_UNKNOWN','TERMINAL_HOLD'))
    OR (prior='PROVIDER_CONFIRMED' AND p_stage IN ('RECONCILED','OUTCOME_UNKNOWN','TERMINAL_HOLD'))
    OR (prior='OUTCOME_UNKNOWN' AND p_stage IN ('PROVIDER_CONFIRMED','TERMINAL_HOLD'))
  ) THEN
    RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='GEMINI_STAGE_TRANSITION_REJECTED';
  END IF;
  IF p_provider_name IS NOT NULL AND (length(p_provider_name)>160
    OR (kind_name='BATCH_INLINE' AND p_provider_name !~ '^batches/[A-Za-z0-9_-]{1,128}$')
    OR (kind_name='CACHE_CREATE' AND p_provider_name !~ '^cachedContents/[A-Za-z0-9_-]{1,128}$'))
  THEN
    RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='GEMINI_PROVIDER_ID_INVALID';
  END IF;
  IF p_stage='PROVIDER_CONFIRMED' AND p_provider_name IS NULL THEN
    RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='GEMINI_PROVIDER_ID_REQUIRED';
  END IF;
  IF existing_provider IS NOT NULL AND p_provider_name IS NOT NULL
    AND existing_provider <> p_provider_name THEN
    RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='GEMINI_PROVIDER_ID_MISMATCH';
  END IF;
  INSERT INTO public.wow_gemini_spend_events (reservation_id,stage,provider_name)
    VALUES (p_reservation_id,p_stage,coalesce(p_provider_name,existing_provider));
  RETURN jsonb_build_object('status',p_stage,'reservation_id',p_reservation_id,
                            'can_execute',false);
END $$;
REVOKE ALL ON FUNCTION public.wow_gemini_record_spend_stage(uuid,text,text)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.wow_gemini_record_spend_stage(uuid,text,text)
  TO service_role;
-- Enabling a project budget requires separately authorized DBA action and
-- production accounting verification. No INSERT or UPDATE privilege is granted
-- for controls to the runtime service_role.
