-- V17 MLB forward-shadow cron reconciliation — 2026-09-27
--
-- Class B orchestration/reliability repair for #943.
--
-- The canonical capture/hydration functions were present in production, but
-- cron.job had drifted to an empty state. That left current canonical MLB rows
-- at feature_hydration_status=NOT_STARTED until a request explicitly forced
-- hydration. Reinstall only the two repository-governed jobs when absent.
--
-- This migration does not alter fitted model mathematics, calibration,
-- qualification thresholds, publication authority, terminal reduction, or
-- execution permissions. The invoked functions remain research/shadow paths
-- with probability_publishable=false and can_execute=false.

do $$
begin
  if not exists (
    select 1
    from cron.job
    where jobname = 'wow-mlb-forward-shadow-auto-capture'
  ) then
    perform cron.schedule(
      'wow-mlb-forward-shadow-auto-capture',
      '2,17,32,47 * * * *',
      $job$select public.wow_mlb_forward_auto_capture_pregame();$job$
    );
  end if;

  if not exists (
    select 1
    from cron.job
    where jobname = 'wow-mlb-forward-shadow-auto-hydrate'
  ) then
    perform cron.schedule(
      'wow-mlb-forward-shadow-auto-hydrate',
      '8,23,38,53 * * * *',
      $job$select public.wow_mlb_forward_auto_hydrate_pregame();$job$
    );
  end if;
end;
$$;
