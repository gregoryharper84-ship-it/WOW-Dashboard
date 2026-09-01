-- WOW v16 Clean Core — NFL moneyline P1 historical data backbone.
--
-- Stores immutable source manifests and normalized historical game/team rows.
-- P1 does NOT create a fitted model, calibrator, model probability, or execution
-- capability. NFL_EVENT_PROBABILITY remains UNAVAILABLE.

create table if not exists public.wow_nfl_source_snapshots (
    snapshot_id uuid primary key default gen_random_uuid(),
    source_family text not null default 'NFLVERSE_PUBLIC_DATA',
    dataset_name text not null,
    season integer,
    source_url text not null,
    resolved_url text not null,
    content_sha256 text not null,
    byte_count bigint not null,
    row_count bigint not null,
    column_names jsonb not null,
    source_etag text,
    source_last_modified text,
    fetched_at timestamptz not null,
    raw_object_uri text,
    source_status text not null,
    probability_publishable boolean not null default false,
    can_execute boolean not null default false,
    created_at timestamptz not null default now(),
    constraint wow_nfl_source_family check (source_family = 'NFLVERSE_PUBLIC_DATA'),
    constraint wow_nfl_source_dataset check (dataset_name in (
        'SCHEDULES','PLAY_BY_PLAY','WEEKLY_ROSTERS','INJURIES'
    )),
    constraint wow_nfl_source_status check (source_status in (
        'CAPTURED','CAPTURED_EMPTY','DATA_UNOBTAINABLE','SCHEMA_CHANGED','REJECTED'
    )),
    constraint wow_nfl_source_sha_shape check (content_sha256 ~ '^[0-9a-f]{64}$'),
    constraint wow_nfl_source_sizes_nonnegative check (byte_count >= 0 and row_count >= 0),
    constraint wow_nfl_source_columns_array check (jsonb_typeof(column_names) = 'array'),
    constraint wow_nfl_source_never_publish check (probability_publishable = false),
    constraint wow_nfl_source_never_execute check (can_execute = false),
    unique(dataset_name, season, content_sha256)
);

alter table public.wow_nfl_source_snapshots enable row level security;

create index if not exists wow_nfl_source_snapshot_lookup
    on public.wow_nfl_source_snapshots(dataset_name, season, fetched_at desc);

create table if not exists public.wow_nfl_training_games (
    game_id text primary key,
    season integer not null,
    game_type text not null,
    week integer not null,
    gameday date not null,
    away_team text not null,
    home_team text not null,
    away_score integer not null,
    home_score integer not null,
    home_win boolean not null,
    tie boolean not null default false,
    roof text,
    surface text,
    temp_f numeric,
    wind_mph numeric,
    schedule_snapshot_id uuid not null references public.wow_nfl_source_snapshots(snapshot_id),
    schedule_content_sha256 text not null,
    row_inputs_hash text not null,
    locked_at timestamptz not null default now(),
    probability_publishable boolean not null default false,
    can_execute boolean not null default false,
    constraint wow_nfl_training_game_score_nonnegative check (home_score >= 0 and away_score >= 0),
    constraint wow_nfl_training_game_sha_shape check (schedule_content_sha256 ~ '^[0-9a-f]{64}$'),
    constraint wow_nfl_training_game_inputs_hash_shape check (row_inputs_hash ~ '^[0-9a-f]{64}$'),
    constraint wow_nfl_training_game_outcome_consistent check (
        (tie = true and home_score = away_score and home_win = false)
        or (tie = false and home_score <> away_score and home_win = (home_score > away_score))
    ),
    constraint wow_nfl_training_game_never_publish check (probability_publishable = false),
    constraint wow_nfl_training_game_never_execute check (can_execute = false)
);

alter table public.wow_nfl_training_games enable row level security;

create index if not exists wow_nfl_training_games_season_week
    on public.wow_nfl_training_games(season, week, gameday, game_id);

create table if not exists public.wow_nfl_game_team_summaries (
    game_id text not null references public.wow_nfl_training_games(game_id),
    team text not null,
    opponent text not null,
    is_home boolean not null,
    offensive_plays integer not null,
    offensive_epa_sum numeric not null,
    offensive_epa_mean numeric,
    defensive_epa_sum numeric not null,
    defensive_epa_mean numeric,
    success_plays integer not null,
    success_rate numeric,
    pass_epa_sum numeric not null,
    rush_epa_sum numeric not null,
    turnovers integer not null,
    sacks_allowed integer not null,
    special_teams_epa_sum numeric not null,
    qb_gsis_ids jsonb not null default '[]'::jsonb,
    pbp_snapshot_id uuid not null references public.wow_nfl_source_snapshots(snapshot_id),
    pbp_content_sha256 text not null,
    row_inputs_hash text not null,
    locked_at timestamptz not null default now(),
    probability_publishable boolean not null default false,
    can_execute boolean not null default false,
    primary key(game_id, team),
    constraint wow_nfl_game_team_counts_nonnegative check (
        offensive_plays >= 0 and success_plays >= 0 and turnovers >= 0 and sacks_allowed >= 0
    ),
    constraint wow_nfl_game_team_success_rate check (success_rate is null or (success_rate >= 0 and success_rate <= 1)),
    constraint wow_nfl_game_team_qb_array check (jsonb_typeof(qb_gsis_ids) = 'array'),
    constraint wow_nfl_game_team_pbp_sha_shape check (pbp_content_sha256 ~ '^[0-9a-f]{64}$'),
    constraint wow_nfl_game_team_inputs_hash_shape check (row_inputs_hash ~ '^[0-9a-f]{64}$'),
    constraint wow_nfl_game_team_never_publish check (probability_publishable = false),
    constraint wow_nfl_game_team_never_execute check (can_execute = false)
);

alter table public.wow_nfl_game_team_summaries enable row level security;

create index if not exists wow_nfl_game_team_history
    on public.wow_nfl_game_team_summaries(team, game_id);

-- Expose non-predictive P1 state without changing NFL model availability.
create or replace function public.wow_nfl_event_p1_data_readiness()
returns jsonb
language sql
stable
security invoker
set search_path = public
as $$
    with counts as (
        select
            count(*) filter (where dataset_name='SCHEDULES' and source_status='CAPTURED') as schedules_snapshots,
            count(*) filter (where dataset_name='PLAY_BY_PLAY' and source_status='CAPTURED') as pbp_snapshots,
            count(*) filter (where dataset_name='WEEKLY_ROSTERS' and source_status='CAPTURED') as roster_snapshots,
            count(*) filter (where dataset_name='INJURIES' and source_status='CAPTURED') as injury_snapshots
        from public.wow_nfl_source_snapshots
    ), game_counts as (
        select count(*) as training_games from public.wow_nfl_training_games
    ), team_counts as (
        select count(*) as team_game_summaries from public.wow_nfl_game_team_summaries
    )
    select jsonb_build_object(
        'ok', true,
        'phase', 'P1_DATA_BACKBONE',
        'source_family', 'NFLVERSE_PUBLIC_DATA',
        'schedules_snapshots', counts.schedules_snapshots,
        'pbp_snapshots', counts.pbp_snapshots,
        'roster_snapshots', counts.roster_snapshots,
        'injury_snapshots', counts.injury_snapshots,
        'training_games', game_counts.training_games,
        'team_game_summaries', team_counts.team_game_summaries,
        'historical_data_ready', (
            counts.schedules_snapshots > 0
            and counts.pbp_snapshots > 0
            and counts.roster_snapshots > 0
            and counts.injury_snapshots > 0
            and game_counts.training_games > 0
            and team_counts.team_game_summaries >= game_counts.training_games * 2
        ),
        'model_status', 'MODEL_UNAVAILABLE',
        'probability_publishable', false,
        'can_execute', false
    )
    from counts cross join game_counts cross join team_counts;
$$;

revoke all on function public.wow_nfl_event_p1_data_readiness()
from anon, authenticated;

-- Preserve the current fail-closed capability while recording P1 schema state.
update public.wow_runtime_capabilities
set evidence = coalesce(evidence, '{}'::jsonb) || jsonb_build_object(
        'p1_data_schema_ready', true,
        'p1_historical_data_loaded', false,
        'fitted_model_ready', false,
        'probability_publishable', false,
        'can_execute', false
    ),
    capability_status = 'UNAVAILABLE',
    can_execute = false,
    updated_at = now()
where capability_key = 'NFL_EVENT_PROBABILITY';
