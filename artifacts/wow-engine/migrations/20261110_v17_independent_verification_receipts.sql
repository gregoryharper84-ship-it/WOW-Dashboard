-- V17 append-only independent verification receipt ledger.
--
-- One immutable row per independent verification attempt against a fitted
-- team-event artifact. This table is an EVIDENCE LEDGER, not a certification,
-- promotion, publication, ranking or execution authority:
--
--   * `publishable_scope` is permanently 'NONE';
--   * `can_execute` is permanently false;
--   * UPDATE / DELETE / TRUNCATE fail closed for every role, including
--     service_role. A re-verification is a new row, never a mutation.
--
-- It is deliberately separate from
-- public.wow_team_event_specialist_certifications: that table's
-- certification_status is limited to CERTIFIED/REVOKED, so recording a FAILed
-- or not-yet-certified verification there would either be impossible or would
-- misrepresent a governance decision. Certification continues to be a separate
-- governed act performed only after this receipt exists and PASSes.

create table if not exists public.wow_independent_verification_receipts (
    receipt_id                 uuid primary key,
    recorded_at                timestamptz not null default now(),
    receipt_digest             text not null unique check (receipt_digest ~ '^[0-9a-f]{64}$'),
    schema_version             text not null,
    harness                    text not null,
    code_sha                   text not null check (length(code_sha) between 1 and 200),
    environment                text,
    sport                      text not null check (sport in ('NBA')),
    league_scope               text not null,
    model_family               text not null,
    model_version              text not null,
    model_artifact_id          text not null,
    model_artifact_sha256      text not null check (model_artifact_sha256 ~ '^[0-9a-f]{64}$'),
    calibration_artifact_id    text not null,
    calibration_sha256         text not null check (calibration_sha256 ~ '^[0-9a-f]{64}$'),
    feature_schema_version     text not null,
    input_contract_version     text not null,
    independent_verifier       text not null,
    implementation_owner       text not null,
    verification_owner         text not null,
    independent_verification_status text not null
        check (independent_verification_status in ('PASS','FAIL')),
    publishable_scope          text not null default 'NONE'
        check (publishable_scope = 'NONE'),
    certification_eligible     boolean not null default false,
    failed_checks              text[] not null default '{}'::text[],
    blockers                   text[] not null default '{}'::text[],
    receipt                    jsonb not null check (jsonb_typeof(receipt) = 'object'),
    can_execute                boolean not null default false check (can_execute = false),
    -- A receipt is only evidence when the two owners are genuinely different.
    constraint wow_ivr_owner_independence check (implementation_owner <> verification_owner),
    -- PASS must be internally consistent: eligible, no FAILed check, no blocker.
    constraint wow_ivr_pass_is_clean check (
        independent_verification_status <> 'PASS'
        or (certification_eligible and cardinality(failed_checks) = 0 and cardinality(blockers) = 0)
    ),
    -- FAIL can never be certification eligible.
    constraint wow_ivr_fail_not_eligible check (
        independent_verification_status <> 'FAIL' or certification_eligible = false
    )
);

create index if not exists idx_wow_ivr_artifact
    on public.wow_independent_verification_receipts (
        sport, model_version, model_artifact_sha256, recorded_at desc
    );
create index if not exists idx_wow_ivr_status
    on public.wow_independent_verification_receipts (sport, independent_verification_status, recorded_at desc);

alter table public.wow_independent_verification_receipts enable row level security;

-- Least privilege: the engine's server-side role inserts and reads; no client.
revoke all privileges on table public.wow_independent_verification_receipts
    from public, anon, authenticated, service_role;
grant select, insert on table public.wow_independent_verification_receipts to service_role;

drop trigger if exists wow_independent_verification_receipts_immutable
    on public.wow_independent_verification_receipts;
create trigger wow_independent_verification_receipts_immutable
before update or delete on public.wow_independent_verification_receipts
for each row execute function public.wow_reject_immutable_mutation();

create or replace function public.wow_independent_verification_receipts_no_truncate()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  raise exception using
    errcode = '55000',
    message = 'WOW_INDEPENDENT_VERIFICATION_RECEIPT_APPEND_ONLY';
end;
$$;

revoke all on function public.wow_independent_verification_receipts_no_truncate()
    from public, anon, authenticated;

drop trigger if exists wow_independent_verification_receipts_no_truncate
    on public.wow_independent_verification_receipts;
create trigger wow_independent_verification_receipts_no_truncate
before truncate on public.wow_independent_verification_receipts
for each statement execute function public.wow_independent_verification_receipts_no_truncate();

comment on table public.wow_independent_verification_receipts is
'Append-only V17 independent verification receipts for fitted team-event artifacts. Evidence only: publishable_scope is NONE and can_execute is false for every row. UPDATE/DELETE/TRUNCATE fail closed; re-verification appends a new row. Not a certification or promotion authority.';

-- The independent harness cannot verify a calibrator it cannot identify. The
-- fitted-calibrator registry carried coefficients and metrics but no stable
-- digest and no calibration cutoff, so V7/V10 could never be satisfied from
-- real rows. Both columns are additive and nullable, so existing rows remain
-- readable and are correctly reported as incomplete evidence rather than
-- being backfilled with a value nobody can recompute.
alter table public.wow_basketball_team_event_calibrators
    add column if not exists calibration_sha256 text,
    add column if not exists calibration_fit_end timestamptz;

comment on column public.wow_basketball_team_event_calibrators.calibration_sha256 is
'Canonical SHA-256 of the calibration identity payload (sport, versions, method, coefficients, row count, metrics, fit_end). Recomputed independently during verification.';
comment on column public.wow_basketball_team_event_calibrators.calibration_fit_end is
'End of the calibration training window. Verification requires it to precede the immutable model registration timestamp.';
