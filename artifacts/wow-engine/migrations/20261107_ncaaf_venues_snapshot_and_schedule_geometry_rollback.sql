-- Rollback for 20261107_ncaaf_venues_snapshot_and_schedule_geometry.sql.
-- Same authorized runner and one-transaction rule as the forward file.
-- Do not run this in the same transaction as the forward statement.
--
-- Refuses to remove /venues or the geometry provider while any of these exist:
-- a staged /venues receipt, a direct evidence row, a training-feature manifest
-- evidence entry, or an event-snapshot manifest evidence entry. Manifest checks
-- read the structured evidence array. A provider name in notes or other text
-- is not a citation.
--
-- Privilege narrowing is kept. This file does not grant UPDATE, DELETE, or
-- TRUNCATE back to service_role.

\set ON_ERROR_STOP on
begin;

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
    if exists (
        select 1
          from public.wow_ncaaf_training_features as feature
         cross join lateral jsonb_array_elements(
             case
                 when jsonb_typeof(feature.feature_source_manifest -> 'evidence') = 'array'
                 then feature.feature_source_manifest -> 'evidence'
                 else '[]'::jsonb
             end
         ) as evidence
         where jsonb_typeof(evidence) = 'object'
           and evidence ->> 'source_provider' = 'CFBD_SCHEDULE_GEOMETRY_V1'
    ) then
        raise exception 'NCAAF_GEOMETRY_ROLLBACK_BLOCKED_BY_TRAINING_MANIFEST';
    end if;
    if exists (
        select 1
          from public.wow_ncaaf_event_feature_snapshots as snapshot
         cross join lateral jsonb_array_elements(
             case
                 when jsonb_typeof(snapshot.source_manifest #> '{feature_source_manifest,evidence}') = 'array'
                 then snapshot.source_manifest #> '{feature_source_manifest,evidence}'
                 else '[]'::jsonb
             end
         ) as evidence
         where jsonb_typeof(evidence) = 'object'
           and evidence ->> 'source_provider' = 'CFBD_SCHEDULE_GEOMETRY_V1'
    ) then
        raise exception 'NCAAF_GEOMETRY_ROLLBACK_BLOCKED_BY_EVENT_MANIFEST';
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
drop trigger if exists trg_wow_ncaaf_source_snapshot_no_truncate
    on public.wow_ncaaf_source_snapshots;
drop function if exists public.wow_ncaaf_reject_source_snapshot_mutation();

delete from public.wow_ncaaf_evidence_sources
 where provider_key = 'CFBD_SCHEDULE_GEOMETRY_V1';

revoke all on table public.wow_ncaaf_source_snapshots from public, anon, authenticated, service_role;
grant select, insert on table public.wow_ncaaf_source_snapshots to service_role;

commit;
