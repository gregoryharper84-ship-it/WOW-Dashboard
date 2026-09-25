-- V17 candidate-bound certification evidence receipts.
--
-- This table records independent source-review + deterministic replay evidence
-- for an exact D1 candidate.  A receipt is evidence only: it cannot promote,
-- activate, publish, rank, or execute a sporting probability.

create table if not exists public.wow_d1_certification_evidence_receipts (
  receipt_id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  evidence_version text not null,
  candidate_id uuid not null references public.wow_d1_candidate_artifacts(candidate_id) on delete restrict,
  sport text not null,
  league text not null,
  lane_id text not null,
  model_family text not null,
  model_artifact_version text not null,
  training_dataset_hash text not null,
  artifact_checksum text not null,
  source_policy_id text not null,
  source_review_pass boolean not null default false,
  replay_evidence_pass boolean not null default false,
  evidence_json jsonb not null default '{}'::jsonb,
  probability_publishable boolean not null default false check (probability_publishable = false),
  can_execute boolean not null default false check (can_execute = false),
  unique (candidate_id, evidence_version)
);

create index if not exists wow_d1_cert_evidence_lane_created_idx
  on public.wow_d1_certification_evidence_receipts (lane_id, created_at desc);

alter table public.wow_d1_certification_evidence_receipts enable row level security;

comment on table public.wow_d1_certification_evidence_receipts is
  'Append-only-style V17 evidence receipts binding independent source/replay verification to one exact D1 candidate. PASS evidence does not certify, promote, publish, rank, or execute.';
comment on column public.wow_d1_certification_evidence_receipts.source_review_pass is
  'True only when the verifier proved the exact candidate training source is entitled/reviewed and every inspected persisted row passed provenance and temporal-integrity checks.';
comment on column public.wow_d1_certification_evidence_receipts.replay_evidence_pass is
  'True only when a deterministic replay of the exact persisted training rows reproduced the candidate dataset hash, artifact checksum, calibrator, and governed metrics.';
