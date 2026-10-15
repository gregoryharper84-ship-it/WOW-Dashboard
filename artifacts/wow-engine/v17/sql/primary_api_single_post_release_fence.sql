-- P0 #1752 / #1021: primary API EXACT-SHA single-POST fence.
-- REVIEW ONLY: never install or seed in an uncertified production database.
-- Requires independently approved wow_release_control.release_control_project_anchor
-- from the worker release-control migration, and a distinct DBA-provisioned
-- wow_primary_release_controller login. No one in Engineering may mint approvals.
create schema if not exists wow_release_control;
revoke all on schema wow_release_control from public;
do $role_gate$
begin
  if not exists (
    select 1 from pg_catalog.pg_roles where rolname='wow_primary_release_controller'
      and rolcanlogin and not rolsuper and not rolbypassrls
  ) then raise exception 'PRIMARY_RELEASE_ROLE_NOT_DBA_PROVISIONED'; end if;
  if to_regclass('wow_release_control.release_control_project_anchor') is null then
    raise exception 'PRIMARY_RELEASE_DBA_PROJECT_ANCHOR_MISSING';
  end if;
end;
$role_gate$;

create table if not exists wow_release_control.primary_api_release_identity (
  singleton boolean primary key default true check(singleton),
  project_ref text not null check(project_ref ~ '^[a-z]{20}$'),
  service_id text not null check(service_id='srv-da7sa9gu01pc73brt80g'),
  authorized boolean not null default false,
  approved_by text,
  approved_at timestamptz,
  check(not authorized or (approved_at is not null and
    nullif(pg_catalog.btrim(approved_by),'') is not null))
);
create table if not exists wow_release_control.primary_api_deploy_intents (
  service_id text primary key check(service_id='srv-da7sa9gu01pc73brt80g'),
  target_sha text not null check(target_sha ~ '^[0-9a-f]{40}$'),
  owner_run_id text not null check(length(owner_run_id) between 1 and 128),
  state text not null check(state in
     ('POST_UNCERTAIN','PROVIDER_ACK','LIVE_OBSERVED','HOLD')),
  post_reserved_at timestamptz not null default clock_timestamp(),
  provider_deploy_id text unique check(provider_deploy_id is null or
       provider_deploy_id ~ '^dep-[a-z0-9]+$'),
  updated_at timestamptz not null default clock_timestamp(),
  can_execute boolean not null default false check(can_execute=false),
  check(state in ('POST_UNCERTAIN','HOLD') or provider_deploy_id is not null)
);
revoke all on wow_release_control.primary_api_release_identity
  from public,anon,authenticated,wow_primary_release_controller;
revoke all on wow_release_control.primary_api_deploy_intents
  from public,anon,authenticated,wow_primary_release_controller;
alter table wow_release_control.primary_api_release_identity enable row level security;
alter table wow_release_control.primary_api_deploy_intents enable row level security;

-- Private SECURITY DEFINER approval verification. Direct controller invocation
-- is NOT granted; the three narrowly scoped transition RPCs invoke it.
create or replace function wow_release_control.assert_primary_api_authority()
returns void language plpgsql security definer
set search_path=pg_catalog,wow_release_control
as $$
declare v_ref text;
begin
  select ident.project_ref into v_ref
    from wow_release_control.primary_api_release_identity ident
    join wow_release_control.release_control_project_anchor anchor
      on anchor.singleton=ident.singleton
     and anchor.expected_project_ref=ident.project_ref
   where ident.singleton=true and ident.authorized=true
     and ident.service_id='srv-da7sa9gu01pc73brt80g'
     and ident.approved_at is not null
     and nullif(pg_catalog.btrim(ident.approved_by),'') is not null
     and anchor.certified_at is not null
   for share of ident,anchor;
  if not found then
    raise exception 'PRIMARY_RELEASE_DBA_AUTHORIZATION_MISSING';
  end if;
end;
$$;
revoke all on function wow_release_control.assert_primary_api_authority()
  from public,anon,authenticated,wow_primary_release_controller;

-- A single reservation is durable BEFORE the Render POST. Even a process
-- crash between commit and network call must leave POST_UNCERTAIN and BLOCK.
-- Same-SHA retries also fail: POST is never issued twice after an ambiguous ACK.
create or replace function wow_release_control.reserve_primary_api_post(
  p_service_id text,p_target_sha text,p_owner_run_id text)
returns text language plpgsql security definer
set search_path=pg_catalog,wow_release_control
as $$
begin
  if p_service_id is distinct from 'srv-da7sa9gu01pc73brt80g'
     or p_target_sha is null or p_target_sha !~ '^[0-9a-f]{40}$'
     or p_owner_run_id is null or length(p_owner_run_id) not between 1 and 128
  then raise exception 'PRIMARY_RELEASE_REQUEST_INVALID'; end if;
  perform wow_release_control.assert_primary_api_authority();
  perform pg_catalog.pg_advisory_xact_lock(
     pg_catalog.hashtextextended(p_service_id, 2701));
  if exists (
    select 1 from wow_release_control.primary_api_deploy_intents
     where service_id=p_service_id for update
  ) then
    raise exception 'PRIMARY_RELEASE_POST_ALREADY_RESERVED_RECONCILE_ONLY';
  end if;
  insert into wow_release_control.primary_api_deploy_intents
    (service_id,target_sha,owner_run_id,state,can_execute)
    values (p_service_id,p_target_sha,p_owner_run_id,'POST_UNCERTAIN',false);
  return 'PRIMARY_RELEASE_POST_AUTHORIZED_ONCE';
end;
$$;
revoke all on function wow_release_control.reserve_primary_api_post(text,text,text)
  from public,anon,authenticated,wow_primary_release_controller;

-- Transaction-scoped second authorization check: callers MUST hold this
-- transaction open during the one permitted provider POST. Independent DBA
-- revocation then orders before or after the network request, never between
-- the authorization check and provider invocation.
create or replace function wow_release_control.authorize_primary_api_post_io(
 p_service_id text,p_target_sha text,p_owner_run_id text
) returns text language plpgsql security definer
set search_path=pg_catalog,wow_release_control
as $$
declare v_state text;
begin
 if p_service_id is distinct from 'srv-da7sa9gu01pc73brt80g'
    or p_target_sha is null or p_target_sha !~ '^[0-9a-f]{40}$'
    or p_owner_run_id is null or length(p_owner_run_id) not between 1 and 128
 then raise exception 'PRIMARY_RELEASE_IO_IDENTITY_INVALID'; end if;
 perform wow_release_control.assert_primary_api_authority();
 select intent.state into v_state
   from wow_release_control.primary_api_deploy_intents intent
  where intent.service_id=p_service_id and intent.target_sha=p_target_sha
    and intent.owner_run_id=p_owner_run_id
  for share of intent;
 if not found or v_state <> 'POST_UNCERTAIN' then
   raise exception 'PRIMARY_RELEASE_IO_RESERVATION_MISSING';
 end if;
 return 'PRIMARY_RELEASE_IO_WINDOW_ATTESTED';
end;
$$;
revoke all on function wow_release_control.authorize_primary_api_post_io(
 text,text,text) from public,anon,authenticated,wow_primary_release_controller;

-- This operation records authenticated provider READBACK, never authorizes a
-- second POST. The release caller must independently authenticate Render data.
create or replace function wow_release_control.observe_primary_api_deploy(
  p_service_id text,p_target_sha text,p_owner_run_id text,
  p_deploy_id text,p_deploy_sha text,p_live boolean)
returns text language plpgsql security definer
set search_path=pg_catalog,wow_release_control
as $$
begin
  if p_service_id is distinct from 'srv-da7sa9gu01pc73brt80g'
     or p_target_sha is null or p_target_sha !~ '^[0-9a-f]{40}$'
     or p_deploy_sha is distinct from p_target_sha
     or p_deploy_id is null or p_deploy_id !~ '^dep-[a-z0-9]+$'
     or p_live is null
  then raise exception 'PRIMARY_RELEASE_OBSERVATION_INVALID'; end if;
  perform wow_release_control.assert_primary_api_authority();
  update wow_release_control.primary_api_deploy_intents
     set provider_deploy_id=p_deploy_id,
         state=case when p_live then 'LIVE_OBSERVED' else 'PROVIDER_ACK' end,
         updated_at=clock_timestamp()
   where service_id=p_service_id and target_sha=p_target_sha
     and owner_run_id=p_owner_run_id
     and state in ('POST_UNCERTAIN','PROVIDER_ACK')
     and (provider_deploy_id is null or provider_deploy_id=p_deploy_id);
  if not found then raise exception 'PRIMARY_RELEASE_READBACK_CONFLICT'; end if;
  return 'PRIMARY_RELEASE_PROVIDER_READBACK_ONLY';
end;
$$;
revoke all on function wow_release_control.observe_primary_api_deploy(
  text,text,text,text,text,boolean)
  from public,anon,authenticated,wow_primary_release_controller;

revoke all on schema wow_release_control from wow_primary_release_controller;
grant usage on schema wow_release_control to wow_primary_release_controller;
grant execute on function wow_release_control.reserve_primary_api_post(
  text,text,text) to wow_primary_release_controller;
grant execute on function wow_release_control.authorize_primary_api_post_io(
  text,text,text) to wow_primary_release_controller;
grant execute on function wow_release_control.observe_primary_api_deploy(
  text,text,text,text,text,boolean) to wow_primary_release_controller;

-- Effective privileges (including inherited roles) and function ownership
-- must be certified, not inferred from direct REVOKE statements alone.
do $acl_gate$
declare rec record;
begin
  if exists (
    select 1 from pg_catalog.pg_namespace n
    where n.nspname='wow_release_control'
      and pg_catalog.pg_has_role('wow_primary_release_controller',n.nspowner,'MEMBER')
  ) or pg_catalog.has_schema_privilege(
      'wow_primary_release_controller','wow_release_control','CREATE'
  ) then
    raise exception 'PRIMARY_RELEASE_CONTROLLER_SCHEMA_PRIVILEGE_LEAK';
  end if;
  for rec in
    select c.oid,c.relname,c.relowner
    from pg_catalog.pg_class c
    join pg_catalog.pg_namespace n on n.oid=c.relnamespace
    where n.nspname='wow_release_control'
      and c.relname in ('primary_api_release_identity',
                        'primary_api_deploy_intents',
                        'release_control_project_anchor')
  loop
    if pg_catalog.pg_has_role('wow_primary_release_controller',
           rec.relowner,'MEMBER')
      or pg_catalog.has_table_privilege('wow_primary_release_controller',
           rec.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
    then raise exception 'PRIMARY_RELEASE_CONTROLLER_PRIVILEGE_LEAK:%',rec.relname; end if;
  end loop;
  if pg_catalog.has_function_privilege('wow_primary_release_controller',
       'wow_release_control.assert_primary_api_authority()'::regprocedure,
       'EXECUTE')
  then raise exception 'PRIMARY_RELEASE_INTERNAL_GUARD_EXPOSED'; end if;
end;
$acl_gate$;
