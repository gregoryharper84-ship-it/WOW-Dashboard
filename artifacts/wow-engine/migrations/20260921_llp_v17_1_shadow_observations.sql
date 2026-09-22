-- WOW V17.1 LLP sharpness challenger prospective shadow observations.
-- Shadow-only research persistence. Production ranking/calibration are unchanged.

create table if not exists public.wow_llp_v17_1_shadow_observations (
    observation_id text primary key,
    shadow_schema_version text not null,
    prediction_id text not null,
    candidate_id text not null,
    official_event_id text not null,
    sport text not null,
    league text,
    selection text not null,
    opponent_or_field text,
    scheduled_start_utc timestamptz,
    requested_slate_date date,
    research_run_id text,
    scan_stage text,
    market_role text,
    controlling_specialist text,
    model_artifact_id text,
    model_timestamp timestamptz not null,
    observed_at timestamptz not null default now(),
    calibrated_probability numeric not null check (calibrated_probability > 0 and calibrated_probability < 1),
    calibrated_lower_bound numeric not null check (calibrated_lower_bound >= 0 and calibrated_lower_bound < 1),
    calibrated_upper_bound numeric check (calibrated_upper_bound > 0 and calibrated_upper_bound <= 1),
    lower_bound_width numeric not null check (lower_bound_width >= 0),
    point_rank_score numeric not null check (point_rank_score > 0 and point_rank_score < 1),
    lower_bound_rank_score numeric not null check (lower_bound_rank_score >= 0 and lower_bound_rank_score < 1),
    uncertainty_adjusted_score numeric not null check (uncertainty_adjusted_score >= 0 and uncertainty_adjusted_score <= 1),
    lambda_penalty numeric not null check (lambda_penalty >= 0 and lambda_penalty <= 1),
    governance_class text not null check (governance_class in ('CLEAR','SOFT_UNCERTAINTY','HARD_BLOCK')),
    hard_blockers text[] not null default '{}',
    soft_uncertainties text[] not null default '{}',
    rank_eligible_shadow boolean not null,
    market_no_vig_probability numeric check (market_no_vig_probability > 0 and market_no_vig_probability < 1),
    market_divergence numeric,
    market_divergence_status text,
    market_prior_weight numeric not null default 0 check (market_prior_weight = 0),
    probability_mutated boolean not null default false check (probability_mutated = false),
    production_policy_mutated boolean not null default false check (production_policy_mutated = false),
    can_execute boolean not null default false check (can_execute = false),
    terminal_authority text not null default 'V17_TERMINAL_REDUCER' check (terminal_authority = 'V17_TERMINAL_REDUCER'),
    created_at timestamptz not null default now(),
    unique (prediction_id, selection, lambda_penalty, shadow_schema_version),
    check (calibrated_lower_bound <= calibrated_probability),
    check (calibrated_upper_bound is null or calibrated_probability <= calibrated_upper_bound),
    check (lower_bound_width = calibrated_probability - calibrated_lower_bound),
    check (point_rank_score = calibrated_probability),
    check (lower_bound_rank_score = calibrated_lower_bound),
    check (uncertainty_adjusted_score >= calibrated_lower_bound and uncertainty_adjusted_score <= calibrated_probability)
);

create index if not exists idx_llp_v17_1_shadow_sport_slate
on public.wow_llp_v17_1_shadow_observations(sport, requested_slate_date, lambda_penalty);

create index if not exists idx_llp_v17_1_shadow_event
on public.wow_llp_v17_1_shadow_observations(official_event_id, observed_at);

create index if not exists idx_llp_v17_1_shadow_cohort
on public.wow_llp_v17_1_shadow_observations(sport, league, governance_class, lower_bound_width);

drop trigger if exists trg_wow_llp_v17_1_shadow_observations_immutable
on public.wow_llp_v17_1_shadow_observations;
create trigger trg_wow_llp_v17_1_shadow_observations_immutable
before update or delete on public.wow_llp_v17_1_shadow_observations
for each row execute function public.wow_core_intelligence_block_mutation();

alter table public.wow_llp_v17_1_shadow_observations enable row level security;
revoke all on public.wow_llp_v17_1_shadow_observations from public, anon, authenticated;
grant select, insert on public.wow_llp_v17_1_shadow_observations to service_role;

create table if not exists public.wow_llp_v17_1_shadow_grades (
    grade_id uuid primary key default gen_random_uuid(),
    observation_id text not null references public.wow_llp_v17_1_shadow_observations(observation_id),
    outcome_target smallint not null check (outcome_target in (0,1)),
    settlement_source text,
    settled_at timestamptz not null,
    point_brier numeric not null check (point_brier >= 0),
    point_log_loss numeric not null check (point_log_loss >= 0),
    created_at timestamptz not null default now(),
    can_execute boolean not null default false check (can_execute = false),
    unique (observation_id)
);

create index if not exists idx_llp_v17_1_shadow_grades_settled
on public.wow_llp_v17_1_shadow_grades(settled_at);

drop trigger if exists trg_wow_llp_v17_1_shadow_grades_immutable
on public.wow_llp_v17_1_shadow_grades;
create trigger trg_wow_llp_v17_1_shadow_grades_immutable
before update or delete on public.wow_llp_v17_1_shadow_grades
for each row execute function public.wow_core_intelligence_block_mutation();

alter table public.wow_llp_v17_1_shadow_grades enable row level security;
revoke all on public.wow_llp_v17_1_shadow_grades from public, anon, authenticated;
grant select, insert on public.wow_llp_v17_1_shadow_grades to service_role;
