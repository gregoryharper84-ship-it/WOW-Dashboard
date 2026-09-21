-- Harden runtime ACLs after Supabase default privileges are applied.
-- The service role may update resumable current-state tables, but transition
-- receipts are append-only and no runtime table may be deleted/truncated.

revoke all on table public.wow_pick_request_runs from service_role;
revoke all on table public.wow_pick_request_row_states from service_role;
revoke all on table public.wow_pick_request_row_transitions from service_role;

grant select, insert, update on table public.wow_pick_request_runs to service_role;
grant select, insert, update on table public.wow_pick_request_row_states to service_role;
grant select, insert on table public.wow_pick_request_row_transitions to service_role;
