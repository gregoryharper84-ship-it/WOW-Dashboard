-- NCAAF/CFB player-prop historical acquisition foundation.
-- This migration adds read-only CFBD /games/players staging and normalized
-- primitive player/game/stat history. It does NOT register a specialist,
-- certify an artifact, publish a probability, or enable execution.

alter table public.wow_ncaaf_source_snapshots
  drop constraint if exists wow_ncaaf_source_endpoint;

alter table public.wow_ncaaf_source_snapshots
  add constraint wow_ncaaf_source_endpoint
  check (endpoint in (
    '/games',
    '/games/players',
    '/ratings/core',
    '/ratings/sp',
    '/ratings/srs',
    '/ratings/elo',
    '/ratings/fpi'
  ));

create table if not exists public.wow_ncaaf_player_stat_history (
  player_stat_history_id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  provider text not null,
  source_endpoint text not null,
  source_payload_sha256 text not null,
  source_retrieved_at timestamptz not null,
  season integer not null,
  week integer not null,
  game_id text not null,
  team text not null,
  conference text,
  home_away text,
  athlete_id text not null,
  athlete_name text not null,
  stat_type text not null,
  stat_value double precision not null,
  feature_schema_version text not null,
  can_execute boolean not null default false,
  constraint wow_ncaaf_player_stat_provider check (provider = 'CFBD'),
  constraint wow_ncaaf_player_stat_source_endpoint check (source_endpoint = '/games/players'),
  constraint wow_ncaaf_player_stat_season check (season between 2000 and 2100),
  constraint wow_ncaaf_player_stat_week check (week between 0 and 30),
  constraint wow_ncaaf_player_stat_home_away check (home_away is null or home_away in ('home','away')),
  constraint wow_ncaaf_player_stat_type check (stat_type in (
    'PASS_YARDS',
    'PASS_TDS',
    'INTERCEPTIONS_THROWN',
    'PASS_COMPLETIONS',
    'PASS_ATTEMPTS',
    'RUSH_YARDS',
    'RUSH_TDS',
    'RUSH_ATTEMPTS',
    'RECEIVING_YARDS',
    'RECEIVING_TDS',
    'RECEPTIONS'
  )),
  constraint wow_ncaaf_player_stat_value_finite check (isfinite(stat_value)),
  constraint wow_ncaaf_player_stat_never_execute check (can_execute = false),
  unique (season, week, game_id, team, athlete_id, stat_type, source_payload_sha256)
);

create index if not exists wow_ncaaf_player_stat_history_lookup_idx
  on public.wow_ncaaf_player_stat_history (athlete_id, stat_type, season, week);

create index if not exists wow_ncaaf_player_stat_history_game_idx
  on public.wow_ncaaf_player_stat_history (game_id, team, stat_type);

alter table public.wow_ncaaf_player_stat_history enable row level security;
revoke all on table public.wow_ncaaf_player_stat_history from anon, authenticated;
grant all on table public.wow_ncaaf_player_stat_history to service_role;

comment on table public.wow_ncaaf_player_stat_history is
  'Normalized historical NCAAF player box-score primitives for governed prop-model research. Outcome history only; not a model probability or publication authority.';
