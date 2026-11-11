-- NBA team/event specialist: independent read-only holdout replay (Class C review evidence).
-- Re-scores the untouched holdout from the STORED artifact + calibrator + stored pregame
-- features. It does not use the training pipeline's own output. Evidence only:
-- never a promotion, publication grant or execution authority. can_execute stays false.
-- Artifact:   NBA_TEAM_EVENT_LOGISTIC_V1_20261110T202644Z  (checksum b842355e83b7...51ac92f)
-- Calibrator: NBA_TEAM_EVENT_PLATT_V1_20261110T202644Z      (PLATT_TIME_SPLIT_V1)
-- Evidence emitted: summary, ECE, per-bin counts, per-season scores, baseline accuracy, as-of timing.
-- Holdout:    game_date > train_end_date (2025-03-16); calibrator fit uses dates <= that cutoff.
with art as (
  select artifact_payload p, artifact_checksum
  from wow_basketball_team_event_model_artifacts
  where sport = 'NBA'
    and model_artifact_version = 'NBA_TEAM_EVENT_LOGISTIC_V1_20261110T202644Z'
    and artifact_checksum = 'b842355e83b7b6d6693dd6bfd402881a86f7af10f2cb7eb28090e947c51ac92f'),
cal as (
  select (coefficients->>'a')::float a, (coefficients->>'b')::float b
  from wow_basketball_team_event_calibrators
  where sport = 'NBA'
    and calibrator_version = 'NBA_TEAM_EVENT_PLATT_V1_20261110T202644Z'
    and model_artifact_version = 'NBA_TEAM_EVENT_LOGISTIC_V1_20261110T202644Z'),
base as (
  select g.game_id, g.game_date, g.season, g.home_win::int y, f.as_of,
         array[f.home_win_rate_prior, f.away_win_rate_prior,
               f.home_point_diff_prior, f.away_point_diff_prior,
               least(f.home_rest_days, 7)::float, least(f.away_rest_days, 7)::float,
               f.home_back_to_back::int::float, f.away_back_to_back::int::float] x
  from wow_nba_training_games g
  join wow_nba_pregame_feature_rows f on f.game_id = g.game_id
  where g.settled and g.home_win is not null),
z as (
  select b.*, (art.p->>'intercept')::float + (
           select sum(((b.x[i] - (art.p->'means'->>(i-1))::float)
                       / (art.p->'scales'->>(i-1))::float)
                      * (art.p->'coefficients'->>(i-1))::float)
           from generate_series(1, 8) i) zz
  from base b, art),
sc as (select z.*, 1/(1+exp(-zz)) raw, 1/(1+exp(-(cal.a + cal.b*zz))) cp from z, cal),
tr as (select avg(y) hr from sc where game_date <= '2025-03-16'),
ho as (select sc.*, tr.hr, least(floor(cp*10), 9)::int bin
       from sc, tr where game_date > '2025-03-16'),
-- Every row is: section, key, n, then up to four numeric values (v1..v4; meaning per section below).
summary as (
  -- v1=raw_brier v2=cal_brier v3=baseline_brier v4=accuracy
  select 'summary' sec, min(game_date)::text||'..'||max(game_date)::text k, count(*) n,
         avg(power(raw-y,2)) v1, avg(power(cp-y,2)) v2, avg(power(hr-y,2)) v3,
         avg(((cp>0.5)::int = y)::int) v4 from ho),
loss as (
  -- v1=raw_log_loss v2=cal_log_loss v3=baseline_log_loss v4=baseline_accuracy (train home-win rate > .5 => always home)
  select 'loss', 'full_precision', count(*),
         -avg(y*ln(raw)+(1-y)*ln(1-raw)), -avg(y*ln(cp)+(1-y)*ln(1-cp)),
         -avg(y*ln(hr)+(1-y)*ln(1-hr)), avg(((hr>0.5)::int = y)::int) from ho),
bias as (
  -- v1=raw_bias v2=cal_bias v3=raw_accuracy (the artifact's persisted holdout_accuracy is the RAW model's)
  select 'bias', 'holdout', count(*), avg(raw-y), avg(cp-y), avg(((raw>0.5)::int = y)::int), null::float from ho),
bins as (
  -- v1=mean predicted v2=observed rate v3=abs gap
  select 'bin', 'cal_p_'||(bin/10.0)::numeric(2,1)::text, count(*), avg(cp), avg(y), abs(avg(cp)-avg(y)), null::float
  from ho group by bin),
ece as (
  select 'ece', 'cal_10bin', sum(n), sum(n*gap)/sum(n), null::float, null::float, null::float
  from (select count(*) n, abs(avg(cp)-avg(y)) gap from ho group by bin) b),
seasons as (
  -- v1=cal_brier v2=baseline_brier v3=cal_bias
  select 'season', season::text, count(*), avg(power(cp-y,2)), avg(power(hr-y,2)), avg(cp-y), null::float
  from ho group by season),
timing as (
  -- Date-only look-ahead checks. wow_nba_training_games has NO tip-off timestamp, so this CANNOT
  -- prove features were frozen before tip-off. v1=count(as_of calendar date > game_date),
  -- v2=count(as_of >= game_date 00:00 UTC). Observed: as_of == game_date 00:00 UTC for every row, i.e. a
  -- date-granularity stamp, NOT an observed snapshot time. Pregame safety rests on the feature builder using
  -- strictly earlier dates (tested), not on this column.
  select 'timing_date_only', 'as_of_vs_game_date', count(*),
         count(*) filter (where as_of::date > game_date)::float,
         count(*) filter (where as_of >= game_date::timestamptz)::float, null::float, null::float from ho)
select * from summary union all select * from loss union all select * from bias
union all select * from bins union all select * from ece union all select * from seasons
union all select * from timing
order by 1, 2;
