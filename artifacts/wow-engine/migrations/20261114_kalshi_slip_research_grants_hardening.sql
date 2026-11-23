-- Follow-up hardening for environments where the initial migration inherited
-- Supabase's default service_role UPDATE/DELETE/TRUNCATE grants. Append-only
-- trigger already denied UPDATE/DELETE, but the grants must fail closed too.
REVOKE ALL ON public.wow_kalshi_slip_research_exchange
    FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT, INSERT ON public.wow_kalshi_slip_research_exchange TO service_role;
