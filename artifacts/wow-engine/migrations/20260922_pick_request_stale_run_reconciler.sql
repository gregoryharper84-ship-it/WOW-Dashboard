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
begin
    if p_stale_after_seconds < 300 then
        raise exception 'STALE_AFTER_SECONDS_TOO_SMALL';
    end if;

    v_cutoff := v_now - make_interval(secs => p_stale_after_seconds);

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
        where r.run_status = 'RUNNING'
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
                when a.completed_rows = a.persisted_rows then 'COMPLETE'
                when a.completed_rows > 0 then 'DEGRADED'
                else 'BLOCKED'
            end as new_status,
            case
                when a.persisted_rows = 0 then 'STALE_RUN_MANIFEST_ROWS_MISSING'
                when a.persisted_rows <> a.manifest_total_rows then 'STALE_RUN_MANIFEST_ROW_COUNT_MISMATCH'
                when a.pending_rows > 0 then 'STALE_RUN_PENDING_WITHOUT_ACTIVE_JOB'
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
          and j.status not in ('COMPLETED', 'STOPPED')
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

-- Replace any prior copy of this exact maintenance job rather than creating
-- duplicates. The job only reconciles stale orchestration state.
do $$
declare
    v_job_id bigint;
begin
    select jobid
      into v_job_id
      from cron.job
     where jobname = 'wow-v17-reconcile-stale-pick-runs'
     limit 1;

    if v_job_id is not null then
        perform cron.unschedule(v_job_id);
    end if;

    perform cron.schedule(
        'wow-v17-reconcile-stale-pick-runs',
        '*/5 * * * *',
        'select public.wow_reconcile_stale_pick_request_runs(3600);'
    );
end;
$$;
