-- Append-only candidate-bound certification evidence receipts for V17 team/event challengers.
-- These receipts are evidence only. They never certify, promote, activate, publish,
-- rank, or execute a sporting probability.

create table if not exists public.wow_d1_certification_evidence_receipts (
    receipt_id uuid primary key default gen_random_uuid(),
    created_at timestamptz not null default now(),
    candidate_id uuid not null references public.wow_d1_candidate_artifacts(candidate_id),
    sport text not null,
    league text not null,
    model_family text not null,
    model_artifact_version text not null,
    training_dataset_hash text not null check (training_dataset_hash ~ '^[0-9a-f]{64}$'),
    artifact_checksum text not null check (artifact_checksum ~ '^[0-9a-f]{64}$'),
    source_review_status text not null check (source_review_status in ('PASS','FAIL')),
    replay_status text not null check (replay_status in ('PASS','FAIL')),
    verifier_version text not null,
    verification_payload jsonb not null default '{}'::jsonb check (jsonb_typeof(verification_payload) = 'object'),
    evidence_sha256 text not null check (evidence_sha256 ~ '^[0-9a-f]{64}$'),
    probability_publishable boolean not null default false check (probability_publishable = false),
    can_execute boolean not null default false check (can_execute = false),
    constraint uq_wow_d1_certification_evidence_exact unique (
        candidate_id, model_artifact_version, training_dataset_hash, artifact_checksum, evidence_sha256
    )
);

create index if not exists idx_wow_d1_certification_evidence_candidate
    on public.wow_d1_certification_evidence_receipts (
        candidate_id, model_artifact_version, training_dataset_hash, artifact_checksum, created_at desc
    );

alter table public.wow_d1_certification_evidence_receipts enable row level security;
revoke all privileges on public.wow_d1_certification_evidence_receipts
    from public, anon, authenticated, service_role;
grant select, insert on public.wow_d1_certification_evidence_receipts to service_role;

create trigger wow_d1_certification_evidence_receipts_immutable
before update or delete on public.wow_d1_certification_evidence_receipts
for each row execute function public.wow_reject_immutable_mutation();

comment on table public.wow_d1_certification_evidence_receipts is
'Append-only exact-candidate source-review and deterministic-replay evidence. Evidence only; never promotion or execution.';
