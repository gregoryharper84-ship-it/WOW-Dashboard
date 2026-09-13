-- WOW V17 — independent NBA/WNBA full-game outright-winner specialist backbone.
--
-- This migration creates storage, immutable historical-data contracts, governed
-- artifact registries, specialist routing, and readiness functions. It seeds NO
-- fitted model and promotes NO capability. NBA/WNBA remain MODEL_UNAVAILABLE
-- until a real sport-specific artifact + calibrator + certification are present.

-- ---------------------------------------------------------------------------
-- Shared helper: create the two sport-specific source/training/feature backbones.
-- Tables remain separate to make cross-sport leakage structurally obvious.
-- ---------------------------------------------------------------------------

create table if not exists public.wow_nba_source_snapshots (
    snapshot_id uuid primary key default gen_random_uuid(),
    source_family text not null default 'ESPN_PUBLIC_BASKETBALL',
    dataset_name text not null default 'SCOREBOARD_RESULTS',
    season integer not null,
    range_start date not null,
    range_end date not null,
    source_url text not null,
    content_sha256 text not null,
    event_count integer not null,
    raw_payload jsonb not null,
    fetched_at timestamptz not null default now(),
    source_status text not null,
    probability_publishable boolean not null default false,
    can_execute boolean not null default false,
    constraint wow_nba_source_family check (source_family='ESPN_PUBLIC_BASKETBALL'),
    constraint wow_nba_source_dataset check (dataset_name='SCOREBOARD_RESULTS'),
    constraint wow_nba_source_sha check (content_sha256 ~ '^[0-9a-f]{64}$'),
    constraint wow_nba_source_count check (event_count >= 0),
    constraint wow_nba_source_payload check (jsonb_typeof(raw_payload)='object'),
    constraint wow_nba_source_status check (source_status in ('CAPTURED','CAPTURED_EMPTY','DATA_UNOBTAINABLE','SCHEMA_CHANGED','REJECTED')),
    constraint wow_nba_source_never_publish check (probability_publishable=false),
    constraint wow_nba_source_never_execute check (can_execute=false),
    unique(season, range_start, range_end, content_sha256)
);
alter table public.wow_nba_source_snapshots enable row level security;

create table if not exists public.wow_wnba_source_snapshots (
    snapshot_id uuid primary key default gen_random_uuid(),
    source_family text not null default 'ESPN_PUBLIC_BASKETBALL',
    dataset_name text not null default 'SCOREBOARD_RESULTS',
    season integer not null,
    range_start date not null,
    range_end date not null,
    source_url text not null,
    content_sha256 text not null,
    event_count integer not null,
    raw_payload jsonb not null,
    fetched_at timestamptz not null default now(),
    source_status text not null,
    probability_publishable boolean not null default false,
    can_execute boolean not null default false,
    constraint wow_wnba_source_family check (source_family='ESPN_PUBLIC_BASKETBALL'),
    constraint wow_wnba_source_dataset check (dataset_name='SCOREBOARD_RESULTS'),
    constraint wow_wnba_source_sha check (content_sha256 ~ '^[0-9a-f]{64}$'),
    constraint wow_wnba_source_count check (event_count >= 0),
    constraint wow_wnba_source_payload check (jsonb_typeof(raw_payload)='object'),
    constraint wow_wnba_source_status check (source_status in ('CAPTURED','CAPTURED_EMPTY','DATA_UNOBTAINABLE','SCHEMA_CHANGED','REJECTED')),
    constraint wow_wnba_source_never_publish check (probability_publishable=false),
    constraint wow_wnba_source_never_execute check (can_execute=false),
    unique(season, range_start, range_end, content_sha256)
);
alter table public.wow_wnba_source_snapshots enable row level security;

create table if not exists public.wow_nba_training_games (
    event_id text primary key,
    season integer not null,
    start_time timestamptz not null,
    home_team text not null,
    away_team text not null,
    home_score integer not null,
    away_score integer not null,
    home_win boolean not null,
    source_snapshot_id uuid not null references public.wow_nba_source_snapshots(snapshot_id),
    row_inputs_hash text not null,
    locked_at timestamptz not null default now(),
    probability_publishable boolean not null default false,
    can_execute boolean not null default false,
    constraint wow_nba_training_score check (home_score>=0 and away_score>=0 and home_score<>away_score),
    constraint wow_nba_training_hash check (row_inputs_hash ~ '^[0-9a-f]{64}$'),
    constraint wow_nba_training_outcome check (home_win=(home_score>away_score)),
    constraint wow_nba_training_never_publish check (probability_publishable=false),
    constraint wow_nba_training_never_execute check (can_execute=false)
);
alter table public.wow_nba_training_games enable row level security;
create index if not exists wow_nba_training_chrono on public.wow_nba_training_games(start_time,event_id);

create table if not exists public.wow_wnba_training_games (
    event_id text primary key,
    season integer not null,
    start_time timestamptz not null,
    home_team text not null,
    away_team text not null,
    home_score integer not null,
    away_score integer not null,
    home_win boolean not null,
    source_snapshot_id uuid not null references public.wow_wnba_source_snapshots(snapshot_id),
    row_inputs_hash text not null,
    locked_at timestamptz not null default now(),
    probability_publishable boolean not null default false,
    can_execute boolean not null default false,
    constraint wow_wnba_training_score check (home_score>=0 and away_score>=0 and home_score<>away_score),
    constraint wow_wnba_training_hash check (row_inputs_hash ~ '^[0-9a-f]{64}$'),
    constraint wow_wnba_training_outcome check (home_win=(home_score>away_score)),
    constraint wow_wnba_training_never_publish check (probability_publishable=false),
    constraint wow_wnba_training_never_execute check (can_execute=false)
);
alter table public.wow_wnba_training_games enable row level security;
create index if not exists wow_wnba_training_chrono on public.wow_wnba_training_games(start_time,event_id);

create table if not exists public.wow_nba_pregame_feature_rows (
    event_id text primary key references public.wow_nba_training_games(event_id),
    as_of timestamptz not null,
    home_elo numeric not null,
    away_elo numeric not null,
    elo_diff numeric not null,
    home_rest_days numeric,
    away_rest_days numeric,
    rest_diff numeric,
    home_rolling_margin numeric not null,
    away_rolling_margin numeric not null,
    rolling_margin_diff numeric not null,
    target_home_win boolean not null,
    feature_schema_version text not null default 'NBA_EVENT_FEATURES_V1',
    feature_inputs_hash text not null,
    probability_publishable boolean not null default false,
    can_execute boolean not null default false,
    constraint wow_nba_feature_schema check (feature_schema_version='NBA_EVENT_FEATURES_V1'),
    constraint wow_nba_feature_hash check (feature_inputs_hash ~ '^[0-9a-f]{64}$'),
    constraint wow_nba_feature_never_publish check (probability_publishable=false),
    constraint wow_nba_feature_never_execute check (can_execute=false)
);
alter table public.wow_nba_pregame_feature_rows enable row level security;

create table if not exists public.wow_wnba_pregame_feature_rows (
    event_id text primary key references public.wow_wnba_training_games(event_id),
    as_of timestamptz not null,
    home_elo numeric not null,
    away_elo numeric not null,
    elo_diff numeric not null,
    home_rest_days numeric,
    away_rest_days numeric,
    rest_diff numeric,
    home_rolling_margin numeric not null,
    away_rolling_margin numeric not null,
    rolling_margin_diff numeric not null,
    target_home_win boolean not null,
    feature_schema_version text not null default 'WNBA_EVENT_FEATURES_V1',
    feature_inputs_hash text not null,
    probability_publishable boolean not null default false,
    can_execute boolean not null default false,
    constraint wow_wnba_feature_schema check (feature_schema_version='WNBA_EVENT_FEATURES_V1'),
    constraint wow_wnba_feature_hash check (feature_inputs_hash ~ '^[0-9a-f]{64}$'),
    constraint wow_wnba_feature_never_publish check (probability_publishable=false),
    constraint wow_wnba_feature_never_execute check (can_execute=false)
);
alter table public.wow_wnba_pregame_feature_rows enable row level security;

-- ---------------------------------------------------------------------------
-- Independent fitted artifact registries. No artifact rows are seeded here.
-- ---------------------------------------------------------------------------

create table if not exists public.wow_nba_event_fitted_model_artifacts (
    artifact_id uuid primary key default gen_random_uuid(),
    provider_identity text not null default 'WOW_NBA_EVENT_FITTED_MODEL_V1',
    model_family text not null,
    model_artifact_version text not null unique,
    artifact_format text not null,
    artifact_payload jsonb not null,
    artifact_checksum text not null,
    bundle_fingerprint text not null,
    feature_schema_version text not null,
    feature_transform_version text not null,
    training_code_sha text not null,
    training_dataset_hash text not null,
    training_rows integer not null,
    validation_metrics jsonb not null default '{}'::jsonb,
    calibrator_id uuid references public.wow_calibrators(calibrator_id),
    certification_id text,
    lifecycle_state text not null default 'CANDIDATE',
    active boolean not null default false,
    promoted boolean not null default false,
    probability_publishable boolean not null default false,
    can_execute boolean not null default false,
    created_at timestamptz not null default now(),
    promoted_at timestamptz,
    retired_at timestamptz,
    sport text not null default 'NBA',
    market_type text not null default 'MONEYLINE',
    settlement_basis text not null default 'FULL_GAME_OUTRIGHT',
    serving_mode text not null default 'PREGAME',
    governance_hash text,
    certification_expires_at timestamptz,
    revoked_at timestamptz,
    specialist_calibration_identity jsonb not null default '{}'::jsonb,
    constraint wow_nba_art_provider check (provider_identity='WOW_NBA_EVENT_FITTED_MODEL_V1'),
    constraint wow_nba_art_sport check (sport='NBA'),
    constraint wow_nba_art_market check (market_type='MONEYLINE' and settlement_basis='FULL_GAME_OUTRIGHT' and serving_mode='PREGAME'),
    constraint wow_nba_art_lifecycle check (lifecycle_state in ('CANDIDATE','PROSPECTIVE_CERTIFIED','CHAMPION','RETIRED','BLOCKED')),
    constraint wow_nba_art_rows check (training_rows>0),
    constraint wow_nba_art_hashes check (artifact_checksum ~ '^[0-9a-f]{64}$' and bundle_fingerprint ~ '^[0-9a-f]{64}$' and training_dataset_hash ~ '^[0-9a-f]{64}$' and training_code_sha ~ '^[0-9a-f]{40,64}$'),
    constraint wow_nba_art_never_execute check (can_execute=false),
    constraint wow_nba_art_registry_not_publish check (probability_publishable=false),
    constraint wow_nba_art_certified check (lifecycle_state not in ('PROSPECTIVE_CERTIFIED','CHAMPION') or (active and promoted and calibrator_id is not null and certification_id is not null and length(trim(certification_id))>0 and promoted_at is not null and revoked_at is null))
);
alter table public.wow_nba_event_fitted_model_artifacts enable row level security;
create unique index if not exists wow_nba_one_active_champion on public.wow_nba_event_fitted_model_artifacts(feature_schema_version) where active and lifecycle_state='CHAMPION';

create table if not exists public.wow_wnba_event_fitted_model_artifacts (
    artifact_id uuid primary key default gen_random_uuid(),
    provider_identity text not null default 'WOW_WNBA_EVENT_FITTED_MODEL_V1',
    model_family text not null,
    model_artifact_version text not null unique,
    artifact_format text not null,
    artifact_payload jsonb not null,
    artifact_checksum text not null,
    bundle_fingerprint text not null,
    feature_schema_version text not null,
    feature_transform_version text not null,
    training_code_sha text not null,
    training_dataset_hash text not null,
    training_rows integer not null,
    validation_metrics jsonb not null default '{}'::jsonb,
    calibrator_id uuid references public.wow_calibrators(calibrator_id),
    certification_id text,
    lifecycle_state text not null default 'CANDIDATE',
    active boolean not null default false,
    promoted boolean not null default false,
    probability_publishable boolean not null default false,
    can_execute boolean not null default false,
    created_at timestamptz not null default now(),
    promoted_at timestamptz,
    retired_at timestamptz,
    sport text not null default 'WNBA',
    market_type text not null default 'MONEYLINE',
    settlement_basis text not null default 'FULL_GAME_OUTRIGHT',
    serving_mode text not null default 'PREGAME',
    governance_hash text,
    certification_expires_at timestamptz,
    revoked_at timestamptz,
    specialist_calibration_identity jsonb not null default '{}'::jsonb,
    constraint wow_wnba_art_provider check (provider_identity='WOW_WNBA_EVENT_FITTED_MODEL_V1'),
    constraint wow_wnba_art_sport check (sport='WNBA'),
    constraint wow_wnba_art_market check (market_type='MONEYLINE' and settlement_basis='FULL_GAME_OUTRIGHT' and serving_mode='PREGAME'),
    constraint wow_wnba_art_lifecycle check (lifecycle_state in ('CANDIDATE','PROSPECTIVE_CERTIFIED','CHAMPION','RETIRED','BLOCKED')),
    constraint wow_wnba_art_rows check (training_rows>0),
    constraint wow_wnba_art_hashes check (artifact_checksum ~ '^[0-9a-f]{64}$' and bundle_fingerprint ~ '^[0-9a-f]{64}$' and training_dataset_hash ~ '^[0-9a-f]{64}$' and training_code_sha ~ '^[0-9a-f]{40,64}$'),
    constraint wow_wnba_art_never_execute check (can_execute=false),
    constraint wow_wnba_art_registry_not_publish check (probability_publishable=false),
    constraint wow_wnba_art_certified check (lifecycle_state not in ('PROSPECTIVE_CERTIFIED','CHAMPION') or (active and promoted and calibrator_id is not null and certification_id is not null and length(trim(certification_id))>0 and promoted_at is not null and revoked_at is null))
);
alter table public.wow_wnba_event_fitted_model_artifacts enable row level security;
create unique index if not exists wow_wnba_one_active_champion on public.wow_wnba_event_fitted_model_artifacts(feature_schema_version) where active and lifecycle_state='CHAMPION';

-- Canonical specialist ownership exists even before artifacts do.
insert into public.wow_specialist_registry(sport,market_family,controlling_specialist,active,precedence,can_execute)
values
 ('NBA','OUTRIGHT_WINNER','wow.nba-game-win-probability-expert',true,100,false),
 ('WNBA','OUTRIGHT_WINNER','wow.wnba-game-win-probability-expert',true,100,false)
on conflict (sport,market_family) do update
set controlling_specialist=excluded.controlling_specialist, active=true, precedence=100, can_execute=false, updated_at=now();

insert into public.wow_runtime_capabilities(capability_key,capability_status,evidence,can_execute)
values
 ('NBA_EVENT_PROBABILITY','UNAVAILABLE',jsonb_build_object('provider_identity','WOW_NBA_EVENT_FITTED_MODEL_V1','source_family','ESPN_PUBLIC_BASKETBALL','feature_schema_version','NBA_EVENT_FEATURES_V1','controlling_specialist','wow.nba-game-win-probability-expert','historical_data_ready',false,'fitted_model_ready',false,'calibrator_ready',false,'probability_publishable',false,'terminal_label_if_scored_now','MODEL_UNAVAILABLE','can_execute',false),false),
 ('WNBA_EVENT_PROBABILITY','UNAVAILABLE',jsonb_build_object('provider_identity','WOW_WNBA_EVENT_FITTED_MODEL_V1','source_family','ESPN_PUBLIC_BASKETBALL','feature_schema_version','WNBA_EVENT_FEATURES_V1','controlling_specialist','wow.wnba-game-win-probability-expert','historical_data_ready',false,'fitted_model_ready',false,'calibrator_ready',false,'probability_publishable',false,'terminal_label_if_scored_now','MODEL_UNAVAILABLE','can_execute',false),false)
on conflict (capability_key) do update
set capability_status='UNAVAILABLE', evidence=excluded.evidence, can_execute=false, updated_at=now();

create or replace function public.wow_nba_event_readiness() returns jsonb language sql stable security invoker set search_path=public as $$
with s as (select count(*) n from public.wow_nba_source_snapshots where source_status='CAPTURED'),
g as (select count(*) n from public.wow_nba_training_games), f as (select count(*) n from public.wow_nba_pregame_feature_rows),
a as (select count(*) n from public.wow_nba_event_fitted_model_artifacts where active and promoted and lifecycle_state in ('PROSPECTIVE_CERTIFIED','CHAMPION') and calibrator_id is not null and certification_id is not null and revoked_at is null)
select jsonb_build_object('ok',true,'sport','NBA','source_family','ESPN_PUBLIC_BASKETBALL','source_snapshots',s.n,'training_games',g.n,'feature_rows',f.n,'fitted_artifact_count',a.n,'historical_data_ready',(s.n>0 and g.n>0 and f.n>0),'model_status',case when a.n>0 then 'CERTIFIED_ARTIFACT_PRESENT' else 'MODEL_UNAVAILABLE' end,'probability_publishable',false,'can_execute',false) from s,g,f,a;
$$;
revoke all on function public.wow_nba_event_readiness() from anon, authenticated;

create or replace function public.wow_wnba_event_readiness() returns jsonb language sql stable security invoker set search_path=public as $$
with s as (select count(*) n from public.wow_wnba_source_snapshots where source_status='CAPTURED'),
g as (select count(*) n from public.wow_wnba_training_games), f as (select count(*) n from public.wow_wnba_pregame_feature_rows),
a as (select count(*) n from public.wow_wnba_event_fitted_model_artifacts where active and promoted and lifecycle_state in ('PROSPECTIVE_CERTIFIED','CHAMPION') and calibrator_id is not null and certification_id is not null and revoked_at is null)
select jsonb_build_object('ok',true,'sport','WNBA','source_family','ESPN_PUBLIC_BASKETBALL','source_snapshots',s.n,'training_games',g.n,'feature_rows',f.n,'fitted_artifact_count',a.n,'historical_data_ready',(s.n>0 and g.n>0 and f.n>0),'model_status',case when a.n>0 then 'CERTIFIED_ARTIFACT_PRESENT' else 'MODEL_UNAVAILABLE' end,'probability_publishable',false,'can_execute',false) from s,g,f,a;
$$;
revoke all on function public.wow_wnba_event_readiness() from anon, authenticated;
