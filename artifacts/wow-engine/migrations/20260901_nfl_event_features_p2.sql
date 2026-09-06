-- WOW v16 NFL moneyline P2: leakage-safe prior-only feature matrix.
-- P2 is training infrastructure only. It cannot publish probability or execute.

create table if not exists public.wow_nfl_pregame_feature_rows (
    game_id text primary key references public.wow_nfl_training_games(game_id),
    season integer not null,
    week integer not null,
    gameday date not null,
    home_team text not null,
    away_team text not null,
    feature_schema_version text not null,
    feature_cutoff_date date not null,
    max_prior_gameday date,
    feature_order jsonb not null,
    features jsonb not null,
    feature_vector jsonb not null,
    target_outcome text not null,
    home_prior_games integer not null,
    away_prior_games integer not null,
    training_eligible boolean not null,
    exclusion_reasons jsonb not null default '[]'::jsonb,
    source_content_sha256s jsonb not null,
    row_inputs_hash text not null,
    locked_at timestamptz not null default now(),
    probability_publishable boolean not null default false,
    can_execute boolean not null default false,
    constraint wow_nfl_p2_feature_schema check (feature_schema_version='NFL_EVENT_PREGAME_PRIOR_V1'),
    constraint wow_nfl_p2_cutoff_matches_game check (feature_cutoff_date=gameday),
    constraint wow_nfl_p2_prior_only check (max_prior_gameday is null or max_prior_gameday < gameday),
    constraint wow_nfl_p2_feature_order_array check (jsonb_typeof(feature_order)='array'),
    constraint wow_nfl_p2_features_object check (jsonb_typeof(features)='object'),
    constraint wow_nfl_p2_vector_array check (jsonb_typeof(feature_vector)='array'),
    constraint wow_nfl_p2_target check (target_outcome in ('HOME_WIN','AWAY_WIN','TIE')),
    constraint wow_nfl_p2_prior_counts check (home_prior_games >= 0 and away_prior_games >= 0),
    constraint wow_nfl_p2_exclusions_array check (jsonb_typeof(exclusion_reasons)='array'),
    constraint wow_nfl_p2_source_hashes_array check (jsonb_typeof(source_content_sha256s)='array'),
    constraint wow_nfl_p2_input_hash_shape check (row_inputs_hash ~ '^[0-9a-f]{64}$'),
    constraint wow_nfl_p2_never_publish check (probability_publishable=false),
    constraint wow_nfl_p2_never_execute check (can_execute=false)
);

alter table public.wow_nfl_pregame_feature_rows enable row level security;

create index if not exists wow_nfl_p2_training_split
    on public.wow_nfl_pregame_feature_rows(season, gameday, training_eligible, game_id);

create or replace function public.wow_nfl_event_p2_feature_readiness()
returns jsonb
language sql
stable
security invoker
set search_path=public
as $$
with counts as (
    select
        count(*) as feature_rows,
        count(*) filter (where training_eligible) as eligible_feature_rows,
        count(*) filter (where target_outcome='TIE') as tie_rows,
        count(*) filter (where max_prior_gameday is not null and max_prior_gameday >= gameday) as leakage_rows,
        count(distinct season) as seasons,
        min(gameday) as first_gameday,
        max(gameday) as last_gameday
    from public.wow_nfl_pregame_feature_rows
), artifacts as (
    select count(*) as fitted_artifact_count
    from public.wow_nfl_event_fitted_model_artifacts
)
select jsonb_build_object(
    'ok', true,
    'phase', 'P2_PRIOR_FEATURE_MATRIX',
    'feature_schema_version', 'NFL_EVENT_PREGAME_PRIOR_V1',
    'feature_rows', counts.feature_rows,
    'eligible_feature_rows', counts.eligible_feature_rows,
    'tie_rows', counts.tie_rows,
    'leakage_rows', counts.leakage_rows,
    'season_count', counts.seasons,
    'first_gameday', counts.first_gameday,
    'last_gameday', counts.last_gameday,
    'feature_matrix_ready', (counts.eligible_feature_rows > 0 and counts.leakage_rows = 0),
    'fitted_artifact_count', artifacts.fitted_artifact_count,
    'model_status', 'MODEL_UNAVAILABLE',
    'model_probability_publishable', false,
    'probability_publishable', false,
    'can_execute', false
)
from counts cross join artifacts;
$$;

revoke all on function public.wow_nfl_event_p2_feature_readiness() from anon, authenticated;

update public.wow_runtime_capabilities
set evidence=coalesce(evidence,'{}'::jsonb) || jsonb_build_object(
        'p2_feature_schema_ready', true,
        'p2_feature_schema_version', 'NFL_EVENT_PREGAME_PRIOR_V1',
        'p2_feature_matrix_loaded', false,
        'fitted_model_ready', false,
        'probability_publishable', false,
        'can_execute', false
    ),
    capability_status='UNAVAILABLE',
    can_execute=false,
    updated_at=now()
where capability_key='NFL_EVENT_PROBABILITY';
