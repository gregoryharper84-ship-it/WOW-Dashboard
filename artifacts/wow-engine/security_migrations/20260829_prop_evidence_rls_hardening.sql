-- WOW production security hardening — REVIEW/APPLY SEPARATELY.
-- This file is intentionally not auto-applied by Agent Runtime migrations.
-- Current evidence (2026-08-29): public.wow_prop_evidence_snapshots has RLS
-- disabled and anon/authenticated hold broad table privileges. The production
-- API calls wow_prop_evidence_snapshot() server-side with backend credentials;
-- browser/client roles do not require direct table or RPC access.

begin;

alter table public.wow_prop_evidence_snapshots enable row level security;
revoke all privileges on table public.wow_prop_evidence_snapshots from anon, authenticated;
revoke execute on function public.wow_prop_evidence_snapshot(uuid,text,text,text,text,numeric) from anon, authenticated;

-- Deliberately no anon/authenticated policies are created. Server-side service
-- role access remains the intended path. Validate the API bridge after applying.

commit;
