-- V17 MLB grader cron restoration — 2026-10-15
--
-- Class B orchestration/reliability repair (evidence persistence). No change to
-- fitted model mathematics, calibration, lower bounds, qualification
-- thresholds, publication authority, terminal reduction, or execution
-- permissions. probability_publishable=false is not granted by either grader;
-- can_execute=false remains binding on every graded row.
--
-- Root cause (production evidence, Supabase iczfhsmjrrafhvcpmqhr):
--   * cron.job drifted empty on 2026-09-27 after a pg_cron
--     "job startup timeout" storm (six jobs started on minute :15).
--   * 20260927_restore_mlb_forward_cron_jobs.sql reinstalled only capture and
--     hydrate. Later migrations (20261003022500, 20261003033000) only
--     reschedule jobs that already exist, so both graders stayed absent.
--   * Last runs: wow-mlb-forward-shadow-auto-grade 2026-09-27 00:15 UTC,
--     wow-governed-primary-ledger-auto-grade 2026-09-27 00:15 UTC.
--   * Effect: MLB forward shadow grades frozen at 390 (last 2026-09-26),
--     certification evidence stale for 19 days while capture kept running.
--
-- Reproduction (forced-rollback dry run, 2026-10-15): both functions complete
-- without error (forward grader: 43 graded / 0 errors, 2.7 s; governed grader:
-- 200 checked per pass, 11.9 s).
--
-- Schedules respect the >=4 minute runtime spacing enforced by
-- test_maintenance_cron_collision_guard.py against the live job set
-- (capture 2,17,32,47; hydrate 8,23,38,53; stale reconcile 13). The old
-- 7,22,37,52 / 9,24,39,54 grader minutes sit 1 minute from hydrate and are not
-- reused. Grading is post-settlement and not latency-critical.
--
-- Idempotent: installs when absent; reschedules an existing job by name.

do $$
declare
  v record;
  v_job_id bigint;
begin
  for v in
    select *
    from (values
      (
        'wow-mlb-forward-shadow-auto-grade',
        '27,57 * * * *',
        'select public.wow_mlb_forward_auto_grade_completed();'
      ),
      (
        'wow-governed-primary-ledger-auto-grade',
        '42 * * * *',
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
    end if;

    perform cron.schedule(v.jobname, v.cron_expr, v.command_text);
  end loop;
end;
$$;
