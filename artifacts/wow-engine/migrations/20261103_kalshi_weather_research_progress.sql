-- Durable, research-only cursor for bounded Miami hourly settlement recovery.
-- Apply only through the protected release path before enabling the caller.
-- This table contains NO forecasts, outcome substitutions, or trading authority.

create table if not exists public.wow_kalshi_weather_research_progress (
    station_scope text primary key
      check (station_scope = 'KALSHI_WEATHER_INDEX:MIAMI'),
    next_offset integer not null default 0 check (next_offset >= 0),
    claim_sequence bigint not null default 0 check (claim_sequence >= 0),
    updated_at timestamptz not null default now()
);
alter table public.wow_kalshi_weather_research_progress enable row level security;
revoke all on table public.wow_kalshi_weather_research_progress from public, anon, authenticated;
grant select, insert, update on table public.wow_kalshi_weather_research_progress to service_role;

-- A single transaction serializes position claims across workers/retries.
-- The cursor advances BEFORE attempts, so missing/nonfinal official points
-- cannot strand later eligible records or create fabricated outcomes.
create or replace function public.wow_kalshi_weather_claim_research_window(
    p_station_scope text,
    p_population integer,
    p_attempt_limit integer
)
returns table (rotation_offset integer, claim_sequence bigint)
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_offset integer;
    v_sequence bigint;
begin
    if p_station_scope is distinct from 'KALSHI_WEATHER_INDEX:MIAMI'
       or p_population is null or p_population < 1 or p_population > 500
       or p_attempt_limit is null or p_attempt_limit < 1 or p_attempt_limit > 3
    then
        raise exception 'WEATHER_RESEARCH_PROGRESS_SCOPE_INVALID';
    end if;

    insert into public.wow_kalshi_weather_research_progress(station_scope)
    values ('KALSHI_WEATHER_INDEX:MIAMI')
    on conflict (station_scope) do nothing;

    select next_offset, claim_sequence
      into v_offset, v_sequence
      from public.wow_kalshi_weather_research_progress
      where station_scope = 'KALSHI_WEATHER_INDEX:MIAMI'
      for update;

    if not found or v_sequence is null then
        raise exception 'WEATHER_RESEARCH_PROGRESS_UNAVAILABLE';
    end if;

    rotation_offset := mod(v_offset, p_population);
    claim_sequence := v_sequence + 1;

    update public.wow_kalshi_weather_research_progress
       set next_offset = mod(rotation_offset + p_attempt_limit, p_population),
           claim_sequence = v_sequence + 1,
           updated_at = now()
     where station_scope = 'KALSHI_WEATHER_INDEX:MIAMI';

    return next;
end;
$$;

revoke all on function public.wow_kalshi_weather_claim_research_window(text, integer, integer)
  from public, anon, authenticated;
grant execute on function public.wow_kalshi_weather_claim_research_window(text, integer, integer)
  to service_role;
