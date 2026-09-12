create schema if not exists wow_scout;

create table if not exists wow_scout.candidates (
  candidate_id text primary key,
  sport_key text not null,
  scout_team text not null,
  market_type text not null,
  selection text,
  event_id text,
  canonical_event_id text,
  commence_time timestamptz,
  home_team text,
  away_team text,
  controlling_specialist text not null,
  research_status text not null default 'WATCH' check (research_status in ('WATCH','RESEARCH_INTEREST_LOW','RESEARCH_INTEREST_MEDIUM','RESEARCH_INTEREST_HIGH','QUARANTINED','NO_INTEREST')),
  research_priority_score numeric(5,2),
  thesis text,
  edge_classes text[] not null default '{}',
  contradictory_evidence text[] not null default '{}',
  red_team_flags text[] not null default '{}',
  data_completeness numeric(5,2),
  source_freshness_score numeric(5,2),
  probability numeric null check (probability is null),
  can_execute boolean not null default false check (can_execute = false),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists wow_scout.observations (
  observation_id bigint generated always as identity primary key,
  candidate_id text not null references wow_scout.candidates(candidate_id) on delete cascade,
  agent_id text not null,
  stage text not null,
  source_type text not null,
  source_name text,
  source_url text,
  source_published_at timestamptz,
  observed_at timestamptz not null default now(),
  confidence text not null default 'MEDIUM' check (confidence in ('LOW','MEDIUM','HIGH','CONFIRMED')),
  evidence_type text not null,
  evidence_text text not null,
  evidence_value jsonb not null default '{}'::jsonb,
  is_contradictory boolean not null default false,
  is_stale boolean not null default false,
  checksum text,
  unique(candidate_id, agent_id, checksum)
);

create table if not exists wow_scout.research_runs (
  research_run_id text primary key,
  sport_key text not null,
  scout_team text not null,
  stage text not null,
  scheduled_for timestamptz,
  started_at timestamptz,
  completed_at timestamptz,
  status text not null,
  source_snapshot jsonb not null default '{}'::jsonb,
  candidate_count integer not null default 0,
  changed_candidate_count integer not null default 0,
  quarantined_count integer not null default 0,
  error_code text,
  can_execute boolean not null default false check (can_execute = false)
);

create table if not exists wow_scout.candidate_history (
  history_id bigint generated always as identity primary key,
  candidate_id text not null references wow_scout.candidates(candidate_id) on delete cascade,
  research_run_id text references wow_scout.research_runs(research_run_id),
  recorded_at timestamptz not null default now(),
  research_status text not null,
  research_priority_score numeric(5,2),
  thesis text,
  edge_classes text[] not null default '{}',
  contradictory_evidence text[] not null default '{}',
  red_team_flags text[] not null default '{}',
  data_completeness numeric(5,2),
  source_freshness_score numeric(5,2),
  probability numeric null check (probability is null),
  snapshot jsonb not null default '{}'::jsonb
);

create table if not exists wow_scout.final_boards (
  board_id text primary key,
  sport_key text not null,
  slate_date date not null,
  board_type text not null,
  generated_at timestamptz not null default now(),
  status text not null,
  candidate_ids text[] not null default '{}',
  quarantined_candidate_ids text[] not null default '{}',
  unresolved_candidate_ids text[] not null default '{}',
  specialist_handoff_ready boolean not null default false,
  payload jsonb not null default '{}'::jsonb,
  can_execute boolean not null default false check (can_execute = false),
  unique(sport_key, slate_date, board_type)
);

create index if not exists idx_wow_scout_candidates_sport_status on wow_scout.candidates(sport_key, research_status);
create index if not exists idx_wow_scout_candidates_commence on wow_scout.candidates(commence_time);
create index if not exists idx_wow_scout_observations_candidate_time on wow_scout.observations(candidate_id, observed_at desc);
create index if not exists idx_wow_scout_runs_sport_time on wow_scout.research_runs(sport_key, scheduled_for desc);
create index if not exists idx_wow_scout_history_candidate_time on wow_scout.candidate_history(candidate_id, recorded_at desc);
