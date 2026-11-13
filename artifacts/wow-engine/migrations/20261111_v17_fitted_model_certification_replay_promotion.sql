-- WOW V17 fitted-model certification replay + promotion receipts.
--
-- Append-only audit evidence.  These tables do not certify, promote, activate,
-- publish, rank, or execute a model.  A promotion row records a dry-run
-- governed-apply packet for a separate privileged review; applying it is out of
-- scope for this migration.
--
-- can_execute and probability_publishable are permanently false.

create table if not exists public.wow_fitted_model_certification_replay_receipts (
    receipt_id uuid primary key default gen_random_uuid(),
    artifact_id text not null,
    sport text not null,
    league text not null,
    surface text not null,
    model_family text not null,
    stat_type text,
    model_artifact_version text not null,
    artifact_checksum text not null,
    status text not null,
    verifier_version text not null,
    blockers jsonb not null default '[]'::jsonb,
    metrics jsonb not null default '{}'::jsonb,
    partition_counts jsonb not null default '{}'::jsonb,
    settlement_counts jsonb not null default '{}'::jsonb,
    partition_settled_counts jsonb not null default '{}'::jsonb,
    received_corpus_sha256 text,
    receipt_sha256 text not null,
    payload jsonb not null,
    probability_publishable boolean not null default false,
    can_execute boolean not null default false,
    created_at timestamptz not null default now(),
    constraint wow_fitted_replay_surface check (
        surface in ('TEAM_EVENT','PROP','__UNIDENTIFIED__')
    ),
    constraint wow_fitted_replay_status check (
        status in ('CERTIFICATION_REPLAY_PASS','CERTIFICATION_REPLAY_FAILED')
    ),
    constraint wow_fitted_replay_blockers_array check (jsonb_typeof(blockers) = 'array'),
    constraint wow_fitted_replay_metrics_object check (jsonb_typeof(metrics) = 'object'),
    constraint wow_fitted_replay_partition_object check (jsonb_typeof(partition_counts) = 'object'),
    constraint wow_fitted_replay_settlement_object check (jsonb_typeof(settlement_counts) = 'object'),
    constraint wow_fitted_replay_partition_settled_object check (jsonb_typeof(partition_settled_counts) = 'object'),
    constraint wow_fitted_replay_receipt_hash_shape check (receipt_sha256 ~ '^[0-9a-f]{64}$'),
    constraint wow_fitted_replay_corpus_hash_shape check (
        received_corpus_sha256 is null
        or received_corpus_sha256 = '__UNIDENTIFIED__'
        or received_corpus_sha256 ~ '^[0-9a-f]{64}$'
    ),
    constraint wow_fitted_replay_never_publish check (probability_publishable = false),
    constraint wow_fitted_replay_never_execute check (can_execute = false)
);

create unique index if not exists wow_fitted_replay_receipt_identity
    on public.wow_fitted_model_certification_replay_receipts (
        artifact_id, artifact_checksum, received_corpus_sha256,
        verifier_version, receipt_sha256
    );

create index if not exists wow_fitted_replay_lane_lookup
    on public.wow_fitted_model_certification_replay_receipts (
        sport, surface, stat_type, model_artifact_version, created_at desc
    );

create table if not exists public.wow_fitted_model_promotion_receipts (
    receipt_id uuid primary key default gen_random_uuid(),
    artifact_id text not null,
    sport text not null,
    league text not null,
    surface text not null,
    model_family text not null,
    stat_type text,
    model_artifact_version text not null,
    artifact_checksum text not null,
    status text not null,
    policy_id text not null,
    policy_sha256 text not null,
    authorization_id text not null,
    authorization_sha256 text not null,
    replay_receipt_sha256 text not null,
    blockers jsonb not null default '[]'::jsonb,
    promotion_sha256 text not null,
    apply_packet jsonb,
    payload jsonb not null,
    probability_publishable boolean not null default false,
    can_execute boolean not null default false,
    created_at timestamptz not null default now(),
    constraint wow_fitted_promotion_surface check (
        surface in ('TEAM_EVENT','PROP','__UNIDENTIFIED__')
    ),
    constraint wow_fitted_promotion_status check (
        status in (
            'PROMOTION_BLOCKED',
            'REMAIN_SHADOW',
            'PROMOTION_POLICY_REVIEW_REQUIRED',
            'PROMOTION_READY_FOR_INDEPENDENT_REVIEW',
            'PROMOTION_AUTHORIZATION_INVALID',
            'PROMOTION_APPROVED_FOR_GOVERNED_APPLY',
            'PROMOTION_ALREADY_APPLIED'
        )
    ),
    constraint wow_fitted_promotion_blockers_array check (jsonb_typeof(blockers) = 'array'),
    constraint wow_fitted_promotion_payload_object check (jsonb_typeof(payload) = 'object'),
    constraint wow_fitted_promotion_packet_object check (
        apply_packet is null or jsonb_typeof(apply_packet) = 'object'
    ),
    constraint wow_fitted_promotion_replay_hash_shape check (
        replay_receipt_sha256 = '__UNIDENTIFIED__'
        or replay_receipt_sha256 ~ '^[0-9a-f]{64}$'
    ),
    constraint wow_fitted_promotion_packet_hash_shape check (promotion_sha256 ~ '^[0-9a-f]{64}$'),
    constraint wow_fitted_promotion_never_publish check (probability_publishable = false),
    constraint wow_fitted_promotion_never_execute check (can_execute = false)
);

create unique index if not exists wow_fitted_promotion_receipt_identity
    on public.wow_fitted_model_promotion_receipts (
        artifact_id, artifact_checksum, policy_sha256,
        authorization_sha256, promotion_sha256
    );

create index if not exists wow_fitted_promotion_lane_lookup
    on public.wow_fitted_model_promotion_receipts (
        sport, surface, stat_type, model_artifact_version, created_at desc
    );

-- Append-only enforcement: a recorded replay or decision may never be edited or
-- deleted.  A corrected result is a new immutable receipt.
create or replace function public.wow_fitted_model_receipt_immutable()
returns trigger
language plpgsql
as $$
begin
    raise exception 'WOW_FITTED_MODEL_RECEIPTS_ARE_APPEND_ONLY';
end;
$$;

drop trigger if exists wow_fitted_replay_receipt_immutable on public.wow_fitted_model_certification_replay_receipts;
create trigger wow_fitted_replay_receipt_immutable
    before update or delete on public.wow_fitted_model_certification_replay_receipts
    for each row execute function public.wow_fitted_model_receipt_immutable();

drop trigger if exists wow_fitted_promotion_receipt_immutable on public.wow_fitted_model_promotion_receipts;
create trigger wow_fitted_promotion_receipt_immutable
    before update or delete on public.wow_fitted_model_promotion_receipts
    for each row execute function public.wow_fitted_model_receipt_immutable();

alter table public.wow_fitted_model_certification_replay_receipts enable row level security;
alter table public.wow_fitted_model_promotion_receipts enable row level security;

-- No client-role reads or writes.  The service role writes audit receipts only;
-- it still cannot turn a receipt into probability or execution authority.
revoke all on public.wow_fitted_model_certification_replay_receipts from anon, authenticated;
revoke all on public.wow_fitted_model_promotion_receipts from anon, authenticated;
grant select, insert on public.wow_fitted_model_certification_replay_receipts to service_role;
grant select, insert on public.wow_fitted_model_promotion_receipts to service_role;
