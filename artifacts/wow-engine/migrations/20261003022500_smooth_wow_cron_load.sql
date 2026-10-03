-- V17 Supabase cron load smoothing — 2026-10-02
--
-- Class B orchestration reliability only. Production evidence showed PostgREST
-- PGRST002/503 schema-cache failures and PostgreSQL statement/cron startup
-- timeouts during overlapping WOW maintenance jobs. Keep every existing job,
-- function, probability rule, publication gate, terminal reducer and
-- can_execute=false invariant unchanged; only offset start minutes.
--
-- Preserve intentionally absent jobs: only reschedule a job when it already
-- exists in cron.job.

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
        '3,8,13,18,23,28,33,38,43,48,53,58 * * * *',
        'select public.wow_reconcile_stale_pick_request_runs(3600);'
      ),
      (
        'wow-mlb-forward-shadow-auto-hydrate',
        '4,19,34,49 * * * *',
        'select public.wow_mlb_forward_auto_hydrate_pregame();'
      ),
      (
        'wow-mlb-forward-shadow-auto-grade',
        '7,22,37,52 * * * *',
        'select public.wow_mlb_forward_auto_grade_completed();'
      ),
      (
        'wow-governed-primary-ledger-auto-grade',
        '9,24,39,54 * * * *',
        'select public.wow_governed_auto_grade_predictions();'
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
