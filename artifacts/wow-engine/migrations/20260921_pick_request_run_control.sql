-- V17 exact-once large-board run control and durable resume payloads.
-- Orchestration only: no sporting probability or execution authority is added.

alter table public.wow_pick_request_runs
    add column if not exists closure_code text,
    add column if not exists closure_reason text,
    add column if not exists closure_metadata jsonb not null default '{}'::jsonb,
    add column if not exists closed_at timestamptz,
    add column if not exists reopen_allowed boolean not null default false,
    add column if not exists run_control_version text;

alter table public.wow_pick_request_row_states
    add column if not exists identity_version text not null default 'V1_RAW',
    add column if not exists request_payload jsonb;

create index if not exists wow_pick_request_runs_status_idx
    on public.wow_pick_request_runs(run_status, updated_at desc);

comment on column public.wow_pick_request_runs.closure_code is
    'Typed governed run-closure code. Closed runs cannot silently resume.';
comment on column public.wow_pick_request_runs.closure_metadata is
    'Immutable closure context such as source-candidate and reconciled counts; never execution authority.';
comment on column public.wow_pick_request_row_states.identity_version is
    'Identity normalization version. V2_SEMANTIC_UTC normalizes equivalent timestamps/stat aliases before exact-once comparison.';
comment on column public.wow_pick_request_row_states.request_payload is
    'Frozen canonical PickRequestRow payload used only to resume the same exact row after transport/session loss.';
