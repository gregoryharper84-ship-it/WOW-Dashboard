-- Kalshi Weather V2 ledger access hardening.
-- Runtime contract: server-side service_role may append/read immutable evidence.
-- Browser/public roles have no direct table privileges.
-- No trading/execution capability is introduced.

do $$
declare
  table_name text;
begin
  foreach table_name in array array[
    'wow_kalshi_weather_contract_rules',
    'wow_kalshi_weather_source_snapshots',
    'wow_kalshi_weather_calibration_profiles',
    'wow_kalshi_weather_predictions',
    'wow_kalshi_weather_market_snapshots',
    'wow_kalshi_weather_outcomes'
  ]
  loop
    execute format('alter table public.%I enable row level security', table_name);

    -- Explicitly remove broad default/public API access.
    execute format('revoke all privileges on table public.%I from anon', table_name);
    execute format('revoke all privileges on table public.%I from authenticated', table_name);

    -- The server runtime is append-only. Immutability is also enforced by
    -- the existing BEFORE UPDATE OR DELETE triggers.
    execute format('revoke all privileges on table public.%I from service_role', table_name);
    execute format('grant select, insert on table public.%I to service_role', table_name);
  end loop;
end;
$$;

-- Intentionally create no anon/authenticated RLS policies. The dedicated
-- Render runtime is the analytical access boundary and uses service_role
-- server-side only. Supabase service_role bypasses RLS while table grants
-- still restrict it to SELECT/INSERT for these ledgers.
