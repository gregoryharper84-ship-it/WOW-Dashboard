-- WOW V17 durable pick-request stale-run reconciliation.
--
-- This migration changes orchestration state only. It never scores a row,
-- mutates a sporting probability, changes calibration, or enables execution.
-- Durable row state remains immutable evidence for deciding whether a stale
-- parent run can be finalized or must stop under exact-once protection.

create or replace function public.wow_reconcile_stale_pick_request_runs(
    p_stale_after_seconds integer default 3600
)
returns table (
    out_run_id text,
    prior_run_status text,
    reconciled_run_status text,
    persisted_rows integer,
    pending_rows integer,
    unresolved_rows integer,
    reconciliation_action text
)
language plpgsql
security definer
set search_path = public, pg_catalog
as $$
declare
    v_now timestamptz := clock_timestamp();
    v_cutoff timestamptz;
    v_locked_run_ids text[];
begin
    if p_stale_after_seconds < 300 then
        raise exception 'STALE_AFTER_SECONDS_TOO_SMALL';
    end if;

    -- Claims take FOR SHARE on the parent. Lock eligible parents first,
    -- then check leases in a separate command with a fresh snapshot.
    -- A reclaim committed during the lock scan must be visible to that check.
    if current_setting('transaction_isolation') <> 'read committed' then
        raise exception 'reconciliation requires READ COMMITTED'
            using errcode = '25001';
    end if;
    v_now := clock_timestamp();
    v_cutoff := v_now - make_interval(secs => p_stale_after_seconds);
    select array_agg(locked.run_id) into v_locked_run_ids
    from (
        select r.run_id
        from public.wow_pick_request_runs r
        where r.run_status = 'RUNNING' and r.updated_at < v_cutoff
        for update of r skip locked
    ) locked;
    if v_locked_run_ids is null then
        return;
    end if;
    v_now := clock_timestamp();

    return query
    with candidates as (
        select
            r.run_id,
            r.run_status as prior_status,
            r.total_rows as manifest_total_rows,
            r.closure_metadata,
            r.resumed_rows,
            r.updated_at
        from public.wow_pick_request_runs r
        where r.run_id = any(v_locked_run_ids)
          and r.run_status = 'RUNNING'
          and r.updated_at < v_cutoff
          and not exists (
              select 1
              from public.wow_pick_request_jobs j
              where j.run_id = r.run_id
                and (
                    j.status in ('QUEUED', 'RETRY_WAIT')
                    or (
                        j.status = 'RUNNING'
                        and (j.lease_expires_at is null or j.lease_expires_at > v_now)
                    )
                )
          )
        for update of r skip locked
    ), aggregates as (
        select
            c.run_id,
            c.prior_status,
            c.manifest_total_rows,
            c.closure_metadata,
            c.resumed_rows,
            count(s.row_key)::integer as persisted_rows,
            count(*) filter (where s.terminal_status = 'COMPLETED')::integer as completed_rows,
            count(*) filter (where s.terminal_status = 'HELD')::integer as held_rows,
            count(*) filter (where s.terminal_status = 'REJECTED')::integer as rejected_rows,
            count(*) filter (where s.terminal_status = 'PENDING')::integer as pending_rows,
            count(*) filter (
                where s.terminal_status = 'PENDING'
                   or (s.model_evaluated is true and coalesce(s.stage_seq, 0) < 4)
            )::integer as unresolved_rows
        from candidates c
        left join public.wow_pick_request_row_states s on s.run_id = c.run_id
        group by
            c.run_id,
            c.prior_status,
            c.manifest_total_rows,
            c.closure_metadata,
            c.resumed_rows
    ), decisions as (
        select
            a.*,
            case
                when a.persisted_rows = 0 then 'STOPPED_INFRASTRUCTURE_EXACT_ONCE_PROTECTED'
                when a.persisted_rows <> a.manifest_total_rows then 'STOPPED_INFRASTRUCTURE_EXACT_ONCE_PROTECTED'
                when a.pending_rows > 0 then 'STOPPED_INFRASTRUCTURE_EXACT_ONCE_PROTECTED'
                when a.unresolved_rows > 0 then 'STOPPED_INFRASTRUCTURE_EXACT_ONCE_PROTECTED'
                when a.completed_rows + a.held_rows + a.rejected_rows <> a.persisted_rows then 'STOPPED_INFRASTRUCTURE_EXACT_ONCE_PROTECTED'
                when a.held_rows = 0 then 'COMPLETE'
                when a.completed_rows > 0 then 'DEGRADED'
                else 'BLOCKED'
            end as new_status,
            case
                when a.persisted_rows = 0 then 'STALE_RUN_MANIFEST_ROWS_MISSING'
                when a.persisted_rows <> a.manifest_total_rows then 'STALE_RUN_MANIFEST_ROW_COUNT_MISMATCH'
                when a.pending_rows > 0 then 'STALE_RUN_PENDING_WITHOUT_ACTIVE_JOB'
                when a.unresolved_rows > 0 then 'RECEIPT_SERVICE_UNREACHABLE'
                when a.completed_rows + a.held_rows + a.rejected_rows <> a.persisted_rows then 'PICK_REQUEST_RECONCILIATION_FAILED'
                else null
            end as stop_reason
        from aggregates a
    ), updated_runs as (
        update public.wow_pick_request_runs r
        set
            run_status = d.new_status,
            total_rows = case
                when d.persisted_rows = d.manifest_total_rows then d.persisted_rows
                else r.total_rows
            end,
            completed_rows = d.completed_rows,
            held_rows = d.held_rows,
            rejected_rows = d.rejected_rows,
            pending_rows = d.pending_rows,
            unresolved_rows = d.unresolved_rows,
            closure_code = case
                when d.new_status = 'STOPPED_INFRASTRUCTURE_EXACT_ONCE_PROTECTED'
                    then 'STOPPED_INFRASTRUCTURE_EXACT_ONCE_PROTECTED'
                else r.closure_code
            end,
            closure_reason = case
                when d.new_status = 'STOPPED_INFRASTRUCTURE_EXACT_ONCE_PROTECTED'
                    then d.stop_reason
                else r.closure_reason
            end,
            closure_metadata = case
                when d.new_status = 'STOPPED_INFRASTRUCTURE_EXACT_ONCE_PROTECTED' then
                    coalesce(r.closure_metadata, '{}'::jsonb) || jsonb_build_object(
                        'reconciler_version', 'V17_PICK_REQUEST_STALE_RECONCILER_V1',
                        'stale_after_seconds', p_stale_after_seconds,
                        'active_job_proven', false,
                        'manifest_total_rows', d.manifest_total_rows,
                        'persisted_rows', d.persisted_rows,
                        'completed_rows', d.completed_rows,
                        'held_rows', d.held_rows,
                        'rejected_rows', d.rejected_rows,
                        'pending_rows', d.pending_rows,
                        'unresolved_rows', d.unresolved_rows,
                        'typed_stop_reason', d.stop_reason
                    )
                else r.closure_metadata
            end,
            closed_at = case
                when d.new_status = 'STOPPED_INFRASTRUCTURE_EXACT_ONCE_PROTECTED'
                    then coalesce(r.closed_at, v_now)
                else r.closed_at
            end,
            reopen_allowed = case
                when d.new_status = 'STOPPED_INFRASTRUCTURE_EXACT_ONCE_PROTECTED'
                    then false
                else r.reopen_allowed
            end,
            run_control_version = 'V17_PICK_REQUEST_STALE_RECONCILER_V1',
            updated_at = v_now,
            can_execute = false
        from decisions d
        where r.run_id = d.run_id
          and r.run_status = 'RUNNING'
          and r.updated_at < v_cutoff
        returning
            r.run_id,
            d.prior_status,
            r.run_status,
            d.persisted_rows,
            d.pending_rows,
            d.unresolved_rows,
            case
                when r.run_status = 'STOPPED_INFRASTRUCTURE_EXACT_ONCE_PROTECTED'
                    then d.stop_reason
                else 'FINALIZED_FROM_DURABLE_ROW_TRUTH'
            end as reconciliation_action
    ), updated_jobs as (
        update public.wow_pick_request_jobs j
        set
            status = case
                when u.run_status = 'STOPPED_INFRASTRUCTURE_EXACT_ONCE_PROTECTED' then 'STOPPED'
                else 'COMPLETED'
            end,
            lease_owner = null,
            lease_expires_at = null,
            next_attempt_at = null,
            last_error = case
                when u.run_status = 'STOPPED_INFRASTRUCTURE_EXACT_ONCE_PROTECTED'
                    then jsonb_build_object(
                        'code', u.reconciliation_action,
                        'reconciler_version', 'V17_PICK_REQUEST_STALE_RECONCILER_V1'
                    )
                else null
            end,
            updated_at = v_now,
            can_execute = false
        from updated_runs u
        where j.run_id = u.run_id
          and j.status not in ('COMPLETED', 'STOPPED', 'QUEUED', 'RETRY_WAIT')
          and not (j.status = 'RUNNING' and
                   (j.lease_expires_at is null or j.lease_expires_at > v_now))
        returning j.run_id
    )
    select
        u.run_id,
        u.prior_status,
        u.run_status,
        u.persisted_rows,
        u.pending_rows,
        u.unresolved_rows,
        u.reconciliation_action
    from updated_runs u
    order by u.run_id;
end;
$$;

revoke all on function public.wow_reconcile_stale_pick_request_runs(integer) from public, anon, authenticated;
grant execute on function public.wow_reconcile_stale_pick_request_runs(integer) to service_role;

-- The worker claim must participate in the same run-row protocol. Otherwise a
-- claim can still commit a fresh RUNNING lease onto a run this pass has just
-- stopped: the claim reads run status from its own snapshot and never locks
-- the parent run. FOR SHARE keeps concurrent claims of different jobs in one
-- run compatible while conflicting with the reconciler's FOR UPDATE; SKIP
-- LOCKED turns that contention into a bounded "no job" retry instead of a wait
-- or a deadlock. Lock footprint and retry semantics are otherwise unchanged.
create or replace function public.wow_claim_pick_request_job(
    p_worker_id text,
    p_lease_seconds integer default 1800
)
returns setof public.wow_pick_request_jobs
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
begin
    if coalesce(trim(p_worker_id), '') = '' then
        raise exception 'worker id required';
    end if;

    return query
    with candidate as (
        select j.job_id
        from public.wow_pick_request_jobs j
        join public.wow_pick_request_runs r on r.run_id = j.run_id
        where j.status in ('QUEUED','RETRY_WAIT','RUNNING')
          and left(r.run_status, 8) <> 'STOPPED_'
          and left(r.run_status, 6) <> 'CLOSED'
          and (j.next_attempt_at is null or j.next_attempt_at <= now())
          and (
              j.status <> 'RUNNING'
              or j.lease_expires_at is null
              or j.lease_expires_at <= now()
          )
        order by j.updated_at asc, j.created_at asc
        for update of j skip locked
        for share of r skip locked
        limit 1
    )
    update public.wow_pick_request_jobs j
       set status = 'RUNNING',
           lease_owner = p_worker_id,
           lease_expires_at = now() + make_interval(secs => greatest(60, least(coalesce(p_lease_seconds, 1800), 7200))),
           updated_at = now()
      from candidate c
     where j.job_id = c.job_id
    returning j.*;
end;
$$;

revoke all on function public.wow_claim_pick_request_job(text, integer) from public, anon, authenticated;
grant execute on function public.wow_claim_pick_request_job(text, integer) to service_role;

-- Exact-once terminal fence. A direct /score-pick-request writer can finish
-- after this reconciler stops an expired parent run. A later plain UPDATE or
-- INSERT ... ON CONFLICT DO UPDATE must not resurrect that stopped run.
-- This is independent of Python wrapper patches and applies to all writers.
create or replace function public.wow_reject_stopped_pick_request_run_reopen()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog, public
as $$
begin
    if left(coalesce(old.run_status, ''), 8) = 'STOPPED_'
       and new.run_status is distinct from old.run_status then
        raise exception 'PICK_REQUEST_STOPPED_RUN_IMMUTABLE'
            using errcode = '23514';
    end if;
    return new;
end;
$$;

revoke all on function public.wow_reject_stopped_pick_request_run_reopen()
    from public, anon, authenticated;
grant execute on function public.wow_reject_stopped_pick_request_run_reopen()
    to service_role;

drop trigger if exists wow_pick_request_stopped_run_fence
    on public.wow_pick_request_runs;
create trigger wow_pick_request_stopped_run_fence
    before update on public.wow_pick_request_runs
    for each row
    execute function public.wow_reject_stopped_pick_request_run_reopen();

