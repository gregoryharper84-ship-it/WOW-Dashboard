-- Versioned, not-yet-applied migration for wow-engine-validation
-- (project ref iczfhsmjrrafhvcpmqhr).
--
-- Run this file in one transaction, and only after independent acceptance.
-- It does not insert venue rows, rewrite retrieved_at, or backdate a catalog.
-- A venue snapshot acquired after this statement is pregame only for kickoffs
-- that are still in the future at its real retrieved_at.
--
-- Uniqueness is intentionally unchanged. Postgres UNIQUE does not treat
-- week NULL as equal, and this statement does not add an index that could
-- fail on existing season-wide /games receipts. Application identity already
-- matches week IS NULL.
--
-- Rollback: 20261107_ncaaf_venues_snapshot_and_schedule_geometry_rollback.sql

alter table public.wow_ncaaf_source_snapshots
    drop constraint if exists wow_ncaaf_source_endpoint;
alter table public.wow_ncaaf_source_snapshots
    add constraint wow_ncaaf_source_endpoint
    check (endpoint in (
        '/games',
        '/games/players',
        '/ratings/core',
        '/ratings/sp',
        '/ratings/srs',
        '/ratings/elo',
        '/ratings/fpi',
        '/venues'
    ));

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

alter table public.wow_ncaaf_source_snapshots enable row level security;
revoke all on table public.wow_ncaaf_source_snapshots from anon, authenticated;
grant all on table public.wow_ncaaf_source_snapshots to service_role;

insert into public.wow_ncaaf_evidence_sources (
    provider_key, provider_class, max_provenance_grade, allowed_evidence_kinds,
    active, approval_reference, notes, can_execute
) values (
    'CFBD_SCHEDULE_GEOMETRY_V1',
    'RESEARCH_SOURCE',
    'B',
    array['REST_TRAVEL'],
    true,
    'CODE:ncaaf_pregame_feature_acquisition.py@V1',
    'REST_TRAVEL only. Prior completion and venue coordinates must come from hashed receipts retrieved before the target kickoff. Neutral-site HOME travel is provenance and is not an extra fitted feature. No score, rating, box score, forecast, or market input.',
    false
)
on conflict (provider_key) do update set
    provider_class = excluded.provider_class,
    max_provenance_grade = excluded.max_provenance_grade,
    allowed_evidence_kinds = excluded.allowed_evidence_kinds,
    active = excluded.active,
    approval_reference = excluded.approval_reference,
    notes = excluded.notes,
    can_execute = false;

-- Read back after commit, separately:
-- select pg_get_constraintdef(oid) from pg_constraint where conname = 'wow_ncaaf_source_endpoint';
-- select tgname, tgenabled from pg_trigger where tgname = 'trg_wow_ncaaf_source_snapshot_immutable';
-- select provider_key, allowed_evidence_kinds, active, can_execute
--   from public.wow_ncaaf_evidence_sources
--  where provider_key = 'CFBD_SCHEDULE_GEOMETRY_V1';
