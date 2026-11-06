-- P0 NFL historical forward-grade safety: quarantine invalid immutable grades
-- by omission from the *only* cohort-counting view. Never mutate grade rows,
-- immutable predictions, settled outcomes, or calibration model artifacts.
-- The view is a fresh join: a later changed settlement invalidates its old
-- binary grade immediately, even if a historical health receipt said PASS.
create or replace view public.wow_nfl_forward_verified_grades
with (security_invoker = true, security_barrier = true)
as
select g.official_event_id, g.model_version
from public.wow_nfl_forward_shadow_grades as g
join public.wow_nfl_event_predictions as p
  on p.event_prediction_id = g.event_prediction_id
join public.wow_nfl_training_games as o
  on o.game_id = g.official_event_id
where g.schema_version = 'NFL_FORWARD_SHADOW_GRADE_V1'
  and g.official_event_id = p.official_event_id
  and g.source_game_id = o.game_id
  and g.model_version = p.model_version
  and g.selected_participant = p.selected_participant
  and p.selected_participant in (p.home_team, p.away_team)
  and g.prediction_created_at = p.created_at
  and g.event_start_time_utc = p.event_start_time_utc
  and p.created_at < p.event_start_time_utc
  and p.model_package_valid is true
  and p.provenance_complete is true
  and p.model_probability_publishable is true
  and p.can_execute is false
  and g.can_execute is false
  and o.can_execute is false
  and o.tie is false
  and o.home_score >= 0
  and o.away_score >= 0
  and o.home_score <> o.away_score
  and o.home_win = (o.home_score > o.away_score)
  and g.outcome = case
    when p.selected_participant = p.home_team
      then case when o.home_win then 1 else 0 end
    else case when o.home_win then 0 else 1 end
  end
  and abs(g.calibrated_probability - p.calibrated_selection_probability::double precision) <= 0.0000000001
  and abs(g.calibrated_lower_bound - p.calibrated_selection_lower_bound::double precision) <= 0.0000000001
  and abs(g.calibrated_upper_bound - p.calibrated_selection_upper_bound::double precision) <= 0.0000000001
  and abs(g.brier - power(g.calibrated_probability - g.outcome, 2.0::double precision)) <= 0.000000001
  and abs(g.log_loss - case
    when g.outcome = 1 then -ln(g.calibrated_probability)
    else -ln(1.0 - g.calibrated_probability)
  end) <= 0.000000001;

-- RLS on all three source tables stays effective for invoker callers.
-- Keep raw and reconciled calibration evidence private; service-role only.
revoke all on public.wow_nfl_forward_verified_grades from public, anon, authenticated;
grant select on public.wow_nfl_forward_verified_grades to service_role;
