-- V17 NFL forward grading V2: grading eligibility is separate from publication.
-- Owner decision 2026-11-21 (#1896 comment 7068913166, #1939 comment 7068913721).
--
-- The V1 ledger (wow_nfl_forward_shadow_grades) and V1 view
-- (wow_nfl_forward_verified_grades) are NOT modified: they remain the original,
-- immutable historical record and the only certification-admission source.
-- V2 grades the latest VALID pre-kickoff forecast per event whether or not it
-- is publishable. This migration never mutates predictions, outcomes, grades,
-- model artifacts or publication flags, and never enables execution.

create table if not exists public.wow_nfl_forward_shadow_grades_v2 (
  grade_id uuid primary key default gen_random_uuid(),
  schema_version text not null
    check (schema_version = 'NFL_FORWARD_SHADOW_GRADE_V2'),
  selection_policy text not null
    check (selection_policy = 'LATEST_VALID_PREKICKOFF_V2'),
  event_prediction_id uuid not null unique
    references public.wow_nfl_event_predictions(event_prediction_id),
  official_event_id text not null,
  event_start_time_utc timestamptz not null,
  prediction_created_at timestamptz not null,
  model_version text not null,
  selected_participant text not null,
  calibrated_probability double precision not null,
  calibrated_lower_bound double precision not null,
  calibrated_upper_bound double precision not null,
  outcome integer not null check (outcome in (0,1)),
  brier double precision not null,
  log_loss double precision not null,
  source_game_id text not null,
  can_execute boolean not null default false check (can_execute = false),
  created_at timestamptz not null default now(),
  -- One graded event per policy: the 100-game threshold counts distinct games.
  constraint wow_nfl_forward_grade_v2_one_per_event
    unique (selection_policy, official_event_id),
  constraint wow_nfl_forward_grade_v2_pregame
    check (prediction_created_at < event_start_time_utc),
  constraint wow_nfl_forward_grade_v2_probability
    check (
      calibrated_lower_bound > 0 and calibrated_upper_bound < 1
      and calibrated_lower_bound <= calibrated_probability
      and calibrated_probability <= calibrated_upper_bound
    ),
  constraint wow_nfl_forward_grade_v2_metrics
    check (brier >= 0 and log_loss >= 0)
);

create index if not exists wow_nfl_forward_shadow_grades_v2_model_version
  on public.wow_nfl_forward_shadow_grades_v2(model_version, event_start_time_utc);

alter table public.wow_nfl_forward_shadow_grades_v2 enable row level security;
revoke all on public.wow_nfl_forward_shadow_grades_v2 from public, anon, authenticated;
grant select, insert on public.wow_nfl_forward_shadow_grades_v2 to service_role;

-- Append-only: reuse the existing V1 immutability function.
drop trigger if exists wow_nfl_forward_shadow_grades_v2_immutable
  on public.wow_nfl_forward_shadow_grades_v2;
create trigger wow_nfl_forward_shadow_grades_v2_immutable
before update or delete on public.wow_nfl_forward_shadow_grades_v2
for each row execute function public.wow_v17_reject_nfl_forward_grade_mutation();

drop trigger if exists wow_nfl_forward_shadow_grades_v2_no_truncate
  on public.wow_nfl_forward_shadow_grades_v2;
create trigger wow_nfl_forward_shadow_grades_v2_no_truncate
before truncate on public.wow_nfl_forward_shadow_grades_v2
for each statement execute function public.wow_v17_reject_nfl_forward_grade_mutation();

-- Fresh-join reconciliation, as V1, minus the publication flag, plus a check
-- that the graded row IS the latest valid pre-kickoff row (stale rows drop out).
create or replace view public.wow_nfl_forward_verified_grades_v2
with (security_invoker = true, security_barrier = true)
as
select g.official_event_id, g.model_version,
       p.model_artifact_id, g.event_prediction_id, g.event_start_time_utc,
       g.prediction_created_at, g.selected_participant, g.calibrated_probability,
       g.calibrated_lower_bound, g.calibrated_upper_bound, g.outcome,
       g.brier, g.log_loss, g.source_game_id, g.schema_version, g.can_execute,
       g.selection_policy,
       p.model_output_snapshot->>'artifact_checksum' as artifact_checksum
from public.wow_nfl_forward_shadow_grades_v2 as g
join public.wow_nfl_event_predictions as p
  on p.event_prediction_id = g.event_prediction_id
join public.wow_nfl_training_games as o
  on o.game_id = g.official_event_id
where g.schema_version = 'NFL_FORWARD_SHADOW_GRADE_V2'
  and g.selection_policy = 'LATEST_VALID_PREKICKOFF_V2'
  and g.official_event_id = p.official_event_id
  and g.source_game_id = o.game_id
  and g.model_version = p.model_version
  and g.selected_participant = p.selected_participant
  and p.selected_participant in (p.home_team, p.away_team)
  and g.prediction_created_at = p.created_at
  and g.event_start_time_utc = p.event_start_time_utc
  and p.created_at < p.event_start_time_utc
  and p.created_at < (
    select min(k.event_start_time_utc)
    from public.wow_nfl_event_predictions as k
    where k.official_event_id = p.official_event_id
  )
  and p.model_package_valid is true
  and p.provenance_complete is true
  and p.can_execute is false
  and g.can_execute is false
  and o.can_execute is false
  and not exists (
    select 1
    from public.wow_nfl_event_predictions as q
    where q.official_event_id = p.official_event_id
      and q.event_prediction_id <> p.event_prediction_id
      and q.model_package_valid is true
      and q.provenance_complete is true
      and q.can_execute is false
      and q.created_at < (
        select min(k2.event_start_time_utc)
        from public.wow_nfl_event_predictions as k2
        where k2.official_event_id = p.official_event_id
      )
      and (
        q.created_at > p.created_at
        or (q.created_at = p.created_at and q.event_prediction_id > p.event_prediction_id)
      )
  )
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
    when g.outcome = 1 then -ln(greatest(least(g.calibrated_probability, 1.0 - 0.000000000001), 0.000000000001))
    else -ln(1.0 - greatest(least(g.calibrated_probability, 1.0 - 0.000000000001), 0.000000000001))
  end) <= 0.000000001;

revoke all on public.wow_nfl_forward_verified_grades_v2 from public, anon, authenticated;
grant select on public.wow_nfl_forward_verified_grades_v2 to service_role;
