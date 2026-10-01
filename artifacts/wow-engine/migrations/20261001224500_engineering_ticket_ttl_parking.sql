-- WOW V17 engineering-ticket parking with TTL wake-ups.
--
-- Class B orchestration/persistence infrastructure only. This migration does
-- not touch sporting predictions, fitted models, calibration, terminal sporting
-- reduction, or wager execution. V17_TERMINAL_REDUCER and can_execute=false
-- remain unchanged.
--
-- `status` is the pre-existing engineering lifecycle state
-- (OPEN/IN_PROGRESS/BLOCKED/CLOSED). `queue_status` is deliberately separate so
-- ticket execution semantics can evolve without breaking existing auditor and
-- reporting consumers.

alter table public.wow_engineering_backlog
    add column if not exists queue_status text,
    add column if not exists parked_reason text,
    add column if not exists parked_context jsonb not null default '{}'::jsonb,
    add column if not exists parked_until timestamptz,
    add column if not exists wake_count integer not null default 0,
    add column if not exists claim_owner text,
    add column if not exists claimed_at timestamptz;

-- Preserve terminal decisions even if a legacy row's lifecycle status was not
-- closed consistently. Unknown legacy BLOCKED rows are fail-closed rather than
-- being made eligible without a typed blocker and wake-up policy.
update public.wow_engineering_backlog
set queue_status = case
    when terminal_state is not null or status = 'CLOSED' then 'COMPLETED'
    when status = 'BLOCKED' then 'FAILED'
    else 'ACTIONABLE'
end
where queue_status is null;

alter table public.wow_engineering_backlog
    alter column queue_status set default 'ACTIONABLE',
    alter column queue_status set not null;

alter table public.wow_engineering_backlog
    add constraint wow_engineering_backlog_queue_status_check
        check (queue_status in ('ACTIONABLE','IN_PROGRESS','PARKED','COMPLETED','FAILED')),
    add constraint wow_engineering_backlog_wake_count_check
        check (wake_count >= 0),
    add constraint wow_engineering_backlog_parked_context_object_check
        check (jsonb_typeof(parked_context) = 'object'),
    add constraint wow_engineering_backlog_parking_shape_check
        check (
            (
                queue_status = 'PARKED'
                and parked_reason is not null
                and parked_until is not null
                and claim_owner is null
                and claimed_at is null
            )
            or (
                queue_status = 'IN_PROGRESS'
                and parked_reason is null
                and parked_until is null
                and claim_owner is not null
                and claimed_at is not null
            )
            or (
                queue_status in ('ACTIONABLE','COMPLETED','FAILED')
                and parked_reason is null
                and parked_until is null
                and claim_owner is null
                and claimed_at is null
            )
        );

create index if not exists wow_engineering_backlog_queue_eligible_idx
    on public.wow_engineering_backlog (queue_status, parked_until, priority, opened_at, id)
    where queue_status in ('ACTIONABLE','PARKED');

-- Claim and wake in one transaction. Returning a row lock to an application is
-- not enough because the lock is released when the RPC transaction ends; this
-- function therefore changes each selected row to IN_PROGRESS before returning
-- it. FOR UPDATE SKIP LOCKED prevents two concurrent orchestrators from choosing
-- the same eligible row while the UPDATE persists ownership after lock release.
create or replace function public.wow_claim_engineering_tickets(
    p_claim_owner text,
    p_limit integer default 5
)
returns table (
    id bigint,
    ticket_id text,
    title text,
    "class" text,
    status text,
    terminal_state text,
    source text,
    priority text,
    scope text,
    probability_impact text,
    schema_behavior_change text,
    constraint_dependency text,
    validation_environment text,
    description text,
    evidence_notes text,
    opened_at timestamptz,
    closed_at timestamptz,
    created_by text,
    queue_status text,
    previous_queue_status text,
    previous_parked_reason text,
    previous_parked_until timestamptz,
    parked_reason text,
    parked_context jsonb,
    parked_until timestamptz,
    wake_count integer,
    claim_owner text,
    claimed_at timestamptz
)
language sql
security invoker
set search_path = ''
as $function$
    with eligible as (
        select
            b.id,
            b.queue_status as previous_queue_status,
            b.parked_reason as previous_parked_reason,
            b.parked_until as previous_parked_until
        from public.wow_engineering_backlog b
        where nullif(btrim(p_claim_owner), '') is not null
          and b.terminal_state is null
          and (
              b.queue_status = 'ACTIONABLE'
              or (b.queue_status = 'PARKED' and b.parked_until <= current_timestamp)
          )
        order by
            case b.priority
                when 'P0' then 0
                when 'P1' then 1
                when 'P2' then 2
                when 'P3' then 3
                else 4
            end,
            b.opened_at asc,
            b.id asc
        limit greatest(1, least(coalesce(p_limit, 5), 25))
        for update skip locked
    ), claimed as (
        update public.wow_engineering_backlog b
        set status = 'IN_PROGRESS',
            queue_status = 'IN_PROGRESS',
            parked_reason = null,
            parked_until = null,
            parked_context = case
                when e.previous_queue_status = 'PARKED' then b.parked_context
                else '{}'::jsonb
            end,
            claim_owner = p_claim_owner,
            claimed_at = current_timestamp
        from eligible e
        where b.id = e.id
        returning
            b.*,
            e.previous_queue_status,
            e.previous_parked_reason,
            e.previous_parked_until
    )
    select
        c.id,
        c.ticket_id,
        c.title,
        c."class",
        c.status,
        c.terminal_state,
        c.source,
        c.priority,
        c.scope,
        c.probability_impact,
        c.schema_behavior_change,
        c.constraint_dependency,
        c.validation_environment,
        c.description,
        c.evidence_notes,
        c.opened_at,
        c.closed_at,
        c.created_by,
        c.queue_status,
        c.previous_queue_status,
        c.previous_parked_reason,
        c.previous_parked_until,
        c.parked_reason,
        c.parked_context,
        c.parked_until,
        c.wake_count,
        c.claim_owner,
        c.claimed_at
    from claimed c
    order by
        case c.priority
            when 'P0' then 0
            when 'P1' then 1
            when 'P2' then 2
            when 'P3' then 3
            else 4
        end,
        c.opened_at asc,
        c.id asc;
$function$;

-- Internal service-role queue operation only. Keep RLS enabled on the table and
-- do not expose this state transition to anon/authenticated API callers.
revoke all on function public.wow_claim_engineering_tickets(text, integer) from public;
revoke all on function public.wow_claim_engineering_tickets(text, integer) from anon;
revoke all on function public.wow_claim_engineering_tickets(text, integer) from authenticated;
grant execute on function public.wow_claim_engineering_tickets(text, integer) to service_role;
