-- WOW V17 maintenance cron deconfliction — 2026-10-03
--
-- Class A/B orchestration reliability only. Preserve the stale-run reconciler's
-- five-minute cadence while moving it off the same minute as the MLB forward
-- capture/hydration jobs. This changes no sporting probability, calibration,
-- qualification, publication, terminal authority, or execution permission.
--
-- Existing MLB schedules:
--   capture:  2,17,32,47 * * * *
--   hydrate:  5,20,35,50 * * * *
-- Reconciler is offset to minute mod 5 = 3 so it collides with neither.

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
        '3,8,13,18,23,28,33,38,43,48,53,58 * * * *',
        'select public.wow_reconcile_stale_pick_request_runs(3600);'
    );
end;
$$;
