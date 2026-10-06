begin;

create table if not exists public.wow_v17_reliability_receipts (
    sentinel_signature text primary key
        check (sentinel_signature ~ '^sha256:[0-9a-f]{64}$'),
    contract_version text not null
        check (contract_version = 'WOW_ENGINEERING_RELIABILITY_V1'),
    issue_id bigint not null check (issue_id > 0),
    pr_number bigint not null check (pr_number > 0),
    exact_head_sha text not null check (exact_head_sha ~ '^[0-9a-f]{40}$'),
    merge_sha text not null check (merge_sha ~ '^[0-9a-f]{40}$'),
    deployed_render_sha text not null check (deployed_render_sha ~ '^[0-9a-f]{40}$'),
    method text not null check (method in ('GET', 'POST')),
    route_tested text not null,
    schema_hash text not null check (schema_hash ~ '^[0-9a-f]{64}$'),
    http_status integer not null check (http_status between 100 and 599),
    dry_run_header_present boolean not null
        check (dry_run_header_present is true),
    can_execute_header_false boolean not null
        check (can_execute_header_false is true),
    raw_response_digest text not null
        check (raw_response_digest ~ '^[0-9a-f]{64}$'),
    execution_trace_digest text not null
        check (execution_trace_digest ~ '^[0-9a-f]{64}$'),
    sentinel_workflow text not null,
    sentinel_workflow_run_id bigint not null check (sentinel_workflow_run_id > 0),
    audit_artifact_name text not null,
    verified_at timestamptz not null,
    receipt_json jsonb not null,
    raw_response_body_base64 text not null,
    raw_response_headers_base64 text not null,
    execution_trace_base64 text not null,
    can_execute boolean not null default false
        check (can_execute is false),
    created_at timestamptz not null default now(),
    unique (issue_id, pr_number, merge_sha, raw_response_digest)
);

create or replace function public.wow_reject_reliability_receipt_mutation()
returns trigger
language plpgsql
as $$
begin
    raise exception 'WOW_RELIABILITY_RECEIPTS_APPEND_ONLY';
end;
$$;

drop trigger if exists wow_v17_reliability_receipts_append_only
on public.wow_v17_reliability_receipts;

create trigger wow_v17_reliability_receipts_append_only
before update or delete on public.wow_v17_reliability_receipts
for each row execute function public.wow_reject_reliability_receipt_mutation();

alter table public.wow_v17_reliability_receipts enable row level security;
revoke update, delete on public.wow_v17_reliability_receipts from anon, authenticated;

commit;
