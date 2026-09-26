-- Forward hardening for the immutable V17 team/event specialist certification registry.
-- The original create migration revoked PUBLIC/anon/authenticated privileges but did
-- not revoke service_role defaults before granting SELECT/INSERT.  Explicitly reduce
-- service_role to the intended append-only surface so TRUNCATE/UPDATE/DELETE cannot
-- bypass row immutability guarantees.

revoke all privileges on public.wow_team_event_specialist_certifications
    from public, anon, authenticated, service_role;
grant select, insert on public.wow_team_event_specialist_certifications to service_role;
