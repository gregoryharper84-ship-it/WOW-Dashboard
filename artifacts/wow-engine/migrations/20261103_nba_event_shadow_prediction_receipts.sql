-- NBA team-event pregame shadow receipt only. Apply only after protected review.
-- Do not write NBA into the MLB-constrained wow_event_predictions table.
create table if not exists public.wow_nba_event_shadow_predictions (
  nba_prediction_id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  official_event_id text not null,
  event_start_time_utc timestamptz not null,
  home_team text not null,
  away_team text not null,
  source_snapshot_id text not null,
  source_snapshot_timestamp timestamptz not null,
  model_timestamp timestamptz not null,
  certification_id text not null,
  model_artifact_id text not null,
  model_artifact_version text not null,
  model_artifact_sha256 text not null,
  calibration_artifact_id text not null,
  calibration_artifact_sha256 text not null,
  raw_home_probability numeric not null,
  calibrated_home_probability numeric not null,
  calibrated_home_lower_bound numeric not null,
  calibrated_home_upper_bound numeric not null,
  prediction_sha256 text not null,
  terminal_label text not null default 'MODEL_RESEARCH_ONLY_HOLD'
    check (terminal_label = 'MODEL_RESEARCH_ONLY_HOLD'),
  probability_publishable boolean not null default false
    check (probability_publishable = false),
  rank_eligible boolean not null default false check (rank_eligible = false),
  can_execute boolean not null default false check (can_execute = false),
  constraint nba_shadow_unique_identity unique (
    official_event_id,source_snapshot_id,model_artifact_id,calibration_artifact_id
  ),
  constraint nba_shadow_prospective check (
    source_snapshot_timestamp <= model_timestamp
    and model_timestamp < event_start_time_utc
    and created_at < event_start_time_utc
  ),
  constraint nba_shadow_distinct_teams check (home_team <> away_team),
  constraint nba_shadow_source_digest check (source_snapshot_id ~ '^sha256:[0-9a-f]{64}$'),
  constraint nba_shadow_hashes check (
    model_artifact_sha256 ~ '^[0-9a-f]{64}$'
    and calibration_artifact_sha256 ~ '^[0-9a-f]{64}$'
    and prediction_sha256 ~ '^[0-9a-f]{64}$'
  ),
  constraint nba_shadow_calibrated_bounds check (
    raw_home_probability > 0 and raw_home_probability < 1
    and calibrated_home_lower_bound > 0
    and calibrated_home_lower_bound <= calibrated_home_probability
    and calibrated_home_probability <= calibrated_home_upper_bound
    and calibrated_home_upper_bound < 1
  )
);
alter table public.wow_nba_event_shadow_predictions enable row level security;
revoke all on table public.wow_nba_event_shadow_predictions from public, anon, authenticated;
revoke update, delete, truncate, references, trigger
  on table public.wow_nba_event_shadow_predictions from service_role;
grant select, insert on table public.wow_nba_event_shadow_predictions to service_role;
