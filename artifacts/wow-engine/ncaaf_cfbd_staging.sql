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
    constraint wow_ncaaf_source_endpoint check (endpoint in ('/games','/ratings/core','/ratings/sp','/ratings/srs','/ratings/elo','/ratings/fpi')),
    constraint wow_ncaaf_source_params_object check (jsonb_typeof(request_params) = 'object'),
    constraint wow_ncaaf_source_rows_array check (jsonb_typeof(response_rows) = 'array'),
    constraint wow_ncaaf_source_blockers_array check (jsonb_typeof(blocker_codes) = 'array'),
    constraint wow_ncaaf_source_count_nonnegative check (response_row_count >= 0),
    constraint wow_ncaaf_source_status check (acquisition_status in ('AVAILABLE','EMPTY','BLOCKED')),
    constraint wow_ncaaf_source_never_execute check (can_execute = false),
    unique (provider, endpoint, season, week, payload_sha256)
);

alter table public.wow_ncaaf_source_snapshots enable row level security;
revoke all on table public.wow_ncaaf_source_snapshots from anon, authenticated;
grant all on table public.wow_ncaaf_source_snapshots to service_role;

comment on table public.wow_ncaaf_source_snapshots is
  'Immutable-style research staging for raw NCAAF source responses. Staging does not certify model features or enable probability publication.';
