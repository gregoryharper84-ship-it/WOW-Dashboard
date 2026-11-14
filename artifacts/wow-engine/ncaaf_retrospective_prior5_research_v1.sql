-- WOW V17 NCAAF retrospective prior-game descriptive cohort (NOT PIT VERIFIED).
-- Backfilled CFBD boxscores were first observed in this system Oct 29, 2026.
-- Never represent a historical outcome or rolling statistic as a timestamp-
-- verified pregame input simply because its game date precedes the target.
-- This view cannot authorize model fitting, calibration or publication.
create or replace view wow_prop_history.ncaaf_retrospective_prior5_research_v1
with (security_invoker=true, security_barrier=true)
as
with windowed as (
  select
    o.season,
    o.official_event_id,
    o.official_player_id,
    o.team,
    o.opponent,
    o.stat_type,
    o.event_start_time,
    o.observed_at as target_source_first_observed_at,
    o.actual_value as settled_target_value_research_only,
    count(*) over prior5 as prior_game_stat_count,
    avg(o.actual_value) over prior5 as prior_five_results_mean_reconstructed,
    max(o.event_start_time) over prior5 as latest_prior_game_start,
    max(o.observed_at) over prior5 as latest_prior_result_observed_at
  from wow_prop_history.ncaaf_player_game_outcomes o
  where o.source_provider='CFBD'
    and o.source_status='NORMALIZED_RESEARCH_ONLY'
    and o.can_execute=false
  window prior5 as (
    partition by o.season,o.official_player_id,o.stat_type
    order by o.event_start_time,o.official_event_id
    rows between 5 preceding and 1 preceding
  )
)
select
  w.season,
  case w.season
    when 2023 then 'RESEARCH_HISTORICAL_2023'
    when 2024 then 'RESEARCH_HISTORICAL_2024'
    when 2025 then 'RESEARCH_HISTORICAL_2025'
    when 2026 then 'RESEARCH_CURRENT_2026'
    else 'RESEARCH_OTHER'
  end as descriptive_cohort,
  w.official_event_id,
  w.official_player_id,
  w.team,
  w.opponent,
  w.stat_type,
  w.event_start_time,
  w.target_source_first_observed_at,
  w.settled_target_value_research_only,
  w.prior_game_stat_count,
  w.prior_five_results_mean_reconstructed,
  w.latest_prior_game_start,
  w.latest_prior_result_observed_at,
  (w.latest_prior_game_start < w.event_start_time) as prior_game_chronology_valid,
  (w.latest_prior_result_observed_at < w.event_start_time) as prior_source_was_observed_pregame,
  false::boolean as point_in_time_verified,
  null::timestamptz as verified_feature_as_of,
  false::boolean as model_training_eligible,
  false::boolean as probability_publishable,
  false::boolean as can_execute
from windowed w
where w.prior_game_stat_count >= 3
  and w.latest_prior_game_start < w.event_start_time;

revoke all on wow_prop_history.ncaaf_retrospective_prior5_research_v1
  from public, anon, authenticated, service_role;
grant select on wow_prop_history.ncaaf_retrospective_prior5_research_v1
  to service_role;

comment on view wow_prop_history.ncaaf_retrospective_prior5_research_v1 is
  'Retrospective descriptive lagged player form from 2026 backfilled CFBD outcomes. Always PIT_UNVERIFIED. Never claim pregame feature provenance, model training eligibility, betting market price or governed probability.';
