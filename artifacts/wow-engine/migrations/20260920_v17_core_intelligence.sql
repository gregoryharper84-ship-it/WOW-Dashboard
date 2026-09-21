-- WOW V17 Core Intelligence V1
-- Advisory learning store only. It cannot execute wagers, change frozen predictions,
-- or promote a model. Source ledgers remain authoritative.

create table if not exists public.wow_intelligence_observations (
    observation_id uuid primary key,
    source_prediction_kind text not null check (source_prediction_kind in ('PROP','EVENT')),
    source_prediction_id text not null,
    schema_version text not null,
    sport text,
    league text,
    market_family text,
    stat_type text,
    direction text,
    model_family text,
    model_artifact_version text,
    calibration_version text,
    probability numeric,
    probability_source text,
    calibrated_lower_bound numeric,
    official_result text not null,
    outcome_target smallint check (outcome_target is null or outcome_target in (0,1)),
    residual numeric,
    brier_score numeric,
    log_loss numeric,
    calibration_bucket text,
    actual_value numeric,
    signed_distance_to_threshold numeric,
    close_miss_flag boolean,
    process_classification text,
    diagnostic_tags text[] not null default '{}',
    settlement_source text,
    settlement_timestamp timestamptz,
    authority text not null default 'ADVISORY_ONLY' check (authority = 'ADVISORY_ONLY'),
    can_execute boolean not null default false check (can_execute = false),
    created_at timestamptz not null default now(),
    unique (source_prediction_kind, source_prediction_id, schema_version),
    check (probability is null or (probability > 0 and probability < 1)),
    check (calibrated_lower_bound is null or (calibrated_lower_bound > 0 and calibrated_lower_bound < 1)),
    check (brier_score is null or brier_score >= 0),
    check (log_loss is null or log_loss >= 0)
);

create index if not exists idx_wow_intelligence_observation_cohort
on public.wow_intelligence_observations (
    source_prediction_kind, sport, league, market_family, stat_type,
    model_family, model_artifact_version, calibration_version
);

create index if not exists idx_wow_intelligence_observation_settled
on public.wow_intelligence_observations (settlement_timestamp);

create table if not exists public.wow_intelligence_hypotheses (
    hypothesis_id uuid primary key,
    cohort_key text not null,
    hypothesis_type text not null,
    direction text not null,
    evidence_n integer not null check (evidence_n > 0),
    metric_name text not null,
    metric_value numeric not null,
    threshold numeric not null,
    status text not null default 'OPEN_FOR_REVIEW'
        check (status in ('OPEN_FOR_REVIEW','REJECTED','ACCEPTED_FOR_CHALLENGER')),
    automatic_promotion_allowed boolean not null default false
        check (automatic_promotion_allowed = false),
    authority text not null default 'ADVISORY_ONLY' check (authority = 'ADVISORY_ONLY'),
    can_execute boolean not null default false check (can_execute = false),
    created_at timestamptz not null default now()
);

create table if not exists public.wow_intelligence_challenger_evaluations (
    evaluation_id uuid primary key default gen_random_uuid(),
    challenger_id text not null,
    cohort_key text not null,
    holdout_n integer not null check (holdout_n >= 0),
    champion_brier numeric not null,
    challenger_brier numeric not null,
    brier_improvement numeric not null,
    champion_log_loss numeric,
    challenger_log_loss numeric,
    eligible_for_review boolean not null default false,
    review_status text not null
        check (review_status in ('SHADOW_VALIDATING','ELIGIBLE_FOR_GOVERNED_REVIEW')),
    automatic_promotion_allowed boolean not null default false
        check (automatic_promotion_allowed = false),
    authority text not null default 'ADVISORY_ONLY' check (authority = 'ADVISORY_ONLY'),
    can_execute boolean not null default false check (can_execute = false),
    created_at timestamptz not null default now()
);

-- Generic immutability guard: Core Intelligence is an append-only evidence ledger.
create or replace function public.wow_core_intelligence_block_mutation()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'WOW_CORE_INTELLIGENCE_APPEND_ONLY';
end;
$$;

drop trigger if exists trg_wow_intelligence_observations_immutable
on public.wow_intelligence_observations;
create trigger trg_wow_intelligence_observations_immutable
before update or delete on public.wow_intelligence_observations
for each row execute function public.wow_core_intelligence_block_mutation();

drop trigger if exists trg_wow_intelligence_hypotheses_immutable
on public.wow_intelligence_hypotheses;
create trigger trg_wow_intelligence_hypotheses_immutable
before update or delete on public.wow_intelligence_hypotheses
for each row execute function public.wow_core_intelligence_block_mutation();

drop trigger if exists trg_wow_intelligence_challengers_immutable
on public.wow_intelligence_challenger_evaluations;
create trigger trg_wow_intelligence_challengers_immutable
before update or delete on public.wow_intelligence_challenger_evaluations
for each row execute function public.wow_core_intelligence_block_mutation();

-- No public/client mutation authority. Backend service-role may append evidence only;
-- the trigger prevents it from rewriting or deleting historical intelligence rows.
revoke all on public.wow_intelligence_observations from public, anon, authenticated;
revoke all on public.wow_intelligence_hypotheses from public, anon, authenticated;
revoke all on public.wow_intelligence_challenger_evaluations from public, anon, authenticated;

grant select, insert on public.wow_intelligence_observations to service_role;
grant select, insert on public.wow_intelligence_hypotheses to service_role;
grant select, insert on public.wow_intelligence_challenger_evaluations to service_role;

revoke all on function public.wow_core_intelligence_block_mutation() from public, anon, authenticated;
grant execute on function public.wow_core_intelligence_block_mutation() to service_role;
