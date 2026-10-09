-- P1 #1579: atomic cross-worker paid-provider reservation.
-- Class B metering only: does not grant model, probability, or execution authority.
-- Install BEFORE adopting the Python caller changes; fail-closed until installed.
create table if not exists public.wow_rundown_budget_days (
  utc_day date primary key,
  reserved_calls integer not null default 0 check (reserved_calls >= 0),
  observed_datapoints bigint not null default 0 check (observed_datapoints >= 0),
  pending_calls integer not null default 0 check (pending_calls >= 0),
  unknown_usage integer not null default 0 check (unknown_usage >= 0),
  updated_at timestamptz not null default now()
);

create table if not exists public.wow_rundown_budget_requests (
  request_id text primary key check (request_id ~ '^[a-f0-9]{32}$'),
  utc_day date not null references public.wow_rundown_budget_days(utc_day),
  status text not null check (status in ('PENDING', 'SETTLED', 'UNKNOWN')),
  observed_datapoints integer check (observed_datapoints is null or observed_datapoints >= 0),
  created_at timestamptz not null default now(),
  finalized_at timestamptz
);
create index if not exists wow_rundown_budget_requests_day_idx
  on public.wow_rundown_budget_requests(utc_day);

alter table public.wow_rundown_budget_days enable row level security;
alter table public.wow_rundown_budget_requests enable row level security;
revoke all on public.wow_rundown_budget_days from public, anon, authenticated;
revoke all on public.wow_rundown_budget_requests from public, anon, authenticated;
-- RPCs run as the verified service_role, which has BYPASSRLS on Supabase.
-- Explicit grants are needed when using SECURITY INVOKER, and do not
-- expose these budget records to anon/authenticated clients.
grant select, insert, update on public.wow_rundown_budget_days to service_role;
grant select, insert, update on public.wow_rundown_budget_requests to service_role;

-- Rows are locked in a single transaction, not by a process-local mutex.
-- Exactly one unresolved provider request is permitted globally at any instant:
-- this deliberately serializes paid calls so a missing/crashed completion
-- cannot silently consume an unbounded number of paid datapoints.
create or replace function public.wow_rundown_reserve_call(
  p_request_id text, p_utc_day date, p_call_limit integer, p_point_limit bigint
) returns jsonb
language plpgsql security invoker set search_path = public, pg_temp as $$
declare
  rec public.wow_rundown_budget_days%rowtype;
begin
  if p_request_id is null or p_request_id !~ '^[a-f0-9]{32}$'
     or p_utc_day is null or p_call_limit is null or p_point_limit is null
     or p_call_limit < -1 or p_point_limit < -1
     or p_utc_day <> (now() at time zone 'UTC')::date then
    return jsonb_build_object('allowed', false, 'code', 'PAID_PROVIDER_BUDGET_ARGUMENT_INVALID');
  end if;
  insert into public.wow_rundown_budget_days(utc_day) values (p_utc_day)
    on conflict (utc_day) do nothing;
  select * into rec from public.wow_rundown_budget_days
    where utc_day = p_utc_day for update;
  if exists (select 1 from public.wow_rundown_budget_requests where request_id = p_request_id) then
    return jsonb_build_object('allowed', false, 'code', 'PAID_PROVIDER_REQUEST_ID_REUSED');
  end if;
  if rec.pending_calls > 0 or rec.unknown_usage > 0 then
    return jsonb_build_object('allowed', false, 'code', 'PAID_PROVIDER_USAGE_UNRECONCILED');
  end if;
  if (p_call_limit >= 0 and rec.reserved_calls >= p_call_limit)
      or (p_point_limit >= 0 and rec.observed_datapoints >= p_point_limit) then
    return jsonb_build_object('allowed', false, 'code', 'PAID_PROVIDER_BUDGET_EXHAUSTED');
  end if;
  insert into public.wow_rundown_budget_requests(request_id, utc_day, status)
    values (p_request_id, p_utc_day, 'PENDING');
  update public.wow_rundown_budget_days
    set reserved_calls = reserved_calls + 1,
        pending_calls = pending_calls + 1,
        updated_at = now()
    where utc_day = p_utc_day;
  return jsonb_build_object('allowed', true, 'code', 'PAID_PROVIDER_CALL_RESERVED',
      'request_id', p_request_id);
end;
$$;

create or replace function public.wow_rundown_finish_call(
  p_request_id text, p_datapoints integer default null
) returns jsonb
language plpgsql security invoker set search_path = public, pg_temp as $$
declare
  req public.wow_rundown_budget_requests%rowtype;
begin
  if p_request_id is null or p_request_id !~ '^[a-f0-9]{32}$'
     or (p_datapoints is not null and p_datapoints < 0) then
    return jsonb_build_object('ok', false, 'code', 'PAID_PROVIDER_BUDGET_ARGUMENT_INVALID');
  end if;
  select * into req from public.wow_rundown_budget_requests
    where request_id = p_request_id for update;
  if not found then
    return jsonb_build_object('ok', false, 'code', 'PAID_PROVIDER_RESERVATION_NOT_FOUND');
  end if;
  if req.status <> 'PENDING' then
    return jsonb_build_object('ok', false, 'code', 'PAID_PROVIDER_RESERVATION_ALREADY_FINAL');
  end if;
  update public.wow_rundown_budget_days
    set pending_calls = pending_calls - 1,
        observed_datapoints = observed_datapoints + coalesce(p_datapoints, 0),
        unknown_usage = unknown_usage + (case when p_datapoints is null then 1 else 0 end),
        updated_at = now()
    where utc_day = req.utc_day and pending_calls > 0;
  if not found then
    raise exception 'PAID_PROVIDER_PENDING_COUNT_CORRUPT';
  end if;
  update public.wow_rundown_budget_requests
    set status = case when p_datapoints is null then 'UNKNOWN' else 'SETTLED' end,
        observed_datapoints = p_datapoints, finalized_at = now()
    where request_id = p_request_id;
  return jsonb_build_object('ok', true, 'code', case when p_datapoints is null
    then 'PAID_PROVIDER_USAGE_UNKNOWN' else 'PAID_PROVIDER_CALL_SETTLED' end);
end;
$$;

revoke all on function public.wow_rundown_reserve_call(text,date,integer,bigint) from public, anon, authenticated;
revoke all on function public.wow_rundown_finish_call(text,integer) from public, anon, authenticated;
grant execute on function public.wow_rundown_reserve_call(text,date,integer,bigint) to service_role;
grant execute on function public.wow_rundown_finish_call(text,integer) to service_role;

comment on table public.wow_rundown_budget_days is
  'Call ceiling is atomic and hard. Data-point ceiling is measured-after-response; may exceed by one call, never claimed as a hard data-point guarantee.';
