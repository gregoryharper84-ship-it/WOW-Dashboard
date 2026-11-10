-- PROPOSED / DO NOT APPLY before independent protected schema review.
-- Shadow verification evidence only. Certification registry stays unchanged.
-- This migration intentionally does not manufacture historical calibrator digests.
begin;

create table public.wow_nba_team_event_verification_receipts (
  receipt_id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  maintenance_run_id text not null check (length(trim(maintenance_run_id)) > 0),
  sport text not null default 'NBA' check (sport = 'NBA'),
  model_artifact_version text not null check (length(trim(model_artifact_version)) > 0),
  model_artifact_sha256 text not null check (model_artifact_sha256 ~ '^[0-9a-f]{64}$'),
  calibrator_version text not null check (length(trim(calibrator_version)) > 0),
  calibrator_sha256 text not null check (calibrator_sha256 ~ '^[0-9a-f]{64}$'),
  calibrator_fit_end timestamptz not null,
  verifier_version text not null check (length(trim(verifier_version)) > 0),
  verification_status text not null check (verification_status in ('PASS','HOLD','FAIL')),
  evidence_sha256 text not null check (evidence_sha256 ~ '^[0-9a-f]{64}$'),
  verification_payload jsonb not null check (jsonb_typeof(verification_payload) = 'object'),
  probability_publishable boolean not null default false check (probability_publishable = false),
  can_execute boolean not null default false check (can_execute = false),
  constraint uq_wow_nba_verification_run_bundle unique (
    maintenance_run_id, sport, model_artifact_version, calibrator_version
  )
);

create index idx_wow_nba_verification_artifact
  on public.wow_nba_team_event_verification_receipts
  (model_artifact_version, calibrator_version, created_at desc);

alter table public.wow_nba_team_event_verification_receipts enable row level security;
revoke all on public.wow_nba_team_event_verification_receipts
  from public, anon, authenticated, service_role;
grant select, insert on public.wow_nba_team_event_verification_receipts to service_role;

create trigger wow_nba_verification_receipts_immutable
before update or delete on public.wow_nba_team_event_verification_receipts
for each row execute function public.wow_reject_immutable_mutation();

-- SECURITY INVOKER: read-only catalog check, no privileged row bypass.
create function public.wow_nba_team_event_verification_receipt_schema_ready()
returns jsonb
language plpgsql stable security invoker set search_path = ''
as $$
declare
  v_table oid := pg_catalog.to_regclass('public.wow_nba_team_event_verification_receipts');
  v_ready boolean := false;
begin
  if v_table is not null then
    select
      c.relrowsecurity
      and (select count(*) = 15 from pg_catalog.pg_attribute a
           where a.attrelid = v_table and not a.attisdropped
             and a.attnum > 0 and a.attname = any(array[
               'receipt_id','created_at','maintenance_run_id','sport',
               'model_artifact_version','model_artifact_sha256',
               'calibrator_version','calibrator_sha256','calibrator_fit_end',
               'verifier_version','verification_status','evidence_sha256',
               'verification_payload','probability_publishable','can_execute'
             ]))
      and exists (select 1 from pg_catalog.pg_constraint x
                  where x.conrelid = v_table
                    and x.conname = 'uq_wow_nba_verification_run_bundle'
                    and x.convalidated)
      and exists (select 1 from pg_catalog.pg_trigger t
                  where t.tgrelid = v_table
                    and t.tgname = 'wow_nba_verification_receipts_immutable'
                    and not t.tgisinternal and t.tgenabled = 'O'
                    and (t.tgtype::int & 27) = 27
                    and t.tgfoid = pg_catalog.to_regprocedure('public.wow_reject_immutable_mutation()'))
      and pg_catalog.has_table_privilege('service_role',v_table,'SELECT')
      and pg_catalog.has_table_privilege('service_role',v_table,'INSERT')
      and not pg_catalog.has_table_privilege('service_role',v_table,'UPDATE')
      and not pg_catalog.has_table_privilege('service_role',v_table,'DELETE')
      and not pg_catalog.has_table_privilege('anon',v_table,'SELECT')
      and not pg_catalog.has_table_privilege('authenticated',v_table,'SELECT')
    into v_ready
    from pg_catalog.pg_class c where c.oid = v_table;
  end if;
  return pg_catalog.jsonb_build_object(
    'contract_version', 'NBA_TEAM_EVENT_VERIFICATION_RECEIPTS_V1',
    'compatible', coalesce(v_ready, false),
    'probability_publishable', false, 'can_execute', false
  );
end;
$$;
revoke all on function public.wow_nba_team_event_verification_receipt_schema_ready()
  from public, anon, authenticated, service_role;
grant execute on function public.wow_nba_team_event_verification_receipt_schema_ready()
  to service_role;
comment on table public.wow_nba_team_event_verification_receipts is
'Immutable NBA team-event SHADOW verifier receipt; not certification, promotion, or execution.';
commit;
