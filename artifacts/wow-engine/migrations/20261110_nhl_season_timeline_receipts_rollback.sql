-- Rollback for 20261110_nhl_season_timeline_receipts.sql
--
-- Drops the NHL season timeline receipt and re-ingest lease tables introduced
-- for fitted-specialist scoring. Safe only when no production scoring path
-- depends on these tables. Does not touch wow_d1_source_events or candidate
-- artifacts. Never enables can_execute or probability publication.
drop table if exists public.wow_nhl_source_reingest_leases;
drop table if exists public.wow_nhl_season_timeline_receipts;
