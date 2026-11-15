-- Validation environments initially applied the permissive three-valued CHECK.
-- Recreate it with an explicit non-null event timestamp for pregame research.
ALTER TABLE public.wow_kalshi_slip_research_exchange
  DROP CONSTRAINT IF EXISTS kalshi_research_stage_time;
ALTER TABLE public.wow_kalshi_slip_research_exchange
  ADD CONSTRAINT kalshi_research_stage_time CHECK (
    (analysis_stage='PREGAME_RESEARCH'
      AND event_start_utc IS NOT NULL
      AND as_of_utc < event_start_utc)
    OR (analysis_stage='POSTMORTEM_RESEARCH'
      AND (event_start_utc IS NULL OR as_of_utc >= event_start_utc))
  );
