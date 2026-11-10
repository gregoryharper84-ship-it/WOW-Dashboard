-- NHL fitted specialist: verified full-season timeline receipts for scoring.
--
-- Persisted by full-season append-only source re-ingest. Scoring loads and
-- projects this receipt; it never fetches club schedules on the score path.
-- Mutable by season (upsert) so a later complete re-ingest can replace the
-- snapshot. Never grants probability, certification, publication, ranking, or
-- execution authority.
create table if not exists public.wow_nhl_season_timeline_receipts (
    receipt_id uuid primary key default gen_random_uuid(),
    created_at timestamptz not null default now(),
    season text not null,
    provenance_complete boolean not null check (provenance_complete = true),
    season_scope text not null check (season_scope = 'FULL_SEASON'),
    settled_events jsonb not null check (jsonb_typeof(settled_events) = 'array'),
    verified_at timestamptz not null,
    ingest_code_sha text not null check (ingest_code_sha ~ '^[0-9a-f]{7,64}$'),
    source text not null,
    can_execute boolean not null default false check (can_execute = false),
    unique (season)
);

alter table public.wow_nhl_season_timeline_receipts enable row level security;

revoke all on table public.wow_nhl_season_timeline_receipts
    from public, anon, authenticated;

grant select, insert, update on table public.wow_nhl_season_timeline_receipts
    to service_role;

comment on table public.wow_nhl_season_timeline_receipts is
'Service-role-only verified full-season NHL settled-event timeline receipts for fitted scoring. Replaced per season by complete re-ingest; never probability/certification/publication/execution authority; can_execute=false.';
