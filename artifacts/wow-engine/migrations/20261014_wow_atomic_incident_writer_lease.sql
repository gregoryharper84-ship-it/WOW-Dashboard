-- #1739: atomic, fenced incident writer leases. Apply only through governed review.
-- No client grants. The service_role is the only permitted RPC caller.
create table if not exists public.wow_engineering_incident_writer_leases (
  incident_id bigint primary key check (incident_id > 0),
  owner_id text not null check (length(owner_id) between 8 and 256),
  epoch bigint not null check (epoch > 0),
  lease_group text not null,
  branch text not null default '',
  pr_number bigint,
  state text not null check (state in ('HELD','RELEASED')),
  acquired_at timestamptz not null,
  expires_at timestamptz not null,
  updated_at timestamptz not null
);
alter table public.wow_engineering_incident_writer_leases enable row level security;
revoke all on public.wow_engineering_incident_writer_leases from public, anon, authenticated;
grant select, insert, update on public.wow_engineering_incident_writer_leases to service_role;

create or replace function public.wow_engineering_claim_writer_lease(
 p_incident_id bigint, p_owner_id text, p_lease_group text, p_branch text,
 p_pr_number bigint default null, p_ttl_seconds integer default 900
) returns jsonb language plpgsql security invoker set search_path = pg_catalog, public
as $$
declare result_row public.wow_engineering_incident_writer_leases%rowtype;
begin
 if p_incident_id is null or p_incident_id <= 0
    or length(coalesce(p_owner_id,'')) not between 8 and 256
    or length(coalesce(p_lease_group,'')) not between 1 and 80
    or length(coalesce(p_branch,'')) > 255
    or p_ttl_seconds not between 60 and 1800 then
   raise exception 'INVALID_LEASE_REQUEST' using errcode = '22023';
 end if;
 insert into public.wow_engineering_incident_writer_leases as l
  (incident_id,owner_id,epoch,lease_group,branch,pr_number,state,acquired_at,expires_at,updated_at)
 values (p_incident_id,p_owner_id,1,p_lease_group,coalesce(p_branch,''),p_pr_number,
         'HELD',clock_timestamp(),clock_timestamp()+make_interval(secs=>p_ttl_seconds),clock_timestamp())
 on conflict (incident_id) do update set
   owner_id=excluded.owner_id, epoch=l.epoch+1, lease_group=excluded.lease_group,
   branch=excluded.branch,pr_number=excluded.pr_number,state='HELD',
   acquired_at=clock_timestamp(),expires_at=clock_timestamp()+make_interval(secs=>p_ttl_seconds),
   updated_at=clock_timestamp()
 where l.state='RELEASED' or l.expires_at <= clock_timestamp()
 returning * into result_row;
 if not found then return jsonb_build_object('ok',false,'reason','LEASE_BUSY'); end if;
 return jsonb_build_object('ok',true,'incident_id',result_row.incident_id,
   'owner_id',result_row.owner_id,'epoch',result_row.epoch,'expires_at',result_row.expires_at);
end $$;

create or replace function public.wow_engineering_check_writer_lease(
 p_incident_id bigint,p_owner_id text,p_epoch bigint
) returns boolean language sql volatile security invoker set search_path = pg_catalog, public as $$
 select exists(select 1 from public.wow_engineering_incident_writer_leases
 where incident_id=p_incident_id and owner_id=p_owner_id and epoch=p_epoch
 and state='HELD' and expires_at > clock_timestamp())
$$;

create or replace function public.wow_engineering_renew_writer_lease(
 p_incident_id bigint,p_owner_id text,p_epoch bigint,p_ttl_seconds integer default 900
) returns boolean language plpgsql security invoker set search_path = pg_catalog, public as $$
begin
 if p_ttl_seconds not between 60 and 1800 then return false; end if;
 update public.wow_engineering_incident_writer_leases
 set expires_at=clock_timestamp()+make_interval(secs=>p_ttl_seconds), updated_at=clock_timestamp()
 where incident_id=p_incident_id and owner_id=p_owner_id and epoch=p_epoch
 and state='HELD' and expires_at > clock_timestamp();
 return found;
end $$;

create or replace function public.wow_engineering_release_writer_lease(
 p_incident_id bigint,p_owner_id text,p_epoch bigint
) returns boolean language plpgsql security invoker set search_path = pg_catalog, public as $$
begin
 update public.wow_engineering_incident_writer_leases
 set state='RELEASED',expires_at=clock_timestamp(),updated_at=clock_timestamp()
 where incident_id=p_incident_id and owner_id=p_owner_id and epoch=p_epoch and state='HELD';
 return found;
end $$;

revoke all on function public.wow_engineering_claim_writer_lease(bigint,text,text,text,bigint,integer) from public,anon,authenticated;
revoke all on function public.wow_engineering_check_writer_lease(bigint,text,bigint) from public,anon,authenticated;
revoke all on function public.wow_engineering_renew_writer_lease(bigint,text,bigint,integer) from public,anon,authenticated;
revoke all on function public.wow_engineering_release_writer_lease(bigint,text,bigint) from public,anon,authenticated;
grant execute on function public.wow_engineering_claim_writer_lease(bigint,text,text,text,bigint,integer) to service_role;
grant execute on function public.wow_engineering_check_writer_lease(bigint,text,bigint) to service_role;
grant execute on function public.wow_engineering_renew_writer_lease(bigint,text,bigint,integer) to service_role;
grant execute on function public.wow_engineering_release_writer_lease(bigint,text,bigint) to service_role;

-- Append-only audit of ownership transitions; never treat telemetry as a lock.
create table if not exists public.wow_engineering_writer_lease_events (
 event_id bigint generated always as identity primary key,
 incident_id bigint not null,
 owner_id text not null,
 epoch bigint not null,
 lease_group text not null,
 branch text not null,
 pr_number bigint,
 state text not null,
 event_kind text not null,
 recorded_at timestamptz not null default clock_timestamp()
);
alter table public.wow_engineering_writer_lease_events enable row level security;
revoke all on public.wow_engineering_writer_lease_events from public,anon,authenticated;
grant select, insert on public.wow_engineering_writer_lease_events to service_role;

create or replace function public.wow_engineering_record_lease_transition()
returns trigger language plpgsql security invoker set search_path = pg_catalog, public as $$
begin
 insert into public.wow_engineering_writer_lease_events
  (incident_id,owner_id,epoch,lease_group,branch,pr_number,state,event_kind)
 values (new.incident_id,new.owner_id,new.epoch,new.lease_group,new.branch,
         new.pr_number,new.state,
         case when TG_OP='INSERT' then 'CLAIM'
              when new.state='RELEASED' then 'RELEASE'
              when old.epoch <> new.epoch then 'RECLAIM'
              else 'RENEW' end);
 return new;
end $$;
revoke all on function public.wow_engineering_record_lease_transition() from public,anon,authenticated;
grant execute on function public.wow_engineering_record_lease_transition() to service_role;
drop trigger if exists wow_engineering_writer_lease_audit on public.wow_engineering_incident_writer_leases;
create trigger wow_engineering_writer_lease_audit
after insert or update on public.wow_engineering_incident_writer_leases
for each row execute function public.wow_engineering_record_lease_transition();
