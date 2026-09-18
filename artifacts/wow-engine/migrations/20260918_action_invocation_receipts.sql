-- V17 Action invocation ledger (WOW-RUNTIME-ACTION-CANARY-NEVER-VERIFIED-011, P0-A).
--
-- PURPOSE AND NON-PURPOSE
-- This table answers exactly one question: "did something call a canonical WOW
-- Action route, from where, and what did it get back?" It is invocation
-- telemetry, not certification evidence.
--
-- It exists because wow_prop_action_canary_receipts was being read as if it
-- were invocation telemetry. That table is correctly gated behind an
-- independently reviewed certification release, so it stays empty until a
-- release exists no matter how many Action calls succeed. Reading emptiness
-- there as "never invoked" produced a false P0 root cause.
--
-- A row here therefore NEVER implies certification, publication eligibility,
-- model success, or any capability whatsoever. Rows are written for failed
-- calls (401/404/422/409/500) exactly as readily as for successful ones --
-- that is the point. can_execute is permanently false.
--
-- No credential, bearer token, API key, request body, player, line, market
-- price or probability is recorded here. Only the request envelope.
create table if not exists public.wow_action_invocation_receipts (
    invocation_id uuid primary key default gen_random_uuid(),
    occurred_at timestamptz not null default now(),
    route text not null,
    action_operation_id text not null,
    http_method text not null,
    http_status integer not null check (http_status between 100 and 599),
    -- How the caller authenticated, by SCHEME ONLY. Never the credential.
    auth_scheme text not null default 'NONE'
        check (auth_scheme in ('NONE','BEARER','BASIC','OTHER')),
    -- Which class of host called. This is observability metadata only, not an
    -- authorization or certification claim. ACTION_API_KEY means an authenticated
    -- bearer-key caller whose transport identity was not independently attributable
    -- to ChatGPT; it must not be promoted to CHATGPT_ACTION by inference.
    caller_class text not null default 'UNKNOWN'
        check (caller_class in ('CHATGPT_ACTION','ACTION_API_KEY','GITHUB_ACTIONS','RENDER_INTERNAL','SELF_ACCEPTANCE','OTHER','UNKNOWN')),
    caller_user_agent text,
    request_id text,
    rows_in integer check (rows_in is null or rows_in >= 0),
    duration_ms numeric check (duration_ms is null or duration_ms >= 0),
    can_execute boolean not null default false check (can_execute = false),
    constraint wow_action_invocation_route_ck
        check (route in ('/score-prop','/score-pick-request','/score-team-event-request','/score-team-event'))
);
create index if not exists wow_action_invocation_recent_idx
    on public.wow_action_invocation_receipts (route, occurred_at desc);
create index if not exists wow_action_invocation_caller_idx
    on public.wow_action_invocation_receipts (caller_class, occurred_at desc);
alter table public.wow_action_invocation_receipts enable row level security;
revoke all on table public.wow_action_invocation_receipts from public, anon, authenticated;
grant select, insert on table public.wow_action_invocation_receipts to service_role;
comment on table public.wow_action_invocation_receipts is
'Certification-independent invocation telemetry for canonical WOW Action routes. Records that a call happened, from which caller class, and with what HTTP result -- including failures. Presence of a row never implies certification, publication eligibility, model success, or execution authority; can_execute is permanently false. Distinct from wow_prop_action_canary_receipts, which is reviewed-certification proof and is intentionally empty until a release exists.';
