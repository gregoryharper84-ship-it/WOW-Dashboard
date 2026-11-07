-- Rollback for 20261107_ncaaf_venues_snapshot_and_schedule_geometry.sql.
-- Do not run this in the same transaction as the forward statement.
-- Refuses to drop /venues while a staged /venues receipt exists, and refuses
-- to delete the provider while a REST_TRAVEL row cites it.

do $$
begin
    if exists (
        select 1
          from public.wow_ncaaf_source_snapshots
         where endpoint = '/venues'
    ) then
        raise exception 'NCAAF_VENUES_ROLLBACK_BLOCKED_BY_SNAPSHOTS';
    end if;
    if exists (
        select 1
          from public.wow_ncaaf_pregame_evidence
         where source_provider = 'CFBD_SCHEDULE_GEOMETRY_V1'
    ) then
        raise exception 'NCAAF_GEOMETRY_ROLLBACK_BLOCKED_BY_EVIDENCE';
    end if;
end;
$$;

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
        '/ratings/fpi'
    ));

drop trigger if exists trg_wow_ncaaf_source_snapshot_immutable
    on public.wow_ncaaf_source_snapshots;
drop function if exists public.wow_ncaaf_reject_source_snapshot_mutation();

delete from public.wow_ncaaf_evidence_sources
 where provider_key = 'CFBD_SCHEDULE_GEOMETRY_V1';
