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

-- Caller must run in a trusted service-side privileged connection.
-- PENDING may be created once. INITIATING means a POST might have been sent:
-- after expiry a different run MUST NOT issue another POST automatically.
create or replace function wow_release_control.claim_render_worker_deploy(
  p_service_id text, p_target_sha text, p_owner_run_id text,
  p_lease_seconds integer default 300
) returns table(state text, lease_token bigint, can_initiate boolean)
language plpgsql security invoker
set search_path = pg_catalog, wow_release_control
as $$
declare v wow_release_control.render_worker_deploy_intents%rowtype;
begin
  if p_service_id <> 'srv-dapkbc0473hc73c1978g'
     or p_target_sha !~ '^[0-9a-f]{40}$'
     or nullif(p_owner_run_id,'') is null
     or p_lease_seconds not between 30 and 900 then
    raise exception 'WORKER_DEPLOY_CLAIM_INPUT_INVALID';
  end if;
  insert into wow_release_control.render_worker_deploy_intents
      (service_id,target_sha,state)
    values (p_service_id,p_target_sha,'PENDING')
    on conflict do nothing;
  select * into v from wow_release_control.render_worker_deploy_intents
    where service_id=p_service_id and target_sha=p_target_sha
    for update;
  if v.state = 'PENDING'
     and (v.lease_expires_at is null or v.lease_expires_at < clock_timestamp()) then
    update wow_release_control.render_worker_deploy_intents d
      set state='INITIATING', owner_run_id=p_owner_run_id,
          lease_token=d.lease_token+1,
          lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),
          attempts=d.attempts+1, updated_at=clock_timestamp()
      where d.service_id=p_service_id and d.target_sha=p_target_sha
      returning * into v;
    return query select v.state,v.lease_token,true;
  end if;
  return query select v.state,v.lease_token,false;
end;
$$;
revoke all on function wow_release_control.claim_render_worker_deploy(text,text,text,integer) from public, anon, authenticated;
