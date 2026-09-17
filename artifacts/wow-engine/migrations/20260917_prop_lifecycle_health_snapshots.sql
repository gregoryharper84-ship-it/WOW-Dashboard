-- V17 continuous exact-route prop lifecycle health ledger.
-- This is observability/control-plane state only. It cannot certify, promote,
-- publish, or authorize execution. Rows are service-role-only and immutable by
-- ordinary clients. can_execute is permanently false.

create table if not exists public.wow_prop_lifecycle_health_snapshots (
    snapshot_id uuid primary key default gen_random_uuid(),
    cycle_id uuid not null,
    captured_at timestamptz not null,
    health_key text not null,
    row_kind text not null check (row_kind in ('ROUTE_STATUS', 'ARTIFACT_COHORT')),
    sport text not null,
    stat_type text not null,
    model_family text,
    model_artifact_version text,
    artifact_checksum text,
    calibrator_version text,
    forward_prediction_n integer check (forward_prediction_n is null or forward_prediction_n >= 0),
    forward_settled_n integer check (forward_settled_n is null or forward_settled_n >= 0),
    eligible_n integer check (eligible_n is null or eligible_n >= 0),
    calibration_status text,
    policy_review_status text,
    lifecycle_status text not null,
    action_canary_verified boolean not null default false,
    blockers jsonb not null default '[]'::jsonb,
    payload jsonb not null default '{}'::jsonb,
    can_execute boolean not null default false check (can_execute = false),
    constraint wow_prop_lifecycle_health_cycle_key_uq unique (cycle_id, health_key)
);

create index if not exists wow_prop_lifecycle_health_route_time_idx
    on public.wow_prop_lifecycle_health_snapshots (
        upper(sport), upper(stat_type), captured_at desc
    );

create index if not exists wow_prop_lifecycle_health_artifact_time_idx
    on public.wow_prop_lifecycle_health_snapshots (
        model_artifact_version, artifact_checksum, captured_at desc
    )
    where row_kind = 'ARTIFACT_COHORT';

alter table public.wow_prop_lifecycle_health_snapshots enable row level security;
revoke all on table public.wow_prop_lifecycle_health_snapshots from public, anon, authenticated;
grant select, insert, update on table public.wow_prop_lifecycle_health_snapshots to service_role;

comment on table public.wow_prop_lifecycle_health_snapshots is
'Append-only-by-cycle V17 prop lifecycle health snapshots: exact route/artifact forward N, settled N, calibration/certification readiness, runtime registration, Action canary and typed blockers. It never grants capability and can_execute is permanently false.';
