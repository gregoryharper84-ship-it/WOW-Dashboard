-- V17 Rundown cost control (#1562 / PR #1568): atomic daily call reservation.
--
-- Operational cost ledger only. Never probability, certification, publication,
-- ranking or execution authority. can_execute=false.
--
-- calls_reserved is a HARD bound: each paid call is admitted by one atomic
-- conditional UPDATE before network I/O, so concurrent workers cannot exceed
-- p_call_limit. datapoints is a SOFT admission ceiling: usage is only known
-- after the provider responds (X-Datapoints), so overshoot is bounded by the
-- calls already in flight.
create table if not exists public.wow_v17_rundown_daily_budget (
    budget_day date primary key,
    calls_reserved integer not null default 0 check (calls_reserved >= 0),
    calls_settled integer not null default 0 check (calls_settled >= 0),
    calls_in_flight integer not null default 0 check (calls_in_flight >= 0),
    calls_unmetered integer not null default 0 check (calls_unmetered >= 0),
    calls_failed integer not null default 0 check (calls_failed >= 0),
    datapoints bigint not null default 0 check (datapoints >= 0),
    updated_at timestamptz not null default now(),
    can_execute boolean not null default false check (can_execute = false)
);

alter table public.wow_v17_rundown_daily_budget enable row level security;
revoke all on table public.wow_v17_rundown_daily_budget from public, anon, authenticated;
grant select, insert, update on table public.wow_v17_rundown_daily_budget to service_role;

create or replace function public.wow_v17_rundown_budget_reserve(
    p_day date,
    p_call_limit integer,
    p_datapoint_limit integer
) returns jsonb
language plpgsql
set search_path = public
as $$
declare
    r public.wow_v17_rundown_daily_budget;
    v_allowed boolean := true;
begin
    insert into public.wow_v17_rundown_daily_budget (budget_day)
    values (p_day)
    on conflict (budget_day) do nothing;

    -- Single conditional UPDATE: the row lock serializes concurrent callers and
    -- the WHERE clause is re-checked after the lock, so the call cap is exact.
    update public.wow_v17_rundown_daily_budget
       set calls_reserved = calls_reserved + 1,
           calls_in_flight = calls_in_flight + 1,
           updated_at = now()
     where budget_day = p_day
       and (p_call_limit is null or calls_reserved < p_call_limit)
       and (p_datapoint_limit is null or datapoints < p_datapoint_limit)
    returning * into r;

    if not found then
        v_allowed := false;
        select * into r from public.wow_v17_rundown_daily_budget where budget_day = p_day;
    end if;

    return jsonb_build_object(
        'allowed', v_allowed,
        'calls_reserved', r.calls_reserved,
        'calls_settled', r.calls_settled,
        'calls_in_flight', r.calls_in_flight,
        'calls_unmetered', r.calls_unmetered,
        'calls_failed', r.calls_failed,
        'datapoints', r.datapoints,
        'can_execute', false
    );
end;
$$;

create or replace function public.wow_v17_rundown_budget_settle(
    p_day date,
    p_datapoints integer,
    p_failed boolean
) returns jsonb
language plpgsql
set search_path = public
as $$
declare
    r public.wow_v17_rundown_daily_budget;
begin
    if p_datapoints is not null and p_datapoints < 0 then
        raise exception 'RUNDOWN_BUDGET_NEGATIVE_DATAPOINTS';
    end if;

    update public.wow_v17_rundown_daily_budget
       set calls_in_flight = greatest(calls_in_flight - 1, 0),
           calls_settled = calls_settled + 1,
           calls_unmetered = calls_unmetered + case when p_datapoints is null then 1 else 0 end,
           calls_failed = calls_failed + case when coalesce(p_failed, false) then 1 else 0 end,
           datapoints = datapoints + coalesce(p_datapoints, 0),
           updated_at = now()
     where budget_day = p_day
    returning * into r;

    if not found then
        raise exception 'RUNDOWN_BUDGET_SETTLE_WITHOUT_RESERVATION';
    end if;

    return jsonb_build_object(
        'calls_reserved', r.calls_reserved,
        'calls_in_flight', r.calls_in_flight,
        'datapoints', r.datapoints,
        'can_execute', false
    );
end;
$$;

revoke all on function public.wow_v17_rundown_budget_reserve(date, integer, integer) from public, anon, authenticated;
revoke all on function public.wow_v17_rundown_budget_settle(date, integer, boolean) from public, anon, authenticated;
grant execute on function public.wow_v17_rundown_budget_reserve(date, integer, integer) to service_role;
grant execute on function public.wow_v17_rundown_budget_settle(date, integer, boolean) to service_role;

comment on table public.wow_v17_rundown_daily_budget is
'Service-role-only Rundown daily cost ledger. calls_reserved = hard atomic call cap; datapoints = soft admission ceiling. Never probability/certification/publication/execution authority; can_execute=false.';
