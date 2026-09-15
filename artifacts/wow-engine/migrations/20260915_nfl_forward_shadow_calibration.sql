-- V17 NFL prospective forward-shadow grading and calibration-health ledger.
-- Evidence-only: never certifies/promotes a model and never enables execution.

create table if not exists public.wow_nfl_forward_shadow_grades (
  grade_id uuid primary key default gen_random_uuid(),
  schema_version text not null,
  event_prediction_id uuid not null references public.wow_nfl_event_predictions(event_prediction_id),
  official_event_id text not null unique,
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
  constraint wow_nfl_forward_grade_pregame
    check (prediction_created_at < event_start_time_utc),
  constraint wow_nfl_forward_grade_probability
    check (
      calibrated_lower_bound > 0 and calibrated_upper_bound < 1
      and calibrated_lower_bound <= calibrated_probability
      and calibrated_probability <= calibrated_upper_bound
    ),
  constraint wow_nfl_forward_grade_metrics
    check (brier >= 0 and log_loss >= 0)
);

create index if not exists wow_nfl_forward_shadow_grades_model_version
  on public.wow_nfl_forward_shadow_grades(model_version, event_start_time_utc);

alter table public.wow_nfl_forward_shadow_grades enable row level security;

create table if not exists public.wow_nfl_forward_calibration_health (
  health_id uuid primary key default gen_random_uuid(),
  schema_version text not null,
  generated_at timestamptz not null,
  graded_n integer not null check (graded_n >= 0),
  minimum_forward_required integer not null check (minimum_forward_required > 0),
  brier double precision,
  log_loss double precision,
  ece double precision,
  calibration_bias double precision,
  mean_predicted_probability double precision,
  observed_hit_rate double precision,
  mean_calibrated_lower_bound double precision,
  lower_bound_reliability_gap double precision,
  status text not null check (status in ('PASS','FAIL','INSUFFICIENT_FORWARD_EVIDENCE')),
  certification_recommendation text not null check (
    certification_recommendation in ('FORWARD_EVIDENCE_PASS','DO_NOT_CERTIFY_YET')
  ),
  blockers jsonb not null default '[]'::jsonb check (jsonb_typeof(blockers) = 'array'),
  can_execute boolean not null default false check (can_execute = false),
  created_at timestamptz not null default now()
);

create index if not exists wow_nfl_forward_calibration_health_generated
  on public.wow_nfl_forward_calibration_health(generated_at desc);

alter table public.wow_nfl_forward_calibration_health enable row level security;

create or replace function public.wow_v17_reject_nfl_forward_grade_mutation()
returns trigger
language plpgsql
set search_path to ''
as $function$
begin
  raise exception 'WOW_NFL_FORWARD_SHADOW_GRADE_IMMUTABLE';
end;
$function$;

drop trigger if exists wow_nfl_forward_shadow_grades_immutable
  on public.wow_nfl_forward_shadow_grades;
create trigger wow_nfl_forward_shadow_grades_immutable
before update or delete on public.wow_nfl_forward_shadow_grades
for each row execute function public.wow_v17_reject_nfl_forward_grade_mutation();
