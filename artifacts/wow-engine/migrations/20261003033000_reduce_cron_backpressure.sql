-- V17 cron backpressure repair — 2026-10-03
--
-- Class B orchestration reliability only.
-- Production evidence showed pg_cron job startup timeouts even after exact-minute
-- collision removal. The stale-run reconciler targets rows stale >= 1 hour, so
-- a five-minute wake cadence creates avoidable connection pressure without
-- improving correctness. MLB capture cadence stays unchanged; hydration stays
-- every 15 minutes but starts six minutes after capture.
--
-- Preserve intentionally absent jobs: only reschedule an existing cron.job.
-- No sporting probability, calibration, publication, terminal authority, or
-- execution behavior changes. can_execute=false remains binding.

do $$
declare
  v record;
  v_job_id bigint;
begin
  for v in
    select *
    from (values
      (
        'wow-v17-reconcile-stale-pick-runs',
        '13 * * * *',
        'select public.wow_reconcile_stale_pick_request_runs(3600);'
      ),
      (
        'wow-mlb-forward-shadow-auto-hydrate',
        '8,23,38,53 * * * *',
        'select public.wow_mlb_forward_auto_hydrate_pregame();'
      )
    ) as schedule(jobname, cron_expr, command_text)
  loop
    select jobid
      into v_job_id
      from cron.job
     where jobname = v.jobname
     limit 1;

    if v_job_id is not null then
      perform cron.unschedule(v_job_id);
      perform cron.schedule(v.jobname, v.cron_expr, v.command_text);
    end if;
  end loop;
end;
$$;
