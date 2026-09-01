-- MLB 1IP production-capability support. Repository source only; do not apply
-- to production until independently reviewed. can_execute remains false.

create table if not exists public.wow_mlb_1ip_refresh_queue (
    queue_id uuid primary key default gen_random_uuid(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    row_key text not null,
    request_id text,
    event_id text not null,
    event_start_time timestamptz not null,
    player text not null,
    starter_name_at_capture text not null,
    line numeric not null,
    direction text not null check (direction in ('MORE','LESS')),
    money_lane_status text not null default 'PAYOUT_UNRESOLVED',
    source_snapshot_id uuid,
    status text not null default 'WAITING_FOR_OFFICIAL_LINEUP'
      check (status in ('WAITING_FOR_OFFICIAL_LINEUP','READY_TO_RERUN','RERUN_COMPLETED','SLATE_PURGE','EXPIRED_PREGAME_WINDOW','FAILED')),
    last_refresh_at timestamptz,
    next_refresh_at timestamptz,
    refresh_attempts integer not null default 0 check (refresh_attempts >= 0),
    provisional_evidence jsonb not null default '{}'::jsonb check (jsonb_typeof(provisional_evidence)='object'),
    refreshed_evidence jsonb check (refreshed_evidence is null or jsonb_typeof(refreshed_evidence)='object'),
    rerun_result jsonb check (rerun_result is null or jsonb_typeof(rerun_result)='object'),
    rerun_completed_at timestamptz,
    terminal_label text,
    last_error_code text,
    probability_publishable boolean not null default false check (probability_publishable=false),
    can_execute boolean not null default false check (can_execute=false),
    unique(row_key, event_id, player)
);

create index if not exists wow_mlb_1ip_refresh_queue_pending_idx
on public.wow_mlb_1ip_refresh_queue(next_refresh_at, event_start_time)
where status='WAITING_FOR_OFFICIAL_LINEUP';

comment on table public.wow_mlb_1ip_refresh_queue is
'Pregame-only MLB 1IP provisional-lineup refresh queue. A confirmed lineup causes a governed specialist rerun; the queue never authorizes execution or probability publication.';
