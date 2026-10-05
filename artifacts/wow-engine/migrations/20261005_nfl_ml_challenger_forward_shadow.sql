-- Research-only NFL moneyline challenger forward-shadow ledger for #1342.
--
-- This table is deliberately separate from wow_nfl_forward_shadow_grades,
-- whose official_event_id uniqueness and FK semantics belong to the production
-- champion. Challenger evidence must not overwrite or masquerade as champion
-- evidence.

create table if not exists public.wow_nfl_ml_challenger_forward_shadow (
    shadow_id uuid primary key default gen_random_uuid(),
    challenger_id text not null,
    official_event_id text not null,
    provider_event_id text,
    event_start_time_utc timestamptz not null,
    prediction_created_at timestamptz not null,
    home_team text not null,
    away_team text not null,
    selected_participant text not null,
    calibrated_probability double precision not null,
    local_wilson_lower double precision not null,
    bootstrap_q10_lower double precision not null,
    composite_lower_bound double precision not null,
    lower_bound_method text not null,
    feature_schema_version text not null,
    model_family text not null,
    calibration_method text not null,
    training_cutoff text not null,
    calibration_season integer not null,
    source_feature_hash text not null,
    research_metadata jsonb not null default '{}'::jsonb,
    lifecycle_state text not null default 'FORWARD_SHADOW',
    outcome integer,
    graded_at timestamptz,
    brier double precision,
    log_loss double precision,
    hit boolean,
    probability_publishable boolean not null default false,
    promotion_authorized boolean not null default false,
    can_execute boolean not null default false,
    terminal_authority text not null default 'V17_TERMINAL_REDUCER',
    created_at timestamptz not null default now(),
    unique(challenger_id, official_event_id),
    constraint wow_nfl_ml_challenger_forward_pregame
      check (prediction_created_at < event_start_time_utc),
    constraint wow_nfl_ml_challenger_forward_probability
      check (
        calibrated_probability >= 0 and calibrated_probability <= 1
        and local_wilson_lower >= 0 and local_wilson_lower <= calibrated_probability
        and bootstrap_q10_lower >= 0 and bootstrap_q10_lower <= calibrated_probability
        and composite_lower_bound >= 0 and composite_lower_bound <= calibrated_probability
      ),
    constraint wow_nfl_ml_challenger_forward_composite
      check (
        composite_lower_bound <= local_wilson_lower
        and composite_lower_bound <= bootstrap_q10_lower
      ),
    constraint wow_nfl_ml_challenger_forward_lifecycle
      check (lifecycle_state in ('FORWARD_SHADOW','GRADED')),
    constraint wow_nfl_ml_challenger_forward_outcome
      check (outcome is null or outcome in (0,1)),
    constraint wow_nfl_ml_challenger_forward_grade_consistency
      check (
        (lifecycle_state='FORWARD_SHADOW' and outcome is null and graded_at is null and brier is null and log_loss is null and hit is null)
        or
        (lifecycle_state='GRADED' and outcome is not null and graded_at is not null and brier is not null and log_loss is not null and hit is not null)
      ),
    constraint wow_nfl_ml_challenger_forward_never_publish
      check (probability_publishable=false),
    constraint wow_nfl_ml_challenger_forward_never_promote
      check (promotion_authorized=false),
    constraint wow_nfl_ml_challenger_forward_never_execute
      check (can_execute=false),
    constraint wow_nfl_ml_challenger_forward_terminal_authority
      check (terminal_authority='V17_TERMINAL_REDUCER')
);

alter table public.wow_nfl_ml_challenger_forward_shadow enable row level security;

-- Research ledger is service-role only. Do not rely on RLS alone: Supabase
-- default table grants include table-level privileges that are outside the
-- intended shadow-write contract.
revoke all privileges on table public.wow_nfl_ml_challenger_forward_shadow
  from public, anon, authenticated;
grant select, insert, update on table public.wow_nfl_ml_challenger_forward_shadow
  to service_role;

create index if not exists wow_nfl_ml_challenger_forward_event_start
  on public.wow_nfl_ml_challenger_forward_shadow(event_start_time_utc);

create index if not exists wow_nfl_ml_challenger_forward_state
  on public.wow_nfl_ml_challenger_forward_shadow(challenger_id,lifecycle_state,event_start_time_utc);
