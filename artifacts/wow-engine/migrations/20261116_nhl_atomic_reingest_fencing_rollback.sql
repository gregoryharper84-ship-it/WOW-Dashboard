-- Rollback for the independently reviewed 20261116 NHL re-ingest fencing RPCs.
-- Must be authorized only when the NHL fitted scorer/re-ingest route is held.
-- No source events, forecasts, artifacts, or receipts are deleted.
-- Clients using this version now fail closed (RPC unavailable).
drop function if exists public.wow_nhl_persist_reingest_timeline(text,text,jsonb);
drop function if exists public.wow_nhl_append_reingest_source_batch(text,text,jsonb);
drop function if exists public.wow_nhl_release_reingest_lease(text,text);
drop function if exists public.wow_nhl_claim_reingest_lease(text,text);
-- Deliberately do not restore unfenced service_role direct timeline writes.
