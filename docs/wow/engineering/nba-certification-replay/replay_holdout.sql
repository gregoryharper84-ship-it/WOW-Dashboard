-- NBA team/event specialist: independent read-only holdout replay (Class C review evidence).
-- Re-scores the untouched holdout from the STORED artifact + calibrator + stored pregame
-- features. It does not use the training pipeline's own output. Evidence only:
-- never a promotion, publication grant or execution authority. can_execute stays false.
-- Artifact:   NBA_TEAM_EVENT_LOGISTIC_V1_20261110T202644Z  (checksum b842355e83b7...51ac92f)
-- Calibrator: NBA_TEAM_EVENT_PLATT_V1_20261110T202644Z      (PLATT_TIME_SPLIT_V1)
-- Holdout:    game_date > train_end_date (2025-03-16); calibrator fit uses dates <= that cutoff.
with art as (
  select artifact_payload p from wow_basketball_team_event_model_artifacts
  where model_artifact_version = 'NBA_TEAM_EVENT_LOGISTIC_V1_20261110T202644Z'),
cal as (
  select (coefficients->>'a')::float a, (coefficients->>'b')::float b
  from wow_basketball_team_event_calibrators
  where calibrator_version = 'NBA_TEAM_EVENT_PLATT_V1_20261110T202644Z'),
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
       from sc, tr where game_date > '2025-03-16')
select count(*) n, min(game_date) start_date, max(game_date) end_date,
       avg(power(raw-y,2)) raw_brier, avg(power(cp-y,2)) cal_brier, avg(power(hr-y,2)) baseline_brier,
       -avg(y*ln(raw)+(1-y)*ln(1-raw)) raw_log_loss,
       -avg(y*ln(cp)+(1-y)*ln(1-cp)) cal_log_loss,
       -avg(y*ln(hr)+(1-y)*ln(1-hr)) baseline_log_loss,
       avg(((cp>0.5)::int = y)::int) accuracy, avg(raw-y) raw_bias, avg(cp-y) cal_bias,
       count(*) filter (where as_of::date > game_date) feature_asof_after_game_day
from ho;
