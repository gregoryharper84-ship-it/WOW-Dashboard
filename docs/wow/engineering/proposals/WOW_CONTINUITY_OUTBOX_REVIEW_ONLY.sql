-- DRAFT DESIGN CONTRACT ONLY. DO NOT APPLY DIRECTLY TO PRODUCTION.
-- This is intentionally NOT a migrations/ file. Authorized DB owner must
-- generate/reconcile migration history using the canonical Supabase CLI,
-- review against the live schema, approve independently and test transaction
-- isolation, RLS/grants and app provenance before controlled application.
--
-- Prerequisite verified in read-only production Oct 15 2026:
-- public.wow_engineering_check_writer_lease(bigint,text,bigint) exists,
-- SECURITY INVOKER, EXECUTE service_role only.
--
-- This outbox prevents duplicate external dispatch after restart/uncertain ACK.
-- It deliberately cannot auto-release an ambiguous dispatch. Reconciliation
-- requires a separate independent authenticated GitHub workflow-run proof.

create table if not exists public.wow_engineering_continuity_outbox (
  incident_id bigint not null check (incident_id > 0),
  intent_id uuid primary key,
  main_sha text not null check (main_sha ~ '^[0-9a-f]{40}$'),
  action text not null check (action in ('DISPATCH_NEW_REPAIR','REPAIR_SAME_BRANCH')),
  pr_number bigint,
  pr_head_sha text,
  writer_owner_id text not null,
  writer_epoch bigint not null check (writer_epoch > 0),
  state text not null check (state in (
    'RESERVED','ARMED','ATTEMPT_ACK_UNCORRELATED',
    'ATTEMPT_ACK_CORRELATED_UNVERIFIED','ATTEMPT_AMBIGUOUS',
    'PREARM_ABORTED'
  )),
  workflow_run_id bigint,
  receipt_code text,
  created_at timestamptz not null default clock_timestamp(),
  armed_at timestamptz,
  updated_at timestamptz not null default clock_timestamp(),
  constraint action_identity_check check (
    (action='DISPATCH_NEW_REPAIR' and pr_number is null and pr_head_sha is null)
    or (action='REPAIR_SAME_BRANCH' and pr_number is not null
        and pr_number > 0 and pr_head_sha is not null
        and pr_head_sha ~ '^[0-9a-f]{40}$')
  ),
  constraint run_receipt_check check (
    workflow_run_id is null or workflow_run_id > 0
  )
);
-- Immutable aborted rows remain as history; only one active incident intent.
-- An aborted intent can never be ARMED.
create unique index if not exists wow_engineering_continuity_outbox_active_incident
on public.wow_engineering_continuity_outbox (incident_id)
where state <> 'PREARM_ABORTED';
alter table public.wow_engineering_continuity_outbox enable row level security;
revoke all on public.wow_engineering_continuity_outbox from public, anon, authenticated;
grant select, insert, update on public.wow_engineering_continuity_outbox to service_role;

-- Always acquire the independent writer lease in the trusted worker first.
-- INSERT conflict serialization is server-owned (never SELECT -> then INSERT).
-- A past incident record stays IN_FLIGHT until a separately certified
-- reconciliation RPC (not part of this proposal) documents a terminal proof.
create or replace function public.wow_engineering_continuity_outbox_reserve(
  p_incident_id bigint, p_intent_id uuid, p_main_sha text, p_action text,
  p_pr_number bigint, p_pr_head_sha text, p_owner_id text, p_epoch bigint
) returns jsonb
language plpgsql
set search_path = pg_catalog, public
as $$
declare row_record public.wow_engineering_continuity_outbox%rowtype;
begin
  if p_intent_id is null or p_incident_id is null or p_incident_id <= 0
    or p_main_sha is null or p_main_sha !~ '^[0-9a-f]{40}$'
    or p_action is null or p_owner_id is null
    or p_action not in ('DISPATCH_NEW_REPAIR','REPAIR_SAME_BRANCH')
    or not (
      (p_action='DISPATCH_NEW_REPAIR' and p_pr_number is null and p_pr_head_sha is null)
      or (p_action='REPAIR_SAME_BRANCH' and p_pr_number is not null
          and p_pr_number > 0 and p_pr_head_sha is not null
          and p_pr_head_sha ~ '^[0-9a-f]{40}$')
    )
    or p_epoch is null or p_epoch <= 0 then
    return jsonb_build_object('status','INVALID_REQUEST');
  end if;
  if not public.wow_engineering_check_writer_lease(
    p_incident_id,p_owner_id,p_epoch
  ) then
    return jsonb_build_object('status','LEASE_NOT_VERIFIED');
  end if;
  insert into public.wow_engineering_continuity_outbox
    (incident_id,intent_id,main_sha,action,pr_number,pr_head_sha,
     writer_owner_id,writer_epoch,state)
  values (p_incident_id,p_intent_id,p_main_sha,p_action,p_pr_number,p_pr_head_sha,
          p_owner_id,p_epoch,'RESERVED')
  on conflict do nothing
  returning * into row_record;
  if not found then
    return jsonb_build_object('status','IN_FLIGHT');
  end if;
  return jsonb_build_object(
    'status','RESERVED',
    'incident',row_record.incident_id,
    'intent_id',row_record.intent_id,
    'main_sha',row_record.main_sha,
    'action',row_record.action,
    'pr_number',row_record.pr_number,
    'pr_head_sha',row_record.pr_head_sha,
    'epoch',row_record.writer_epoch
  );
end $$;

-- Before provider dispatch, the still-authorized exact writer may abort a
-- NEVER-ARMED intent. This preserves durable audit history and unblocks
-- the incident without permitting retries of any ARMED or ambiguous dispatch.
create or replace function public.wow_engineering_continuity_outbox_abort(
  p_incident_id bigint, p_intent_id uuid, p_owner_id text, p_epoch bigint,
  p_reason text
) returns boolean
language plpgsql
set search_path = pg_catalog, public
as $$
begin
  if p_reason is null or p_reason not in (
      'MAIN_CHANGED_BEFORE_DISPATCH','PR_HEAD_RECHECK_UNAVAILABLE',
      'PR_CHANGED_BEFORE_DISPATCH','WRITER_FENCE_UNVERIFIED',
      'PREARM_RECHECK_UNAVAILABLE'
    ) or coalesce(public.wow_engineering_check_writer_lease(
      p_incident_id,p_owner_id,p_epoch
    ),false) is not true then
      return false;
  end if;
  update public.wow_engineering_continuity_outbox
  set state='PREARM_ABORTED',receipt_code=p_reason,
      updated_at=clock_timestamp()
  where incident_id=p_incident_id and intent_id=p_intent_id
    and writer_owner_id=p_owner_id and writer_epoch=p_epoch
    and state='RESERVED';
  return found;
end $$;

-- Recover a crash during the PRE-ARM interval ONLY after the old lease
-- has been superseded and a newer exact incident lease is held by this caller.
-- No timeout alone authorizes a new dispatch. State RESERVED proves no POST
-- was permitted by compliant workers (all workers must use ARM before POST).
-- Serialize with the competing ARM/ABORT by locking the immutable intent row.
-- This cannot release ARMED / acknowledged / ambiguous attempts.
create or replace function public.wow_engineering_continuity_outbox_reclaim(
  p_incident_id bigint, p_old_intent_id uuid,
  p_new_owner_id text, p_new_epoch bigint
) returns boolean
language plpgsql
set search_path = pg_catalog, public
as $$
declare old_epoch bigint;
begin
  if p_old_intent_id is null
      or p_new_epoch is null or p_new_epoch <= 1
      or coalesce(public.wow_engineering_check_writer_lease(
        p_incident_id,p_new_owner_id,p_new_epoch
      ),false) is not true then
    return false;
  end if;
  -- The active-intent partial unique index prevents multiple RESERVED rows.
  select writer_epoch into old_epoch
  from public.wow_engineering_continuity_outbox
  where incident_id=p_incident_id and intent_id=p_old_intent_id
    and state='RESERVED'
  for update;
  if not found or old_epoch >= p_new_epoch then
    return false;
  end if;
  -- Recheck while holding row lock: a stale/taken-over recovery must deny.
  if coalesce(public.wow_engineering_check_writer_lease(
      p_incident_id,p_new_owner_id,p_new_epoch
    ),false) is not true then
    return false;
  end if;
  update public.wow_engineering_continuity_outbox
  set state='PREARM_ABORTED',
      receipt_code='ABANDONED_PREARM_NEW_EPOCH',
      updated_at=clock_timestamp()
  where incident_id=p_incident_id and intent_id=p_old_intent_id
    and state='RESERVED' and writer_epoch=old_epoch;
  return found;
end $$;

-- Atomic one-shot arm; only one contender may transition RESERVED -> ARMED.
create or replace function public.wow_engineering_continuity_outbox_arm(
  p_incident_id bigint, p_intent_id uuid, p_owner_id text, p_epoch bigint
) returns boolean
language plpgsql
set search_path = pg_catalog, public
as $$
begin
  if not public.wow_engineering_check_writer_lease(
     p_incident_id,p_owner_id,p_epoch
  ) then return false; end if;
  update public.wow_engineering_continuity_outbox
  set state='ARMED',armed_at=clock_timestamp(),updated_at=clock_timestamp()
  where incident_id=p_incident_id and intent_id=p_intent_id
    and writer_owner_id=p_owner_id and writer_epoch=p_epoch
    and state='RESERVED';
  return found;
end $$;

-- Captures one immutable acknowledgment outcome. It never authorizes a retry.
create or replace function public.wow_engineering_continuity_outbox_mark(
  p_incident_id bigint, p_intent_id uuid, p_owner_id text, p_epoch bigint,
  p_receipt_code text, p_run_id bigint
) returns boolean
language plpgsql
set search_path = pg_catalog, public
as $$
begin
  if p_receipt_code is null
      or p_receipt_code not in ('ATTEMPT_ACK_UNCORRELATED',
       'ATTEMPT_ACK_CORRELATED_UNVERIFIED','ATTEMPT_AMBIGUOUS')
      or (p_receipt_code='ATTEMPT_ACK_CORRELATED_UNVERIFIED'
          and (p_run_id is null or p_run_id <= 0))
      or (p_receipt_code<>'ATTEMPT_ACK_CORRELATED_UNVERIFIED'
          and p_run_id is not null)
      or not public.wow_engineering_check_writer_lease(
          p_incident_id,p_owner_id,p_epoch) then
     return false;
  end if;
  update public.wow_engineering_continuity_outbox
  set state=p_receipt_code,receipt_code=p_receipt_code,
      workflow_run_id=p_run_id,updated_at=clock_timestamp()
  where incident_id=p_incident_id and intent_id=p_intent_id
    and writer_owner_id=p_owner_id and writer_epoch=p_epoch
    and state='ARMED';
  return found;
end $$;

-- Do not rely on default public EXECUTE on functions.
revoke all on function public.wow_engineering_continuity_outbox_reserve(
 bigint,uuid,text,text,bigint,text,text,bigint
) from public, anon, authenticated;
revoke all on function public.wow_engineering_continuity_outbox_arm(
 bigint,uuid,text,bigint
) from public, anon, authenticated;
revoke all on function public.wow_engineering_continuity_outbox_abort(
 bigint,uuid,text,bigint,text
) from public, anon, authenticated;
revoke all on function public.wow_engineering_continuity_outbox_reclaim(
 bigint,uuid,text,bigint
) from public, anon, authenticated;
revoke all on function public.wow_engineering_continuity_outbox_mark(
 bigint,uuid,text,bigint,text,bigint
) from public, anon, authenticated;
grant execute on function public.wow_engineering_continuity_outbox_reserve(
 bigint,uuid,text,text,bigint,text,text,bigint
) to service_role;
grant execute on function public.wow_engineering_continuity_outbox_arm(
 bigint,uuid,text,bigint
) to service_role;
grant execute on function public.wow_engineering_continuity_outbox_abort(
 bigint,uuid,text,bigint,text
) to service_role;
grant execute on function public.wow_engineering_continuity_outbox_reclaim(
 bigint,uuid,text,bigint
) to service_role;
grant execute on function public.wow_engineering_continuity_outbox_mark(
 bigint,uuid,text,bigint,text,bigint
) to service_role;

-- Production acceptance additionally needs actual DB race tests:
-- two reserve callers, two arm callers, prearm-abort + new intent race,
-- immutable abort history, blocked arm-after-abort, two-writer epoch reclaim,
-- stale lease cannot reclaim, wrong UUID cannot reclaim, arm/reclaim row race,
-- stale epoch, lease expiry,
-- ambiguous 204, restart after ARM, RLS/role denial and verified receipt replay.
-- This staged SQL deliberately lacks any general reset/force-release function;
-- a separate independently reviewed reconciliation design must be certified.
