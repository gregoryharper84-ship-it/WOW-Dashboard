-- V17 durable, append-only engineering attempt receipts (Charter §8).
--
-- One row per engineering attempt (provider/role run) against an incident.
-- Rows are immutable: UPDATE, DELETE and TRUNCATE fail closed for every role,
-- including service_role. Corrections are new rows that reference the prior
-- receipt via supersedes_receipt_id. CI artifacts and overwritten heartbeats
-- are not durable evidence; this table is.
--
-- Not a probability, publication, QA, release or execution authority.
-- can_execute is permanently false.

create table if not exists public.wow_engineering_attempt_receipts (
  receipt_id            uuid primary key default gen_random_uuid(),
  recorded_at           timestamptz not null default now(),
  incident_id           text not null check (length(incident_id) between 1 and 200),
  provider              text not null check (provider in ('openai', 'claude', 'human_owner', 'automation')),
  role                  text not null check (role in (
                          'implementer', 'adversarial_reviewer', 'sirt_challenger',
                          'release_preflight', 'research_challenger',
                          'independent_qa', 'release_authority', 'conductor')),
  worker_run_id         text not null,
  lease_id              text,
  lease_epoch           bigint check (lease_epoch is null or lease_epoch >= 0),
  priority              text check (priority in ('P0', 'P1', 'P2', 'P3')),
  change_class          text check (change_class in ('A', 'B', 'C')),
  pr_number             integer check (pr_number is null or pr_number > 0),
  base_sha              text check (base_sha is null or base_sha ~ '^[0-9a-f]{40}$'),
  head_sha              text check (head_sha is null or head_sha ~ '^[0-9a-f]{40}$'),
  deployed_sha          text check (deployed_sha is null or deployed_sha ~ '^[0-9a-f]{40}$'),
  root_cause            text,
  tests                 jsonb not null default '[]'::jsonb check (jsonb_typeof(tests) = 'array'),
  qa_decision           text check (qa_decision in ('PASS', 'REJECT', 'HOLD')),
  release_decision      text check (release_decision in ('READY', 'HOLD', 'REJECT', 'DEPLOYED')),
  disposition           text not null check (disposition in (
                          'IN_PROGRESS',
                          'FIXED_AND_VERIFIED', 'PR_CREATED', 'EXPERIMENT_CREATED',
                          'DUPLICATE', 'NOT_REPRODUCIBLE',
                          'BLOCKED_WITH_EXACT_REASON', 'DEFERRED_WITH_JUSTIFICATION')),
  typed_blocker         text,
  blocker_owner         text,
  retry_count           integer not null default 0 check (retry_count >= 0),
  next_action           text,
  revisit_trigger       text,
  supersedes_receipt_id uuid references public.wow_engineering_attempt_receipts (receipt_id),
  can_execute           boolean not null default false check (can_execute = false),
  -- A blocked attempt must name its exact reason, owner and revisit trigger.
  constraint wow_eng_receipt_blocked_is_typed check (
    disposition <> 'BLOCKED_WITH_EXACT_REASON'
    or (typed_blocker is not null and blocker_owner is not null and revisit_trigger is not null)
  ),
  -- FIXED_AND_VERIFIED requires an observed deployed SHA and independent QA PASS.
  constraint wow_eng_receipt_verified_has_evidence check (
    disposition <> 'FIXED_AND_VERIFIED'
    -- IS NOT DISTINCT FROM: a NULL qa_decision must fail, not pass as unknown.
    or (deployed_sha is not null and qa_decision is not distinct from 'PASS')
  ),
  -- PR_CREATED requires the exact PR and head SHA.
  constraint wow_eng_receipt_pr_has_identity check (
    disposition <> 'PR_CREATED' or (pr_number is not null and head_sha is not null)
  )
);

create index if not exists wow_eng_attempt_receipts_incident_idx
  on public.wow_engineering_attempt_receipts (incident_id, recorded_at desc);
create index if not exists wow_eng_attempt_receipts_open_idx
  on public.wow_engineering_attempt_receipts (disposition, recorded_at desc)
  where disposition in ('IN_PROGRESS', 'BLOCKED_WITH_EXACT_REASON');

alter table public.wow_engineering_attempt_receipts enable row level security;

create or replace function public.wow_engineering_attempt_receipts_append_only_guard()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  raise exception using
    errcode = '55000',
    message = 'WOW_ENGINEERING_ATTEMPT_RECEIPT_APPEND_ONLY';
end;
$$;

revoke all on function public.wow_engineering_attempt_receipts_append_only_guard()
  from public, anon, authenticated;

drop trigger if exists wow_eng_attempt_receipts_no_update_delete
  on public.wow_engineering_attempt_receipts;
create trigger wow_eng_attempt_receipts_no_update_delete
before update or delete on public.wow_engineering_attempt_receipts
for each row execute function public.wow_engineering_attempt_receipts_append_only_guard();

drop trigger if exists wow_eng_attempt_receipts_no_truncate
  on public.wow_engineering_attempt_receipts;
create trigger wow_eng_attempt_receipts_no_truncate
before truncate on public.wow_engineering_attempt_receipts
for each statement execute function public.wow_engineering_attempt_receipts_append_only_guard();

-- Least privilege: server-side insert/read only; no client-role access.
revoke all privileges on table public.wow_engineering_attempt_receipts
  from public, anon, authenticated, service_role;
grant select, insert on table public.wow_engineering_attempt_receipts to service_role;

comment on table public.wow_engineering_attempt_receipts is
  'Append-only V17 engineering attempt receipts (Charter §8). UPDATE/DELETE/TRUNCATE fail closed for all roles; corrections are new rows via supersedes_receipt_id. Not a probability, QA, release or execution authority; can_execute is permanently false.';

-- Hygiene: wow_engineering_backlog is service-role only (RLS, no client
-- policies) but still carried full client-role table grants. Remove them.
revoke all privileges on table public.wow_engineering_backlog from anon, authenticated;
