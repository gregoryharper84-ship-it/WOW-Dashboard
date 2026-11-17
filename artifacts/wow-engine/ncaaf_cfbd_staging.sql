-- Read-only source staging for NCAAF historical acquisition.
-- Raw CFBD observations are staged separately from model-ready feature rows.
-- A staged source row is never itself a probability, certified feature snapshot,
-- calibration record, or execution authority.

create table if not exists public.wow_ncaaf_source_snapshots (
    source_snapshot_id uuid primary key default gen_random_uuid(),
    created_at timestamptz not null default now(),
    provider text not null,
    endpoint text not null,
    season integer not null,
    week integer,
    requested_at timestamptz not null,
    retrieved_at timestamptz not null,
    request_params jsonb not null,
    response_rows jsonb not null,
    response_row_count integer not null,
    payload_sha256 text not null,
    acquisition_status text not null,
    blocker_codes jsonb not null default '[]'::jsonb,
    can_execute boolean not null default false,
    constraint wow_ncaaf_source_provider check (provider in ('CFBD')),
    constraint wow_ncaaf_source_endpoint check (endpoint in ('/games','/games/players','/ratings/core','/ratings/sp','/ratings/srs','/ratings/elo','/ratings/fpi','/venues')),
    constraint wow_ncaaf_source_params_object check (jsonb_typeof(request_params) = 'object'),
    constraint wow_ncaaf_source_rows_array check (jsonb_typeof(response_rows) = 'array'),
    constraint wow_ncaaf_source_blockers_array check (jsonb_typeof(blocker_codes) = 'array'),
    constraint wow_ncaaf_source_count_nonnegative check (response_row_count >= 0),
    constraint wow_ncaaf_source_status check (acquisition_status in ('AVAILABLE','EMPTY','BLOCKED')),
    constraint wow_ncaaf_source_never_execute check (can_execute = false),
    unique (provider, endpoint, season, week, payload_sha256)
);

-- Existing installations were created before the CFBD player-boxscore path
-- was added. CREATE TABLE IF NOT EXISTS does not update their old CHECK.
-- Keep the exact known source-route allowlist; do not grant other endpoints.
alter table public.wow_ncaaf_source_snapshots
    drop constraint if exists wow_ncaaf_source_endpoint;
alter table public.wow_ncaaf_source_snapshots
    add constraint wow_ncaaf_source_endpoint
    check (endpoint in ('/games','/games/players','/ratings/core','/ratings/sp',
                       '/ratings/srs','/ratings/elo','/ratings/fpi','/venues'));

alter table public.wow_ncaaf_source_snapshots enable row level security;
revoke all on table public.wow_ncaaf_source_snapshots from public, anon, authenticated, service_role;
grant select, insert on table public.wow_ncaaf_source_snapshots to service_role;

comment on table public.wow_ncaaf_source_snapshots is
  'Immutable research staging for raw NCAAF source responses. Insert-only. Staging does not certify model features or enable probability publication.';

-- Stored receipts are evidence. A later acquisition must insert a new hash
-- or acknowledge the existing one. It must not rewrite retrieved_at, payload,
-- or blockers on a row that has already been staged.
create or replace function public.wow_ncaaf_reject_source_snapshot_mutation()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
begin
    raise exception 'NCAAF_SOURCE_SNAPSHOT_IMMUTABLE'
        using errcode = '55000';
end;
$$;

revoke all on function public.wow_ncaaf_reject_source_snapshot_mutation() from public, anon, authenticated;
grant execute on function public.wow_ncaaf_reject_source_snapshot_mutation() to service_role;

drop trigger if exists trg_wow_ncaaf_source_snapshot_immutable
    on public.wow_ncaaf_source_snapshots;
create trigger trg_wow_ncaaf_source_snapshot_immutable
    before update or delete on public.wow_ncaaf_source_snapshots
    for each row execute function public.wow_ncaaf_reject_source_snapshot_mutation();

-- TRUNCATE does not fire a row-level UPDATE/DELETE trigger.
drop trigger if exists trg_wow_ncaaf_source_snapshot_no_truncate
    on public.wow_ncaaf_source_snapshots;
create trigger trg_wow_ncaaf_source_snapshot_no_truncate
    before truncate on public.wow_ncaaf_source_snapshots
    for each statement execute function public.wow_ncaaf_reject_source_snapshot_mutation();
