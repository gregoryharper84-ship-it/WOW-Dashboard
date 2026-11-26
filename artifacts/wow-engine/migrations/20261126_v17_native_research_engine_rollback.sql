-- Rollback for 20261126_v17_native_research_engine (evidence-only tables; no model data).
drop function if exists public.wow_nre_search(text, integer, text, text, text, text, text, text, text, text, text,
  text, text, timestamptz, timestamptz, timestamptz);
drop function if exists public.wow_nre_claim_task(text, text, integer);
drop table if exists public.wow_nre_findings;
drop table if exists public.wow_nre_documents;
drop table if exists public.wow_nre_source_rights;
drop table if exists public.wow_nre_research_attempts;
drop table if exists public.wow_nre_research_tasks;
drop function if exists public.wow_nre_server_clock_guard();
drop function if exists public.wow_nre_task_guard();
