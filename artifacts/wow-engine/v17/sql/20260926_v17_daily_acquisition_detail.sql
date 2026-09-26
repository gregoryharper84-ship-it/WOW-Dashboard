-- WOW V17 target-level Daily acquisition audit store.
--
-- Additive/reversible source migration. Apply only through governed Supabase
-- release review. This table is independent of event and prediction rows: a
-- provider failure cannot erase a valid sporting probability, and a valid
-- probability cannot conceal an incomplete acquisition inventory.
--
-- The server writes only bounded status metadata. Raw provider payloads,
-- prices, probabilities, participant names, and PII are prohibited by design.

create table if not exists public.wow_v17_daily_run_acquisition_detail (
    run_id text not null,
    family text not null,
    target_key text not null,
    provider text,
    league text,
    regime text,
    provider_sport_id integer,
    sport_key text,
    final_state text not null,
    provider_status text not null,
    fallback_status text not null,
    exhaustion_status text not null,
    events_returned integer not null default 0,
    duplicate_rows_suppressed integer not null default 0,
    blocker_code text,
    captured_at timestamptz not null default now(),
    can_execute boolean not null default false,
    constraint wow_v17_daily_run_acquisition_detail_pkey
        primary key (run_id, family, target_key),
    constraint wow_v17_daily_run_acquisition_detail_counts_nonnegative check (
        events_returned >= 0 and duplicate_rows_suppressed >= 0
    ),
    constraint wow_v17_daily_run_acquisition_detail_never_execute check (
        can_execute = false
    )
);

alter table public.wow_v17_daily_run_acquisition_detail enable row level security;

create index if not exists wow_v17_daily_run_acquisition_detail_run_idx
    on public.wow_v17_daily_run_acquisition_detail (run_id, family, target_key);

create index if not exists wow_v17_daily_run_acquisition_detail_captured_idx
    on public.wow_v17_daily_run_acquisition_detail (captured_at desc);

comment on table public.wow_v17_daily_run_acquisition_detail is
'Sanitized final acquisition state for every configured V17 Daily discovery target. Server-only audit evidence; never a probability, publication, or execution authority.';

-- RLS is fail-closed: no anon/authenticated policy is created. Governed
-- service-role server paths retain access; client roles receive none.
revoke all on table public.wow_v17_daily_run_acquisition_detail from anon, authenticated;

-- Deterministic rollback (only after release verification confirms no required
-- retained audit evidence):
-- drop table if exists public.wow_v17_daily_run_acquisition_detail;
