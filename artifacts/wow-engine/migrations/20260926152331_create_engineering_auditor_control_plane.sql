-- WOW V17 Continuous Engineering Auditor control plane.
--
-- Engineering evidence only. These tables never store or publish sporting
-- probabilities and can never authorize wager or market-order execution.
-- Public-schema RLS is deliberately enabled with no user policies; anon and
-- authenticated are revoked, matching the existing service-role-only pattern.

create table if not exists public.wow_engineering_audit_work_items (
    work_item_id uuid primary key default gen_random_uuid(),
    fingerprint text not null unique,
    source_system text not null,
    repository text,
    source_kind text not null,
    source_ref text not null,
    title text not null,
    state text not null default 'OPEN',
    source_status text not null default 'OPEN',
    severity text not null default 'P3',
    draft boolean not null default false,
    head_sha text,
    labels text[] not null default '{}'::text[],
    state_payload jsonb not null default '{}'::jsonb,
    last_meaningful_progress_at timestamptz not null default now(),
    expected_next_action text,
    next_audit_at timestamptz not null,
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    closed_at timestamptz,
    terminal_state text,
    can_execute boolean not null default false,
    terminal_authority text not null default 'V17_TERMINAL_REDUCER',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint wow_engineering_audit_work_items_source_system check (source_system in ('GITHUB','SUPABASE','CODE_HEALTH')),
    constraint wow_engineering_audit_work_items_source_kind check (source_kind in ('GITHUB_ISSUE','GITHUB_PR','ENGINEERING_BACKLOG','CODE_HEALTH_RUN')),
    constraint wow_engineering_audit_work_items_state check (state in ('OPEN','TERMINAL')),
    constraint wow_engineering_audit_work_items_severity check (severity in ('P0','P1','P2','P3','P4')),
    constraint wow_engineering_audit_work_items_payload_object check (jsonb_typeof(state_payload) = 'object'),
    constraint wow_engineering_audit_work_items_never_execute check (can_execute = false),
    constraint wow_engineering_audit_work_items_terminal_authority check (terminal_authority = 'V17_TERMINAL_REDUCER')
);

alter table public.wow_engineering_audit_work_items enable row level security;
revoke all on table public.wow_engineering_audit_work_items from anon, authenticated;
grant select, insert, update, delete on table public.wow_engineering_audit_work_items to service_role;

create index if not exists wow_engineering_audit_work_items_due_idx
    on public.wow_engineering_audit_work_items (next_audit_at)
    where state = 'OPEN';
create index if not exists wow_engineering_audit_work_items_source_idx
    on public.wow_engineering_audit_work_items (source_system, source_kind, source_ref);

create table if not exists public.wow_engineering_audit_findings (
    finding_id uuid primary key default gen_random_uuid(),
    fingerprint text not null unique,
    finding_type text not null,
    work_item_id uuid references public.wow_engineering_audit_work_items(work_item_id) on delete set null,
    severity text not null default 'P3',
    component text not null,
    source_ref text,
    status text not null default 'OPEN',
    evidence jsonb not null default '{}'::jsonb,
    first_detected_at timestamptz not null default now(),
    last_verified_at timestamptz not null default now(),
    resolved_at timestamptz,
    resolution text,
    terminal_status text,
    can_execute boolean not null default false,
    terminal_authority text not null default 'V17_TERMINAL_REDUCER',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint wow_engineering_audit_findings_type check (finding_type in ('STALE_WORK','CODE_HEALTH','GOVERNANCE_DRIFT','AUDITOR_HEALTH')),
    constraint wow_engineering_audit_findings_severity check (severity in ('P0','P1','P2','P3','P4')),
    constraint wow_engineering_audit_findings_status check (status in ('OPEN','RESOLVED','DUPLICATE','DEFERRED')),
    constraint wow_engineering_audit_findings_evidence_object check (jsonb_typeof(evidence) = 'object'),
    constraint wow_engineering_audit_findings_never_execute check (can_execute = false),
    constraint wow_engineering_audit_findings_terminal_authority check (terminal_authority = 'V17_TERMINAL_REDUCER')
);

alter table public.wow_engineering_audit_findings enable row level security;
revoke all on table public.wow_engineering_audit_findings from anon, authenticated;
grant select, insert, update, delete on table public.wow_engineering_audit_findings to service_role;

create index if not exists wow_engineering_audit_findings_open_idx
    on public.wow_engineering_audit_findings (severity, first_detected_at)
    where status = 'OPEN';
create index if not exists wow_engineering_audit_findings_work_item_idx
    on public.wow_engineering_audit_findings (work_item_id, finding_type);

create table if not exists public.wow_engineering_auditor_runtime (
    auditor_id text primary key,
    instance_id text,
    status text not null default 'STARTING',
    started_at timestamptz,
    last_heartbeat_at timestamptz,
    last_event_processed_at timestamptz,
    last_reconcile_at timestamptz,
    queue_depth integer not null default 0,
    open_finding_count integer not null default 0,
    overdue_work_item_count integer not null default 0,
    last_error_code text,
    can_execute boolean not null default false,
    terminal_authority text not null default 'V17_TERMINAL_REDUCER',
    updated_at timestamptz not null default now(),
    constraint wow_engineering_auditor_runtime_status check (status in ('STARTING','RUNNING','DEGRADED','STOPPED')),
    constraint wow_engineering_auditor_runtime_counts_nonneg check (queue_depth >= 0 and open_finding_count >= 0 and overdue_work_item_count >= 0),
    constraint wow_engineering_auditor_runtime_never_execute check (can_execute = false),
    constraint wow_engineering_auditor_runtime_terminal_authority check (terminal_authority = 'V17_TERMINAL_REDUCER')
);

alter table public.wow_engineering_auditor_runtime enable row level security;
revoke all on table public.wow_engineering_auditor_runtime from anon, authenticated;
grant select, insert, update, delete on table public.wow_engineering_auditor_runtime to service_role;

insert into public.wow_engineering_auditor_runtime
    (auditor_id, status, can_execute, terminal_authority)
values
    ('WOW_ENGINEERING_AUDITOR', 'STOPPED', false, 'V17_TERMINAL_REDUCER')
on conflict (auditor_id) do nothing;
