-- WOW V17 NBA/WNBA team-event specialist foundation.
-- User-authorized R3 build scope. This migration creates league-separated
-- training, features, model artifacts, calibrators, and certification state.
-- It does not promote any model and does not alter can_execute=false.

create table if not exists public.wow_nba_training_games (
  game_id text primary key, season integer not null, game_date date not null,
  status text not null, home_team_id text not null, away_team_id text not null,
  home_score integer, away_score integer, home_win boolean,
  source_provider text not null default 'BALLDONTLIE', source_endpoint text not null,
  source_retrieved_at timestamptz not null, source_payload_sha256 text not null,
  settled boolean not null default false, created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.wow_wnba_training_games (like public.wow_nba_training_games including all);

create table if not exists public.wow_nba_pregame_feature_rows (
  game_id text primary key references public.wow_nba_training_games(game_id) on delete cascade,
  as_of timestamptz not null, feature_schema_version text not null,
  home_games_prior integer not null, away_games_prior integer not null,
  home_win_rate_prior double precision, away_win_rate_prior double precision,
  home_point_diff_prior double precision, away_point_diff_prior double precision,
  home_rest_days integer, away_rest_days integer,
  home_back_to_back boolean, away_back_to_back boolean,
  feature_payload jsonb not null, feature_payload_sha256 text not null,
  created_at timestamptz not null default now()
);

create table if not exists public.wow_wnba_pregame_feature_rows (
  game_id text primary key references public.wow_wnba_training_games(game_id) on delete cascade,
  as_of timestamptz not null, feature_schema_version text not null,
  home_games_prior integer not null, away_games_prior integer not null,
  home_win_rate_prior double precision, away_win_rate_prior double precision,
  home_point_diff_prior double precision, away_point_diff_prior double precision,
  home_rest_days integer, away_rest_days integer,
  home_back_to_back boolean, away_back_to_back boolean,
  feature_payload jsonb not null, feature_payload_sha256 text not null,
  created_at timestamptz not null default now()
);

create table if not exists public.wow_basketball_team_event_model_artifacts (
  artifact_id uuid primary key default gen_random_uuid(),
  sport text not null check (sport in ('NBA','WNBA')),
  model_family text not null, model_artifact_version text not null,
  feature_schema_version text not null, artifact_format text not null,
  artifact_payload jsonb not null, artifact_checksum text not null,
  training_row_count integer not null, train_start_date date not null, train_end_date date not null,
  holdout_row_count integer not null, holdout_brier double precision,
  holdout_log_loss double precision, holdout_accuracy double precision,
  promoted boolean not null default false, active boolean not null default false,
  certified_at timestamptz, created_at timestamptz not null default now(),
  unique (sport, model_artifact_version)
);

create table if not exists public.wow_basketball_team_event_calibrators (
  calibrator_id uuid primary key default gen_random_uuid(),
  sport text not null check (sport in ('NBA','WNBA')),
  calibrator_version text not null, model_artifact_version text not null,
  calibration_method text not null, coefficients jsonb not null,
  calibration_row_count integer not null, brier double precision,
  log_loss double precision, ece double precision, calibration_bias double precision,
  promoted boolean not null default false, active boolean not null default false,
  created_at timestamptz not null default now(), unique (sport, calibrator_version)
);

create table if not exists public.wow_basketball_team_event_certification (
  sport text primary key check (sport in ('NBA','WNBA')),
  capability_status text not null default 'UNAVAILABLE',
  reason_code text not null default 'MODEL_UNAVAILABLE',
  model_artifact_version text, calibrator_version text,
  corpus_rows integer not null default 0, settled_rows integer not null default 0,
  certification_notes jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  check (capability_status in ('UNAVAILABLE','SHADOW','CERTIFIED'))
);

insert into public.wow_basketball_team_event_certification (sport)
values ('NBA'),('WNBA') on conflict (sport) do nothing;

alter table public.wow_nba_training_games enable row level security;
alter table public.wow_wnba_training_games enable row level security;
alter table public.wow_nba_pregame_feature_rows enable row level security;
alter table public.wow_wnba_pregame_feature_rows enable row level security;
alter table public.wow_basketball_team_event_model_artifacts enable row level security;
alter table public.wow_basketball_team_event_calibrators enable row level security;
alter table public.wow_basketball_team_event_certification enable row level security;

create or replace function public.wow_basketball_certified_model_artifact(p_sport text)
returns table(sport text, model_family text, model_artifact_version text,
  feature_schema_version text, artifact_format text, artifact_payload jsonb, artifact_checksum text)
language sql stable security definer set search_path=public as $$
  select a.sport,a.model_family,a.model_artifact_version,a.feature_schema_version,
         a.artifact_format,a.artifact_payload,a.artifact_checksum
    from public.wow_basketball_team_event_model_artifacts a
    join public.wow_basketball_team_event_certification c
      on c.sport=a.sport and c.model_artifact_version=a.model_artifact_version
   where a.sport=upper(p_sport) and a.promoted and a.active and c.capability_status='CERTIFIED'
   order by a.certified_at desc nulls last,a.created_at desc limit 1;
$$;
