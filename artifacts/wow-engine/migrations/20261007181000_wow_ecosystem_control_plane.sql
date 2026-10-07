-- WOW Ecosystem Conductor durable Class A control-plane ledger.
-- Stores observability, handoff, lifecycle, readiness, and reliability evidence only.
-- It never stores credentials and never grants sporting/weather probability or execution authority.

create table if not exists public.wow_ecosystem_probe_receipts (
    receipt_id uuid primary key default gen_random_uuid(),
    probe_id text not null,
    probe_type text not null,
    observed_at timestamptz not null,
    status text not null check (status in ('PASS','DEGRADED','FAIL','BLOCKED','UNKNOWN','NOT_APPLICABLE')),
    evidence_refs jsonb not null default '[]'::jsonb,
    first_failing_boundary text,
    typed_failure text,
    source_version text,
    deployed_sha text,
    details jsonb not null default '{}'::jsonb,
    can_execute boolean not null default false check (can_execute = false),
    created_at timestamptz not null default now(),
    constraint wow_ecosystem_probe_receipts_identity_uq
        unique (probe_id, observed_at, status, coalesce(source_version,''), coalesce(deployed_sha,''))
);

create index if not exists wow_ecosystem_probe_receipts_recent_idx
    on public.wow_ecosystem_probe_receipts (observed_at desc, probe_id);
create index if not exists wow_ecosystem_probe_receipts_failure_idx
    on public.wow_ecosystem_probe_receipts (status, first_failing_boundary, typed_failure, observed_at desc);

create table if not exists public.wow_ecosystem_handoff_receipts (
    handoff_receipt_id uuid primary key default gen_random_uuid(),
    handoff_id text not null,
    source text not null,
    target text not null,
    observed_at timestamptz not null,
    status text not null check (status in ('PASS','DEGRADED','FAIL','BLOCKED','UNKNOWN','NOT_APPLICABLE')),
    request_id text,
    objective_id text,
    work_item_id text,
    evidence_refs jsonb not null default '[]'::jsonb,
    first_failing_boundary text,
    typed_failure text,
    source_version text,
    deployed_sha text,
    can_execute boolean not null default false check (can_execute = false),
    created_at timestamptz not null default now()
);

create index if not exists wow_ecosystem_handoff_receipts_recent_idx
    on public.wow_ecosystem_handoff_receipts (observed_at desc, handoff_id);
create index if not exists wow_ecosystem_handoff_receipts_work_idx
    on public.wow_ecosystem_handoff_receipts (work_item_id, observed_at desc);
create index if not exists wow_ecosystem_handoff_receipts_failure_idx
    on public.wow_ecosystem_handoff_receipts (status, first_failing_boundary, typed_failure, observed_at desc);

create table if not exists public.wow_ecosystem_work_items (
    work_item_id text primary key,
    request_id text not null,
    objective_id text not null,
    candidate_id text,
    source text not null,
    current_owner text not null,
    next_owner text,
    state text not null check (state in ('ADMITTED','ROUTED','IN_PROGRESS','BLOCKED','TERMINATED')),
    blocking_reason text,
    evidence_refs jsonb not null default '[]'::jsonb,
    specialist_route text,
    verification_state text not null check (verification_state in ('NONE','NOT_REQUIRED','PENDING','VERIFIED','REJECTED')),
    terminal_state text,
    authority_domain text not null,
    change_class text not null check (change_class in ('A','B','C','NONE')),
    decision_right text not null,
    required_verifier text not null,
    promotion_state text not null check (promotion_state in ('NOT_APPLICABLE','NOT_REQUESTED','PENDING','VERIFIED','REJECTED','APPROVED','PROMOTED')),
    first_admitted_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    terminal_at timestamptz,
    can_execute boolean not null default false check (can_execute = false),
    constraint wow_ecosystem_work_item_blocker_ck
        check (
            (state = 'BLOCKED' and blocking_reason is not null and length(btrim(blocking_reason)) > 0)
            or
            (state <> 'BLOCKED' and blocking_reason is null)
        ),
    constraint wow_ecosystem_work_item_terminal_ck
        check (
            (state = 'TERMINATED' and terminal_state is not null and next_owner is null)
            or
            (state <> 'TERMINATED' and terminal_state is null)
        )
);

create index if not exists wow_ecosystem_work_items_state_idx
    on public.wow_ecosystem_work_items (state, current_owner, updated_at desc);
create index if not exists wow_ecosystem_work_items_objective_idx
    on public.wow_ecosystem_work_items (objective_id, request_id, updated_at desc);

create table if not exists public.wow_ecosystem_readiness_snapshots (
    snapshot_id uuid primary key default gen_random_uuid(),
    observed_at timestamptz not null,
    ecosystem_status text not null check (ecosystem_status in ('READY','DEGRADED','SAFE_HOLD')),
    safe_hold_required boolean not null,
    false_green_detected boolean not null,
    component_states jsonb not null,
    handoff_states jsonb not null,
    golden_paths jsonb not null,
    capability_matrix jsonb not null default '[]'::jsonb,
    metrics jsonb not null default '{}'::jsonb,
    evidence_refs jsonb not null default '[]'::jsonb,
    terminal_authority text not null default 'V17_TERMINAL_REDUCER'
        check (terminal_authority = 'V17_TERMINAL_REDUCER'),
    can_execute boolean not null default false check (can_execute = false),
    created_at timestamptz not null default now()
);

create index if not exists wow_ecosystem_readiness_snapshots_recent_idx
    on public.wow_ecosystem_readiness_snapshots (observed_at desc);

create table if not exists public.wow_ecosystem_findings (
    finding_id text primary key,
    finding_type text not null check (finding_type in ('INCIDENT','OPPORTUNITY')),
    fingerprint text not null,
    severity text not null check (severity in ('P0','P1','P2','P3')),
    status text not null check (status in ('OPEN','BLOCKED','VERIFIED_CLOSED','SUPERSEDED','DEFERRED')),
    first_detected_at timestamptz not null,
    last_observed_at timestamptz not null,
    occurrence_count integer not null check (occurrence_count >= 1),
    affected_paths jsonb not null default '[]'::jsonb,
    first_failing_boundary text,
    typed_failure text,
    diagnostic_owner text not null,
    closure_owner text not null,
    change_class text not null check (change_class in ('A','B','C','NONE')),
    evidence_refs jsonb not null default '[]'::jsonb,
    acceptance_criteria jsonb not null default '[]'::jsonb,
    root_cause_proven_at timestamptz,
    verified_closed_at timestamptz,
    can_execute boolean not null default false check (can_execute = false),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists wow_ecosystem_findings_fingerprint_idx
    on public.wow_ecosystem_findings (fingerprint, status, last_observed_at desc);
create index if not exists wow_ecosystem_findings_priority_idx
    on public.wow_ecosystem_findings (status, severity, last_observed_at desc);

alter table public.wow_ecosystem_probe_receipts enable row level security;
alter table public.wow_ecosystem_handoff_receipts enable row level security;
alter table public.wow_ecosystem_work_items enable row level security;
alter table public.wow_ecosystem_readiness_snapshots enable row level security;
alter table public.wow_ecosystem_findings enable row level security;

revoke all on table public.wow_ecosystem_probe_receipts from public, anon, authenticated;
revoke all on table public.wow_ecosystem_handoff_receipts from public, anon, authenticated;
revoke all on table public.wow_ecosystem_work_items from public, anon, authenticated;
revoke all on table public.wow_ecosystem_readiness_snapshots from public, anon, authenticated;
revoke all on table public.wow_ecosystem_findings from public, anon, authenticated;

grant select, insert on table public.wow_ecosystem_probe_receipts to service_role;
grant select, insert on table public.wow_ecosystem_handoff_receipts to service_role;
grant select, insert, update on table public.wow_ecosystem_work_items to service_role;
grant select, insert on table public.wow_ecosystem_readiness_snapshots to service_role;
grant select, insert, update on table public.wow_ecosystem_findings to service_role;

comment on table public.wow_ecosystem_probe_receipts is
'Class A read-only-observation receipts for the WOW Ecosystem Conductor. No probability or execution authority.';
comment on table public.wow_ecosystem_handoff_receipts is
'Durable cross-system handoff evidence for the WOW Ecosystem Conductor. Missing proof remains fail-closed.';
comment on table public.wow_ecosystem_work_items is
'Current durable ownership state for ecosystem work-conservation. Engineering may not self-verify.';
comment on table public.wow_ecosystem_readiness_snapshots is
'Immutable ecosystem readiness snapshots separating component, handoff, capability, verification, and user readiness.';
comment on table public.wow_ecosystem_findings is
'Evidence-backed incident/opportunity findings emitted by Systems Intelligence routing. No automatic probability/model promotion.';
