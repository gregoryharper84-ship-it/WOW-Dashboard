-- V17 durable /score-pick-request run and row state.
-- This is correctness persistence, not Action telemetry and not wager execution.

create table if not exists public.wow_pick_request_runs (
    run_id text primary key,
    request_id text not null unique,
    source_kind text not null default 'PROP_BOARD',
    run_status text not null default 'RUNNING',
    total_rows integer not null default 0 check (total_rows >= 0),
    completed_rows integer not null default 0 check (completed_rows >= 0),
    held_rows integer not null default 0 check (held_rows >= 0),
    rejected_rows integer not null default 0 check (rejected_rows >= 0),
    pending_rows integer not null default 0 check (pending_rows >= 0),
    unresolved_rows integer not null default 0 check (unresolved_rows >= 0),
    resumed_rows integer not null default 0 check (resumed_rows >= 0),
    can_execute boolean not null default false check (can_execute = false),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.wow_pick_request_row_states (
    run_id text not null references public.wow_pick_request_runs(run_id) on delete cascade,
    row_key text not null,
    identity_hash text not null,
    event_id text not null,
    event_start_time timestamptz not null,
    sport text not null,
    player text not null,
    stat_type text not null,
    exact_line double precision not null,
    direction text not null,
    source_type text not null,
    platform text,
    current_stage text not null default 'INGESTED' check (current_stage in (
        'INGESTED',
        'IDENTITY_VERIFIED',
        'MODEL_INPUTS_READY',
        'MODEL_COMPUTED',
        'RECEIPT_PERSISTED',
        'GOVERNANCE_AUDITED',
        'PUBLICATION_AUTHORIZED'
    )),
    stage_seq integer not null default 0 check (stage_seq between 0 and 6),
    retry_count integer not null default 0 check (retry_count >= 0),
    terminal_status text not null default 'PENDING' check (terminal_status in ('PENDING','COMPLETED','HELD','REJECTED')),
    terminal_code text,
    failure_domain text,
    durable_status text not null default 'INGESTED',
    model_evaluated boolean not null default false,
    probability_publishable boolean not null default false,
    rank_eligible boolean not null default false,
    prediction_id uuid references public.wow_predictions(prediction_id),
    source_snapshot_id text,
    outcome jsonb,
    can_execute boolean not null default false check (can_execute = false),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (run_id, row_key)
);

create table if not exists public.wow_pick_request_row_transitions (
    transition_id text primary key,
    run_id text not null,
    row_key text not null,
    from_stage text,
    to_stage text not null,
    stage_seq integer not null check (stage_seq between 0 and 6),
    terminal_status text,
    terminal_code text,
    metadata jsonb not null default '{}'::jsonb,
    can_execute boolean not null default false check (can_execute = false),
    created_at timestamptz not null default now(),
    foreign key (run_id, row_key)
        references public.wow_pick_request_row_states(run_id, row_key)
        on delete cascade,
    unique (run_id, row_key, stage_seq)
);

create index if not exists wow_pick_request_row_states_request_lookup_idx
    on public.wow_pick_request_row_states(run_id, terminal_status, stage_seq);
create index if not exists wow_pick_request_row_states_prediction_idx
    on public.wow_pick_request_row_states(prediction_id)
    where prediction_id is not null;

alter table public.wow_pick_request_runs enable row level security;
alter table public.wow_pick_request_row_states enable row level security;
alter table public.wow_pick_request_row_transitions enable row level security;

revoke all on table public.wow_pick_request_runs from public, anon, authenticated;
revoke all on table public.wow_pick_request_row_states from public, anon, authenticated;
revoke all on table public.wow_pick_request_row_transitions from public, anon, authenticated;

grant select, insert, update on table public.wow_pick_request_runs to service_role;
grant select, insert, update on table public.wow_pick_request_row_states to service_role;
grant select, insert on table public.wow_pick_request_row_transitions to service_role;

comment on table public.wow_pick_request_runs is 'V17 durable prop-board run manifest. can_execute is permanently false.';
comment on table public.wow_pick_request_row_states is 'V17 exact-row resumable state. Sporting-model output may be retained before publication authorization; can_execute is permanently false.';
comment on table public.wow_pick_request_row_transitions is 'Append-only monotonic row-stage receipts for /score-pick-request.';
