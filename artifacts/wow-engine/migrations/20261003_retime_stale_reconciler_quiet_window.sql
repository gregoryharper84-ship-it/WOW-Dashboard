-- WOW V17 stale-run reconciler quiet-window retime — 2026-10-03
--
-- Class B orchestration reliability only. Stale runs are not eligible until
-- they are at least 3600 seconds old, so a 15-minute maintenance cadence is
-- sufficient and materially reduces database pressure.
--
-- MLB maintenance cadence:
--   capture:   2,17,32,47
--   hydrate:   5,20,35,50
-- Reconciler runs at the midpoint quiet window:
--   reconcile: 11,26,41,56
-- This leaves six minutes after hydration and six minutes before the next
-- capture in each 15-minute cycle.
--
-- No sporting probability, calibration, qualification, publication,
-- terminal-authority, or execution behavior is changed.

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
        '11,26,41,56 * * * *',
        'select public.wow_reconcile_stale_pick_request_runs(3600);'
    );
end;
$$;
