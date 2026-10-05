-- Complete least-privilege hardening for the research-only NFL ML challenger ledger.
-- #1352 removed client-role grants, but service_role had inherited additional
-- public-schema privileges before the intended SELECT/INSERT/UPDATE grant.

revoke all privileges on table public.wow_nfl_ml_challenger_forward_shadow
  from service_role;

grant select, insert, update on table public.wow_nfl_ml_challenger_forward_shadow
  to service_role;
