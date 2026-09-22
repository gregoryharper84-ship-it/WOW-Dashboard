-- V17 team/event recoverable-hold durable retry queue.
-- Additive infrastructure only. This queue has no probability-publication or
-- wager-execution authority. Service-role backend access is expected; public
-- Data API access remains blocked by RLS with no anon/authenticated policies.

create table if not exists public.wow_team_event_retry_queue (
    queue_id uuid primary key default gen_random_uuid(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    research_run_id text not null,
    sport text not null,
    league text not null,
    event_key text not null,
    official_event_id text not null,
    objective_lane text not null,
    event_start_time timestamptz not null,
    request_payload jsonb not null check (jsonb_typeof(request_payload) = 'object'),
    canonical_hydration_required boolean not null default true,
    status text not null default 'WAITING_FOR_INPUTS'
      check (status in ('WAITING_FOR_INPUTS','RERUN_COMPLETED','EXPIRED_PREGAME_WINDOW','FAILED')),
    candidate_state text,
    retry_trigger text,
    blocker_code text,
    last_outcome jsonb check (last_outcome is null or jsonb_typeof(last_outcome) = 'object'),
    last_refresh_at timestamptz,
    next_refresh_at timestamptz,
    refresh_attempts integer not null default 0 check (refresh_attempts >= 0),
    completed_at timestamptz,
    last_error_code text,
    queue_probability_publishable boolean not null default false check (queue_probability_publishable = false),
    can_execute boolean not null default false check (can_execute = false),
    unique (sport, event_key, objective_lane)
);

create index if not exists wow_team_event_retry_queue_pending_idx
on public.wow_team_event_retry_queue(next_refresh_at, event_start_time)
where status = 'WAITING_FOR_INPUTS';

alter table public.wow_team_event_retry_queue enable row level security;
revoke all on table public.wow_team_event_retry_queue from anon, authenticated;

comment on table public.wow_team_event_retry_queue is
'V17 pregame team/event recoverable-hold retry queue. Replays the same governed request identity through the controlling specialist. The queue itself cannot publish probabilities or execute wagers.';
