-- WOW deployment intent ledger; staging only, not applied to production.
-- Use a private schema and restrict execution to a dedicated controlled service role.
create schema if not exists wow_release_control;

create table if not exists wow_release_control.render_worker_deploy_intents (
  service_id text not null,
  target_sha text not null check (target_sha ~ '^[0-9a-f]{40}$'),
  state text not null check (state in ('PENDING','INITIATING','ACK_AMBIGUOUS','DEPLOYING','LIVE_VERIFIED','HOLD')),
  owner_run_id text,
  lease_token bigint not null default 0 check (lease_token >= 0),
  lease_expires_at timestamptz,
  render_deploy_id text,
  attempts integer not null default 0 check (attempts between 0 and 3),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (service_id,target_sha),
  check (render_deploy_id is null or render_deploy_id ~ '^dep-[a-z0-9]+$')
);
revoke all on schema wow_release_control from public, anon, authenticated;
revoke all on all tables in schema wow_release_control from public, anon, authenticated;
alter table wow_release_control.render_worker_deploy_intents enable row level security;

-- Explicit DBA-owned production authority receipt. No row is seeded by
-- Engineering staging: a successful migration is NOT release authorization.
-- A security reviewer must provision the dedicated limited login role and
-- insert/approve this singleton independently after verifying project identity.
create table if not exists wow_release_control.release_control_identity (
  singleton boolean primary key default true check (singleton),
  project_ref text not null check (project_ref ~ '^[a-z]{20}$'),
  environment text not null check (environment = 'production'),
  service_id text not null check (service_id = 'srv-dapkbc0473hc73c1978g'),
  authorized boolean not null default false,
  approved_by text,
  approved_at timestamptz,
  check (not authorized or (
    approved_at is not null and nullif(btrim(approved_by),'') is not null
  ))
);
revoke all on wow_release_control.release_control_identity from public, anon, authenticated;
alter table wow_release_control.release_control_identity enable row level security;


-- Caller must run in a trusted service-side privileged connection.
-- PENDING may be created once. INITIATING means a POST might have been sent:
-- after expiry a different run MUST NOT issue another POST automatically.
create or replace function wow_release_control.claim_render_worker_deploy(
  p_service_id text, p_target_sha text, p_owner_run_id text,
  p_lease_seconds integer default 300
) returns table(state text, lease_token bigint, can_initiate boolean)
language plpgsql security definer
set search_path = pg_catalog, wow_release_control
as $$
declare v wow_release_control.render_worker_deploy_intents%rowtype;
        v_in_flight_sha text;
begin
  if p_service_id <> 'srv-dapkbc0473hc73c1978g'
     or p_target_sha !~ '^[0-9a-f]{40}$'
     or nullif(p_owner_run_id,'') is null
     or p_lease_seconds not between 30 and 900 then
    raise exception 'WORKER_DEPLOY_CLAIM_INPUT_INVALID';
  end if;
  -- Serialize every target revision for this Render worker service.
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_service_id, 0));
  select intent.target_sha into v_in_flight_sha
    from wow_release_control.render_worker_deploy_intents intent
   where intent.service_id=p_service_id and intent.target_sha<>p_target_sha
     and intent.state in ('INITIATING','ACK_AMBIGUOUS','DEPLOYING','HOLD')
   limit 1 for update of intent;
  if found then
    raise exception 'WORKER_PREVIOUS_SHA_IN_FLIGHT_RECONCILE_ONLY';
  end if;
  insert into wow_release_control.render_worker_deploy_intents
      (service_id,target_sha,state)
    values (p_service_id,p_target_sha,'PENDING')
    on conflict do nothing;
  select * into v from wow_release_control.render_worker_deploy_intents
    where service_id=p_service_id and target_sha=p_target_sha
    for update;
  if v.state = 'PENDING' and v.attempts < 3
     and (v.lease_expires_at is null or v.lease_expires_at < clock_timestamp()) then
    update wow_release_control.render_worker_deploy_intents d
      set state='INITIATING', owner_run_id=p_owner_run_id,
          lease_token=d.lease_token+1,
          lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),
          attempts=d.attempts+1, updated_at=clock_timestamp()
      where d.service_id=p_service_id and d.target_sha=p_target_sha
      returning * into v;
    return query select v.state,v.lease_token,true;
    return; -- RETURN QUERY appends; stop before emitting a second denial row.
  end if;
  return query select v.state,v.lease_token,false;
end;
$$;
revoke all on function wow_release_control.claim_render_worker_deploy(text,text,text,integer) from public, anon, authenticated;

-- Fenced state transitions require the original run and generation.
-- No transition permits INITIATING/ACK_AMBIGUOUS to revert to PENDING.
create or replace function wow_release_control.record_render_worker_deploy(
  p_service_id text, p_target_sha text, p_owner_run_id text,
  p_lease_token bigint, p_state text, p_render_deploy_id text default null
) returns boolean
language plpgsql security definer
set search_path = pg_catalog, wow_release_control
as $$
declare v wow_release_control.render_worker_deploy_intents%rowtype;
begin
  if p_service_id <> 'srv-dapkbc0473hc73c1978g'
     or p_target_sha !~ '^[0-9a-f]{40}$' then
    raise exception 'WORKER_DEPLOY_RECORD_TARGET_INVALID';
  end if;
  if p_state not in ('ACK_AMBIGUOUS','DEPLOYING','LIVE_VERIFIED','HOLD') then
    raise exception 'WORKER_DEPLOY_TRANSITION_INVALID';
  end if;
  select * into v from wow_release_control.render_worker_deploy_intents
    where service_id=p_service_id and target_sha=p_target_sha for update;
  if not found or v.owner_run_id is distinct from p_owner_run_id
     or v.lease_token is distinct from p_lease_token
     or v.lease_expires_at <= clock_timestamp() then
    raise exception 'WORKER_DEPLOY_FENCING_REJECTED';
  end if;
  if not (
    (v.state='INITIATING' and p_state in ('ACK_AMBIGUOUS','DEPLOYING','LIVE_VERIFIED','HOLD'))
    or (v.state='ACK_AMBIGUOUS' and p_state in ('DEPLOYING','LIVE_VERIFIED','HOLD'))
    or (v.state='DEPLOYING' and p_state in ('LIVE_VERIFIED','HOLD'))
  ) then
    raise exception 'WORKER_DEPLOY_TRANSITION_INVALID_FROM_STATE';
  end if;
  if p_state='DEPLOYING' and
     (p_render_deploy_id is null or p_render_deploy_id !~ '^dep-[a-z0-9]+$') then
    raise exception 'WORKER_DEPLOY_ACTIVE_RECEIPT_MISSING';
  end if;
  if p_state='LIVE_VERIFIED' and
     (p_render_deploy_id is null or p_render_deploy_id !~ '^dep-[a-z0-9]+$') then
    raise exception 'WORKER_DEPLOY_LIVE_RECEIPT_MISSING';
  end if;
  if v.render_deploy_id is not null and p_render_deploy_id is not null
     and v.render_deploy_id <> p_render_deploy_id then
    raise exception 'WORKER_DEPLOY_IDENTITY_CHANGED';
  end if;
  update wow_release_control.render_worker_deploy_intents
     set state=p_state,
         render_deploy_id=coalesce(p_render_deploy_id,v.render_deploy_id),
         updated_at=clock_timestamp()
   where service_id=p_service_id and target_sha=p_target_sha;
  return true;
end;
$$;
revoke all on function wow_release_control.record_render_worker_deploy(text,text,text,bigint,text,text) from public, anon, authenticated;

-- Renewal cannot resurrect an expired owner or overwrite a newer generation.
create or replace function wow_release_control.renew_render_worker_deploy(
  p_service_id text, p_target_sha text, p_owner_run_id text,
  p_lease_token bigint, p_lease_seconds integer default 300
) returns boolean
language plpgsql security definer
set search_path=pg_catalog,wow_release_control
as $$
begin
  if p_service_id <> 'srv-dapkbc0473hc73c1978g'
     or p_target_sha !~ '^[0-9a-f]{40}$' then
    raise exception 'WORKER_DEPLOY_RENEW_TARGET_INVALID';
  end if;
  if p_lease_seconds not between 30 and 900 then
    raise exception 'WORKER_DEPLOY_RENEW_DURATION_INVALID';
  end if;
  update wow_release_control.render_worker_deploy_intents
    set lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),
        updated_at=clock_timestamp()
    where service_id=p_service_id and target_sha=p_target_sha
      and owner_run_id=p_owner_run_id and lease_token=p_lease_token
      and lease_expires_at > clock_timestamp()
      and state in ('INITIATING','ACK_AMBIGUOUS','DEPLOYING');
  if not found then
    raise exception 'WORKER_DEPLOY_RENEW_FENCING_REJECTED';
  end if;
  return true;
end;
$$;
revoke all on function wow_release_control.renew_render_worker_deploy(text,text,text,bigint,integer) from public,anon,authenticated;

-- Do not silently create a production service login or grant access to
-- service_role. The independent DBA/security actor provisions this exact
-- non-superuser identity before applying the authorized migration.
do $role_gate$
begin
  if not exists (
    select 1 from pg_catalog.pg_roles where rolname='wow_release_controller'
      and not rolsuper and not rolbypassrls and rolcanlogin
  ) then
    raise exception 'WOW_RELEASE_CONTROLLER_ROLE_NOT_AUTHORIZED';
  end if;
end;
$role_gate$;

-- RLS remains enabled for both private relations. An unprivileged login
-- with ordinary GRANTs cannot use SECURITY INVOKER RPCs without explicit RLS
-- policies; limiting both privileges and row scope avoids a false-green ACL.
do $policy_gate$
begin
  if not exists (
    select 1 from pg_catalog.pg_policies
     where schemaname='wow_release_control'
       and tablename='release_control_identity'
       and policyname='wow_release_identity_select'
  ) then
    execute 'create policy wow_release_identity_select on wow_release_control.release_control_identity for select to wow_release_controller using (singleton = true)';
  end if;
  if not exists (
    select 1 from pg_catalog.pg_policies
     where schemaname='wow_release_control'
       and tablename='render_worker_deploy_intents'
       and policyname='wow_release_intents_select'
  ) then
    execute 'create policy wow_release_intents_select on wow_release_control.render_worker_deploy_intents for select to wow_release_controller using (service_id = ''srv-dapkbc0473hc73c1978g'')';
  end if;
  -- Direct table mutation is forbidden to the release controller.
  -- State transitions are only available through trusted-definer RPCs.

end;
$policy_gate$;

-- Independent DBA must certify function ownership and privilege chain.
-- SECURITY DEFINER functions must not be owned by the release login.
grant usage on schema wow_release_control to wow_release_controller;
grant select on wow_release_control.release_control_identity to wow_release_controller;
revoke insert, update, delete, truncate, references, trigger
  on wow_release_control.render_worker_deploy_intents from wow_release_controller;
grant select on wow_release_control.render_worker_deploy_intents to wow_release_controller;
grant execute on function wow_release_control.claim_render_worker_deploy(text,text,text,integer) to wow_release_controller;
grant execute on function wow_release_control.record_render_worker_deploy(text,text,text,bigint,text,text) to wow_release_controller;
grant execute on function wow_release_control.renew_render_worker_deploy(text,text,text,bigint,integer) to wow_release_controller;
