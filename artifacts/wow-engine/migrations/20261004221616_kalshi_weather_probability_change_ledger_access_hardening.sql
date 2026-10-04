-- Least-privilege hardening for the Kalshi Weather probability-change ledger.
-- Prevent TRUNCATE and other default table privileges from bypassing append-only semantics.

revoke all privileges on table public.wow_kalshi_weather_probability_changes
  from anon, authenticated, service_role;

grant select, insert on table public.wow_kalshi_weather_probability_changes
  to service_role;
