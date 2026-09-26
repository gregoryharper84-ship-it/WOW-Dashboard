-- Forward hardening for the immutable V17 team/event specialist certification registry.
revoke all privileges on public.wow_team_event_specialist_certifications
    from public, anon, authenticated, service_role;
grant select, insert on public.wow_team_event_specialist_certifications to service_role;
