-- V17 evidence provenance, disagreement review, and hypothesis governance.
alter table public.wow_nfl_source_snapshots drop constraint if exists wow_nfl_source_dataset;
alter table public.wow_nfl_source_snapshots add constraint wow_nfl_source_dataset check (dataset_name in (
  'SCHEDULES','PLAY_BY_PLAY','WEEKLY_ROSTERS','INJURIES','PARTICIPATION','SNAP_COUNTS'
));

create or replace function public.wow_v17_block_immutable_change()
returns trigger language plpgsql as $$ begin
  raise exception '% row is immutable', tg_table_name;
end $$;

create table if not exists public.wow_v17_external_source_snapshots (
  snapshot_id uuid primary key default gen_random_uuid(), source_identity text not null,
  access_licensing_classification text not null, request_timestamp timestamptz not null,
  source_published_timestamp timestamptz, event_id text, player_id text,
  schema_version text not null, schema_fingerprint text not null, freshness_limit_seconds integer not null check (freshness_limit_seconds > 0),
  completeness_score numeric not null check (completeness_score between 0 and 1),
  allowed_model_fields jsonb not null default '[]', allowed_evidence_only_fields jsonb not null default '[]',
  fallback_sources jsonb not null default '[]', fail_closed_behavior text not null,
  raw_payload_sha256 text not null check (raw_payload_sha256 ~ '^[0-9a-f]{64}$'), raw_object_uri text not null,
  immutable_raw_snapshot boolean not null default true check (immutable_raw_snapshot),
  model_authoritative boolean not null default false, can_execute boolean not null default false check (not can_execute),
  created_at timestamptz not null default now()
);

create table if not exists public.wow_v17_model_disagreement_monitor (
  comparison_id uuid primary key default gen_random_uuid(), event_id text not null, model_version text not null,
  model_probability numeric not null check (model_probability between 0 and 1), opener_probability numeric not null check (opener_probability between 0 and 1),
  decision_consensus_probability numeric not null check (decision_consensus_probability between 0 and 1), close_probability numeric check (close_probability between 0 and 1),
  observed_at timestamptz not null, review_status text not null check (review_status in ('OBSERVE','REVIEW_REQUIRED')),
  persistent_large_gap boolean not null default false, automatic_suppression boolean not null default false check (not automatic_suppression),
  probability_unchanged boolean not null default true check (probability_unchanged), can_execute boolean not null default false check (not can_execute)
);

create table if not exists public.wow_v17_temporal_feature_provenance (
  provenance_id uuid primary key default gen_random_uuid(), prediction_id uuid, feature_name text not null,
  feature_value_hash text not null check (feature_value_hash ~ '^[0-9a-f]{64}$'), source_snapshot_id uuid not null references public.wow_v17_external_source_snapshots(snapshot_id),
  source_published_at timestamptz not null, first_knowable_at timestamptz not null, captured_at timestamptz not null, used_at timestamptz not null,
  availability_basis text not null, can_execute boolean not null default false check (not can_execute),
  check (source_published_at <= first_knowable_at and first_knowable_at <= captured_at and captured_at <= used_at)
);

create table if not exists public.wow_v17_hypothesis_change_ledger (
  change_id uuid primary key default gen_random_uuid(), model_family text not null, sporting_rationale text not null,
  affected_feature text not null, expected_direction text not null check (expected_direction in ('INCREASE','DECREASE','NON_MONOTONIC')),
  training_start timestamptz not null, training_end timestamptz not null, holdout_start timestamptz not null, holdout_end timestamptz not null,
  calibration_before jsonb not null, calibration_after jsonb not null, holdout_untouched boolean not null default true check (holdout_untouched),
  automatic_promotion boolean not null default false check (not automatic_promotion), can_execute boolean not null default false check (not can_execute),
  created_at timestamptz not null default now(), check (training_start < training_end and training_end < holdout_start and holdout_start < holdout_end)
);

do $$ declare t text; begin foreach t in array array['wow_v17_external_source_snapshots','wow_v17_model_disagreement_monitor','wow_v17_temporal_feature_provenance','wow_v17_hypothesis_change_ledger'] loop
  execute format('alter table public.%I enable row level security', t);
  execute format('drop trigger if exists trg_%I_immutable on public.%I', t, t);
  execute format('create trigger trg_%I_immutable before update or delete on public.%I for each row execute function public.wow_v17_block_immutable_change()', t, t);
  execute format('revoke all on table public.%I from anon, authenticated', t);
end loop; end $$;
