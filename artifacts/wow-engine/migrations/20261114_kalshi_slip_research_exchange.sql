-- Governed, append-only research relay. It grants no probability, staking or
-- trading rights and is not joined into model input/certification tables.
CREATE TABLE IF NOT EXISTS public.wow_kalshi_slip_research_exchange (
  record_id text PRIMARY KEY CHECK (record_id ~ '^[0-9a-f]{64}$'),
  schema_version text NOT NULL CHECK (schema_version = 'WOW_V17_KALSHI_SLIP_RESEARCH_EXCHANGE_V1'),
  source_host text NOT NULL CHECK (source_host IN ('LLP_TEAM_BETTING_ENGINE','WOW_BETTING_ENGINE')),
  scope text NOT NULL CHECK (scope IN ('EVENT','COHORT')),
  sport text NOT NULL,
  reference_id text NOT NULL,
  market_ref text,
  finding_type text NOT NULL,
  finding_code text NOT NULL,
  analysis_stage text NOT NULL CHECK (analysis_stage IN ('PREGAME_RESEARCH','POSTMORTEM_RESEARCH')),
  as_of_utc timestamptz NOT NULL,
  source_observed_at_utc timestamptz NOT NULL,
  event_start_utc timestamptz,
  evidence_refs jsonb NOT NULL CHECK (jsonb_typeof(evidence_refs) = 'array'),
  evidence_status text NOT NULL DEFAULT 'UNVERIFIED_RESEARCH' CHECK (evidence_status='UNVERIFIED_RESEARCH'),
  source_host_attested boolean NOT NULL DEFAULT false CHECK (source_host_attested=false),
  model_feature_eligible boolean NOT NULL DEFAULT false CHECK (model_feature_eligible=false),
  probability_publishable boolean NOT NULL DEFAULT false CHECK (probability_publishable=false),
  can_execute boolean NOT NULL DEFAULT false CHECK (can_execute=false),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT kalshi_research_chronology CHECK (source_observed_at_utc <= as_of_utc),
  CONSTRAINT kalshi_research_event_scope CHECK (
    (scope='EVENT' AND event_start_utc IS NOT NULL AND sport <> 'MIXED')
    OR (scope='COHORT' AND event_start_utc IS NULL AND market_ref IS NULL)
  ),
  CONSTRAINT kalshi_research_stage_time CHECK (
    (analysis_stage='PREGAME_RESEARCH' AND event_start_utc IS NOT NULL AND as_of_utc < event_start_utc)
    OR (analysis_stage='POSTMORTEM_RESEARCH' AND
        (event_start_utc IS NULL OR as_of_utc >= event_start_utc))
  )
);
CREATE INDEX IF NOT EXISTS wow_kalshi_slip_research_lookup_idx
  ON public.wow_kalshi_slip_research_exchange (scope, reference_id, created_at DESC);
ALTER TABLE public.wow_kalshi_slip_research_exchange ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.wow_kalshi_slip_research_exchange FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT, INSERT ON public.wow_kalshi_slip_research_exchange TO service_role;

-- Defense in depth: even a privileged API caller may not rewrite research
-- history. Corrections are new observations with new record IDs.
CREATE OR REPLACE FUNCTION public.wow_kalshi_slip_research_immutable()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'KALSHI_RESEARCH_APPEND_ONLY';
END;
$$;
REVOKE ALL ON FUNCTION public.wow_kalshi_slip_research_immutable() FROM PUBLIC, anon, authenticated;
DROP TRIGGER IF EXISTS wow_kalshi_slip_research_no_mutation ON public.wow_kalshi_slip_research_exchange;
CREATE TRIGGER wow_kalshi_slip_research_no_mutation
  BEFORE UPDATE OR DELETE ON public.wow_kalshi_slip_research_exchange
  FOR EACH ROW EXECUTE FUNCTION public.wow_kalshi_slip_research_immutable();
