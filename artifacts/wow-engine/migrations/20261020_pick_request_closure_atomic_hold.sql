-- V17 pick-request closure: atomic PENDING-row hold + committed-row recount.
--
-- PR #1895 review HOLD: a caller-side read-then-write closure could overwrite a
-- worker's terminal result, and a worker's stale full-row upsert could overwrite
-- a closure hold. This migration moves both decisions into the database:
--
-- 1. wow_v17_close_pick_request_run_rows(run_id, hold_pending) locks the run row,
--    holds only rows that are still PENDING at UPDATE time (row-locked CAS; a
--    concurrently committed terminal row is re-checked and left untouched), then
--    recomputes run counters from committed row states. Idempotent; safe to retry
--    after an uncertain write.
-- 2. A BEFORE UPDATE fence keeps a RUN_CLOSED_BEFORE_TERMINAL hold terminal: a
--    later upsert of that row keeps the committed hold (each row terminates once).
--
-- Bookkeeping only. No probability, calibration, threshold or publication change.
-- can_execute remains false.

create or replace function public.wow_v17_close_pick_request_run_rows(
    p_run_id text,
    p_hold_pending boolean default true
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_catalog
as $$
declare
    v_run public.wow_pick_request_runs%rowtype;
    v_held integer := 0;
    v_total integer;
    v_completed integer;
    v_held_rows integer;
    v_rejected integer;
    v_pending integer;
    v_unresolved integer;
begin
    select * into v_run
    from public.wow_pick_request_runs
    where run_id = p_run_id
    for update;
    if not found then
        return jsonb_build_object(
            'status', 'RUN_NOT_FOUND', 'run_id', p_run_id,
            'rows_held_at_closure', 0, 'can_execute', false
        );
    end if;
    if v_run.closed_at is null or v_run.closure_code is null then
        return jsonb_build_object(
            'status', 'RUN_NOT_CLOSED', 'run_id', p_run_id,
            'rows_held_at_closure', 0, 'can_execute', false
        );
    end if;

    if p_hold_pending and not coalesce(v_run.reopen_allowed, false) then
        update public.wow_pick_request_row_states s
        set terminal_status = 'HELD',
            terminal_code = 'RUN_CLOSED_BEFORE_TERMINAL',
            failure_domain = 'RUN_CLOSURE',
            durable_status = 'HELD:RUN_CLOSED_BEFORE_TERMINAL',
            model_evaluated = false,
            probability_publishable = false,
            rank_eligible = false,
            outcome = jsonb_build_object(
                'row_key', s.row_key,
                'terminal_status', 'HELD',
                'code', 'RUN_CLOSED_BEFORE_TERMINAL',
                'model_evaluated', false,
                'probability_publishable', false,
                'rank_eligible', false,
                'pick_rejected', false,
                'infrastructure_blocked', true,
                'detail', jsonb_build_object(
                    'run_closure_code', v_run.closure_code,
                    'stage_at_closure', s.current_stage,
                    'specialist_scoring_attempted', false,
                    'can_execute', false
                ),
                'can_execute', false
            ),
            updated_at = now()
        where s.run_id = p_run_id
          and s.terminal_status = 'PENDING';
        get diagnostics v_held = row_count;
    end if;

    select
        count(*),
        count(*) filter (where terminal_status = 'COMPLETED'),
        count(*) filter (where terminal_status = 'HELD'),
        count(*) filter (where terminal_status = 'REJECTED'),
        count(*) filter (where terminal_status = 'PENDING'),
        count(*) filter (
            where terminal_status = 'PENDING'
               or (model_evaluated is true and stage_seq < 4)
        )
    into v_total, v_completed, v_held_rows, v_rejected, v_pending, v_unresolved
    from public.wow_pick_request_row_states
    where run_id = p_run_id;

    update public.wow_pick_request_runs
    set total_rows = v_total,
        completed_rows = v_completed,
        held_rows = v_held_rows,
        rejected_rows = v_rejected,
        pending_rows = v_pending,
        unresolved_rows = v_unresolved,
        run_status = v_run.closure_code,
        updated_at = now()
    where run_id = p_run_id;

    return jsonb_build_object(
        'status', 'RECONCILED',
        'run_id', p_run_id,
        'closure_code', v_run.closure_code,
        'rows_held_at_closure', v_held,
        'total_rows', v_total,
        'completed_rows', v_completed,
        'held_rows', v_held_rows,
        'rejected_rows', v_rejected,
        'pending_rows', v_pending,
        'unresolved_rows', v_unresolved,
        'reconciliation_pass', v_total = v_completed + v_held_rows + v_rejected,
        'can_execute', false
    );
end;
$$;

revoke all on function public.wow_v17_close_pick_request_run_rows(text, boolean) from public, anon, authenticated;
grant execute on function public.wow_v17_close_pick_request_run_rows(text, boolean) to service_role;

create or replace function public.wow_v17_pick_request_row_closure_fence()
returns trigger
language plpgsql
set search_path = public, pg_catalog
as $$
begin
    if old.terminal_status = 'HELD'
       and old.terminal_code = 'RUN_CLOSED_BEFORE_TERMINAL' then
        return old;
    end if;
    return new;
end;
$$;

revoke all on function public.wow_v17_pick_request_row_closure_fence() from public, anon, authenticated;

drop trigger if exists wow_v17_pick_request_row_closure_fence
    on public.wow_pick_request_row_states;
create trigger wow_v17_pick_request_row_closure_fence
    before update on public.wow_pick_request_row_states
    for each row execute function public.wow_v17_pick_request_row_closure_fence();
