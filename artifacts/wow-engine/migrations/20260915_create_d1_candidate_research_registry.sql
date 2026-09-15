create table if not exists public.wow_d1_source_events (
  source_event_id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  sport text not null,
  league text not null,
  official_event_id text not null,
  season text,
  event_start_time timestamptz not null,
  home_participant text,
  away_participant text,
  outcome_json jsonb not null default '{}'::jsonb check (jsonb_typeof(outcome_json) = 'object'),
  source_provider text not null,
  source_uri text,
  source_retrieved_at timestamptz not null,
  source_payload_sha256 text not null check (source_payload_sha256 ~ '^[0-9a-f]{64}$'),
  historical_reconstruction boolean not null default true,
  can_execute boolean not null default false check (can_execute = false),
  unique (sport, official_event_id, source_payload_sha256)
);

create table if not exists public.wow_d1_training_rows (
  training_row_id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  sport text not null,
  league text not null,
  official_event_id text not null,
  event_start_time timestamptz not null,
  feature_as_of timestamptz not null,
  feature_schema_version text not null,
  model_family text not null,
  features jsonb not null check (jsonb_typeof(features) = 'object'),
  outcome_json jsonb not null check (jsonb_typeof(outcome_json) = 'object'),
  source_manifest jsonb not null check (jsonb_typeof(source_manifest) = 'object'),
  source_manifest_sha256 text not null check (source_manifest_sha256 ~ '^[0-9a-f]{64}$'),
  historical_reconstruction boolean not null default true,
  archived_pregame_snapshot boolean not null default false,
  market_features_used boolean not null default false,
  can_execute boolean not null default false check (can_execute = false),
  check (feature_as_of < event_start_time),
  unique (sport, official_event_id, feature_schema_version, source_manifest_sha256)
);

create table if not exists public.wow_d1_candidate_artifacts (
  candidate_id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  sport text not null,
  league text not null,
  market_family text not null default 'OUTRIGHT_WINNER',
  model_family text not null,
  model_artifact_version text not null unique,
  feature_schema_version text not null,
  source_policy_id text not null,
  training_dataset_hash text not null check (training_dataset_hash ~ '^[0-9a-f]{64}$'),
  training_code_sha text not null check (training_code_sha ~ '^[0-9a-f]{7,64}$'),
  artifact_checksum text not null check (artifact_checksum ~ '^[0-9a-f]{64}$'),
  artifact_payload jsonb not null check (jsonb_typeof(artifact_payload) = 'object'),
  calibrator_payload jsonb not null default '{}'::jsonb check (jsonb_typeof(calibrator_payload) = 'object'),
  validation_metrics jsonb not null default '{}'::jsonb check (jsonb_typeof(validation_metrics) = 'object'),
  training_rows integer not null check (training_rows > 0),
  calibration_rows integer not null default 0 check (calibration_rows >= 0),
  test_rows integer not null default 0 check (test_rows >= 0),
  research_screen_pass boolean not null default false,
  source_review_status text not null default 'REQUIRED' check (source_review_status in ('REQUIRED','PASS','FAIL')),
  lifecycle_state text not null default 'CANDIDATE' check (lifecycle_state = 'CANDIDATE'),
  promoted boolean not null default false check (promoted = false),
  active boolean not null default false check (active = false),
  automatic_certification boolean not null default false check (automatic_certification = false),
  automatic_promotion boolean not null default false check (automatic_promotion = false),
  probability_publishable boolean not null default false check (probability_publishable = false),
  can_execute boolean not null default false check (can_execute = false)
);

alter table public.wow_d1_source_events enable row level security;
alter table public.wow_d1_training_rows enable row level security;
alter table public.wow_d1_candidate_artifacts enable row level security;

revoke all on public.wow_d1_source_events from public, anon, authenticated;
revoke all on public.wow_d1_training_rows from public, anon, authenticated;
revoke all on public.wow_d1_candidate_artifacts from public, anon, authenticated;

grant select, insert on public.wow_d1_source_events to service_role;
grant select, insert on public.wow_d1_training_rows to service_role;
grant select, insert on public.wow_d1_candidate_artifacts to service_role;

create trigger wow_d1_source_events_immutable
before update or delete on public.wow_d1_source_events
for each row execute function public.wow_reject_immutable_mutation();

create trigger wow_d1_training_rows_immutable
before update or delete on public.wow_d1_training_rows
for each row execute function public.wow_reject_immutable_mutation();

create trigger wow_d1_candidate_artifacts_immutable
before update or delete on public.wow_d1_candidate_artifacts
for each row execute function public.wow_reject_immutable_mutation();
