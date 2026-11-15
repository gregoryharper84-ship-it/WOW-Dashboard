-- WOW V17 NHL pregame starter-goalie receipt ledger.
-- Immutable research evidence for independent starting-goalie confirmation.
-- This table never stores a probability, calibration state or execution grant.
create table if not exists public.wow_nhl_pregame_starter_receipts (
    receipt_identity_key text primary key,
    created_at timestamptz not null default now(),
    official_event_id text not null,
    provider text not null,
    source_kind text not null,
    source_snapshot_id text not null,
    source_payload_sha256 text not null,
    observed_at timestamptz not null,
    event_start_time_utc timestamptz,
    home_team text,
    away_team text,
    home_goalie_id text,
    away_goalie_id text,
    starting_status text,
    collection_status text not null default 'COLLECTED',
    blockers jsonb not null default '[]'::jsonb,
    raw_receipt jsonb not null,
    prediction_authority boolean not null default false,
    can_execute boolean not null default false,
    constraint wow_nhl_goalie_receipt_never_execute check (can_execute = false),
    constraint wow_nhl_goalie_receipt_no_prediction_authority check (prediction_authority = false),
    constraint wow_nhl_goalie_receipt_hash check (source_payload_sha256 ~ '^[0-9a-f]{64}$'),
    constraint wow_nhl_goalie_receipt_raw_object check (jsonb_typeof(raw_receipt) = 'object'),
    constraint wow_nhl_goalie_receipt_blockers_array check (jsonb_typeof(blockers) = 'array'),
    constraint wow_nhl_goalie_receipt_event_id check (official_event_id ~ '^[0-9]{10}$')
);

create index if not exists idx_wow_nhl_goalie_receipt_event
    on public.wow_nhl_pregame_starter_receipts (official_event_id, provider, observed_at desc);

alter table public.wow_nhl_pregame_starter_receipts enable row level security;

-- Receipts are append-only evidence: no caller may rewrite collected source bytes.
revoke all privileges on public.wow_nhl_pregame_starter_receipts
    from public, anon, authenticated, service_role;
grant select, insert on public.wow_nhl_pregame_starter_receipts to service_role;
