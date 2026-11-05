-- WOW V17 approved-CFBD-only player outcome research refresh.
-- Execute inside database maintenance as postgres, never from public client,
-- browser, or GitHub Actions with a service-role secret.
-- Historical outcomes only; no book, line, prediction or execution authority.
-- Source prerequisite: private normalize_ncaaf_player_week from PR #2273.

create or replace function wow_prop_history.refresh_next_ncaaf_outcome_week()
returns jsonb language plpgsql security invoker set search_path = ''
as $$
declare
  next_week record;
  outcome_n integer;
begin
  select s.season, s.week, s.source_snapshot_id into next_week
  from public.wow_ncaaf_source_snapshots s
  where s.provider='CFBD'
    and s.endpoint='/games/players'
    and s.acquisition_status='AVAILABLE'
    and s.can_execute=false
    and not exists (
      select 1 from wow_prop_history.ncaaf_player_game_outcomes o
      where o.source_snapshot_id=s.source_snapshot_id
    )
  order by s.season desc, s.week desc, s.retrieved_at desc
  limit 1;
  if not found then
    return pg_catalog.jsonb_build_object(
      'status','NO_PENDING_PLAYER_WEEKS',
      'new_outcome_rows',0,
      'probability_publishable',false,
      'model_training_eligible',false,
      'can_execute',false
    );
  end if;
  -- Strict per-week transaction and source/event/stat checks are owned by
  -- the existing private normalizer; any hold aborts this cron invocation.
  outcome_n := wow_prop_history.normalize_ncaaf_player_week(
    next_week.season,next_week.week
  );
  return pg_catalog.jsonb_build_object(
    'status','RESEARCH_OUTCOME_WEEK_UPDATED',
    'season',next_week.season,'week',next_week.week,
    'new_outcome_rows',outcome_n,
    'probability_publishable',false,
    'model_training_eligible',false,
    'can_execute',false
  );
end $$;

revoke all on function wow_prop_history.refresh_next_ncaaf_outcome_week()
from public, anon, authenticated;
grant execute on function wow_prop_history.refresh_next_ncaaf_outcome_week()
to service_role;

-- 14:45 UTC daily, after existing GitHub NCAAF source-maintenance
-- cron schedule 13:35 UTC. A repeated scheduler registration updates
-- the named job rather than creating a second differently named job.
select cron.schedule(
  'wow-v17-ncaaf-research-outcomes-daily',
  '45 14 * * *',
  'select wow_prop_history.refresh_next_ncaaf_outcome_week();'
);
