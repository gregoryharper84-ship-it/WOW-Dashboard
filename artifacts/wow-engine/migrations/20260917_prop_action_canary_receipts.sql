-- V17 exact-route prop Action-canary receipt ledger.
-- A row may be written only after a REAL canonical /score-pick-request call has
-- returned a persisted prediction and exact-once reconciliation PASS. This table
-- is proof storage only; it cannot grant model capability or execution authority.

create table if not exists public.wow_prop_action_canary_receipts (
    receipt_id uuid primary key default gen_random_uuid(),
    created_at timestamptz not null default now(),
    action_operation_id text not null,
    action_request_fingerprint text not null,
    prediction_id uuid not null references public.wow_predictions(prediction_id),
    sport text not null,
    stat_type text not null,
    feature_schema_version text not null,
    model_family text not null,
    model_artifact_version text not null,
    artifact_checksum text not null,
    calibrator_version text not null,
    certification_id text not null,
    calibration_evidence_hash text not null,
    raw_model_probability numeric not null check (raw_model_probability between 0 and 1),
    calibrated_probability numeric not null check (calibrated_probability between 0 and 1),
    calibrated_lower_bound numeric not null check (
        calibrated_lower_bound between 0 and 1
        and calibrated_lower_bound <= calibrated_probability
    ),
    reconciliation_status text not null check (upper(reconciliation_status) = 'PASS'),
    immutable_receipt_hash text not null unique,
    can_execute boolean not null default false check (can_execute = false),
    constraint wow_prop_action_canary_operation_ck
        check (action_operation_id in ('scoreWowPickRequest','scoreWowV17PickRequest')),
    constraint wow_prop_action_canary_identity_uq unique (
        sport, stat_type, feature_schema_version, model_artifact_version,
        artifact_checksum, certification_id, immutable_receipt_hash
    )
);

create index if not exists wow_prop_action_canary_exact_route_idx
    on public.wow_prop_action_canary_receipts (
        upper(sport), upper(stat_type), feature_schema_version,
        model_artifact_version, artifact_checksum, certification_id, created_at desc
    );

alter table public.wow_prop_action_canary_receipts enable row level security;
revoke all on table public.wow_prop_action_canary_receipts from public, anon, authenticated;
grant select, insert on table public.wow_prop_action_canary_receipts to service_role;

comment on table public.wow_prop_action_canary_receipts is
'Immutable proof ledger for real canonical V17 prop Action canaries. Presence alone never grants capability; exact certification evidence hash, artifact identity, runtime adapters, hydration, and lifecycle audit must all pass. can_execute is permanently false.';
