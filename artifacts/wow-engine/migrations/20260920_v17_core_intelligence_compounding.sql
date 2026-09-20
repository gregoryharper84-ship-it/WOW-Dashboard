-- WOW V17 Core Intelligence: compounding memory expansion.
-- Apply after 20260920_v17_core_intelligence.sql.

create table if not exists public.wow_intelligence_market_observations (
    market_memory_id uuid primary key,
    observation_id uuid not null references public.wow_intelligence_observations(observation_id),
    source_prediction_kind text not null check (source_prediction_kind in ('PROP','EVENT')),
    source_prediction_id text not null,
    sport text,
    market_family text,
    stat_type text,
    specialist_id text,
    model_family text,
    model_artifact_version text,
    model_probability numeric,
    opening_market_probability numeric,
    closing_market_probability numeric,
    model_minus_opening numeric,
    model_minus_closing numeric,
    market_move numeric,
    outcome_target smallint check (outcome_target is null or outcome_target in (0,1)),
    model_brier numeric,
    closing_market_brier numeric,
    brier_advantage_vs_close numeric,
    model_log_loss numeric,
    closing_market_log_loss numeric,
    log_loss_advantage_vs_close numeric,
    model_outperformed_close boolean,
    schema_version text not null,
    authority text not null default 'ADVISORY_ONLY' check (authority='ADVISORY_ONLY'),
    can_execute boolean not null default false check (can_execute=false),
    created_at timestamptz not null default now(),
    unique (observation_id, schema_version),
    check (model_probability is null or (model_probability > 0 and model_probability < 1)),
    check (opening_market_probability is null or (opening_market_probability > 0 and opening_market_probability < 1)),
    check (closing_market_probability is null or (closing_market_probability > 0 and closing_market_probability < 1))
);

create index if not exists idx_wow_intelligence_market_context
on public.wow_intelligence_market_observations
(source_prediction_kind, sport, market_family, stat_type, specialist_id);

create table if not exists public.wow_intelligence_signal_observations (
    signal_memory_id uuid primary key,
    observation_id uuid not null references public.wow_intelligence_observations(observation_id),
    source_prediction_kind text not null check (source_prediction_kind in ('PROP','EVENT')),
    source_prediction_id text not null,
    sport text,
    market_family text,
    stat_type text,
    specialist_id text,
    model_family text,
    signal_namespace text not null,
    signal_name text not null,
    signal_value_text text,
    signal_value_numeric numeric,
    signal_bucket text not null,
    model_probability numeric,
    outcome_target smallint check (outcome_target is null or outcome_target in (0,1)),
    residual numeric,
    brier_score numeric,
    schema_version text not null,
    authority text not null default 'ADVISORY_ONLY' check (authority='ADVISORY_ONLY'),
    can_execute boolean not null default false check (can_execute=false),
    created_at timestamptz not null default now(),
    unique (observation_id, signal_namespace, signal_name, signal_bucket, schema_version)
);

create index if not exists idx_wow_intelligence_signal_context
on public.wow_intelligence_signal_observations
(source_prediction_kind, sport, market_family, stat_type, specialist_id, signal_namespace, signal_name, signal_bucket);

create table if not exists public.wow_intelligence_signal_scorecards (
    snapshot_id uuid primary key,
    context_key text not null,
    signal_namespace text not null,
    signal_name text not null,
    signal_bucket text not null,
    sample_n integer not null check (sample_n >= 0),
    scored_n integer not null check (scored_n >= 0),
    observed_rate numeric,
    mean_model_probability numeric,
    calibration_bias numeric,
    mean_residual numeric,
    mean_brier_score numeric,
    baseline_observed_rate numeric,
    observed_lift_vs_context numeric,
    ready_for_review boolean not null default false,
    min_samples integer not null check (min_samples > 0),
    schema_version text not null,
    authority text not null default 'ADVISORY_ONLY' check (authority='ADVISORY_ONLY'),
    can_execute boolean not null default false check (can_execute=false),
    created_at timestamptz not null default now()
);

create table if not exists public.wow_intelligence_market_scorecards (
    snapshot_id uuid primary key,
    context_key text not null,
    sample_n integer not null check (sample_n >= 0),
    comparable_n integer not null check (comparable_n >= 0),
    mean_model_minus_opening numeric,
    mean_model_minus_closing numeric,
    mean_market_move numeric,
    mean_brier_advantage_vs_close numeric,
    mean_log_loss_advantage_vs_close numeric,
    beat_close_n integer not null check (beat_close_n >= 0),
    beat_close_rate numeric,
    ready_for_review boolean not null default false,
    min_samples integer not null check (min_samples > 0),
    schema_version text not null,
    authority text not null default 'ADVISORY_ONLY' check (authority='ADVISORY_ONLY'),
    can_execute boolean not null default false check (can_execute=false),
    created_at timestamptz not null default now()
);

create table if not exists public.wow_intelligence_specialist_scorecards (
    snapshot_id uuid primary key,
    specialist_key text not null,
    specialist_id text not null,
    source_prediction_kind text not null check (source_prediction_kind in ('PROP','EVENT')),
    sport text,
    market_family text,
    stat_type text,
    sample_n integer not null check (sample_n >= 0),
    scored_n integer not null check (scored_n >= 0),
    wins integer not null check (wins >= 0),
    losses integer not null check (losses >= 0),
    mean_probability numeric,
    observed_rate numeric,
    calibration_bias numeric,
    mean_brier_score numeric,
    mean_log_loss numeric,
    market_comparable_n integer not null check (market_comparable_n >= 0),
    mean_brier_advantage_vs_close numeric,
    beat_close_rate numeric,
    model_artifact_versions text[] not null default '{}',
    ready_for_review boolean not null default false,
    min_samples integer not null check (min_samples > 0),
    schema_version text not null,
    authority text not null default 'ADVISORY_ONLY' check (authority='ADVISORY_ONLY'),
    can_execute boolean not null default false check (can_execute=false),
    created_at timestamptz not null default now()
);

create index if not exists idx_wow_intelligence_specialist_key
on public.wow_intelligence_specialist_scorecards(specialist_key, created_at desc);

create table if not exists public.wow_intelligence_challenger_proposals (
    proposal_id uuid primary key,
    proposal_type text not null,
    trigger_source text not null,
    trigger_id text not null,
    target_key text not null,
    specialist_id text,
    sport text,
    market_family text,
    stat_type text,
    evidence_n integer not null check (evidence_n >= 0),
    recipe jsonb not null default '{}'::jsonb,
    requested_lifecycle_state text not null default 'CANDIDATE' check (requested_lifecycle_state='CANDIDATE'),
    shadow_required boolean not null default true check (shadow_required=true),
    automatic_certification boolean not null default false check (automatic_certification=false),
    automatic_promotion boolean not null default false check (automatic_promotion=false),
    probability_publishable boolean not null default false check (probability_publishable=false),
    authority text not null default 'ADVISORY_ONLY' check (authority='ADVISORY_ONLY'),
    can_execute boolean not null default false check (can_execute=false),
    created_at timestamptz not null default now()
);

create index if not exists idx_wow_intelligence_challenger_target
on public.wow_intelligence_challenger_proposals(target_key, created_at desc);

create table if not exists public.wow_intelligence_promotion_reviews (
    review_id uuid primary key,
    challenger_id text not null,
    target_key text not null,
    holdout_n integer not null check (holdout_n >= 0),
    champion_brier numeric,
    challenger_brier numeric,
    brier_improvement numeric,
    champion_log_loss numeric,
    challenger_log_loss numeric,
    log_loss_improvement numeric,
    calibration_not_worse boolean,
    eligible_for_governed_review boolean not null default false,
    status text not null check (status in ('ELIGIBLE_FOR_GOVERNED_REVIEW','SHADOW_OR_REVIEW_BLOCKED')),
    blockers text[] not null default '{}',
    automatic_promotion boolean not null default false check (automatic_promotion=false),
    probability_publishable boolean not null default false check (probability_publishable=false),
    authority text not null default 'ADVISORY_ONLY' check (authority='ADVISORY_ONLY'),
    can_execute boolean not null default false check (can_execute=false),
    created_at timestamptz not null default now()
);

-- Every intelligence table is evidence history, never mutable state.
do $$
declare
  tbl text;
  trigger_name text;
begin
  foreach tbl in array array[
    'wow_intelligence_market_observations',
    'wow_intelligence_signal_observations',
    'wow_intelligence_signal_scorecards',
    'wow_intelligence_market_scorecards',
    'wow_intelligence_specialist_scorecards',
    'wow_intelligence_challenger_proposals',
    'wow_intelligence_promotion_reviews'
  ] loop
    trigger_name := 'trg_' || tbl || '_immutable';
    execute format('drop trigger if exists %I on public.%I', trigger_name, tbl);
    execute format(
      'create trigger %I before update or delete on public.%I for each row execute function public.wow_core_intelligence_block_mutation()',
      trigger_name, tbl
    );
    execute format('revoke all on public.%I from public, anon, authenticated', tbl);
    execute format('grant select, insert on public.%I to service_role', tbl);
  end loop;
end;
$$;
