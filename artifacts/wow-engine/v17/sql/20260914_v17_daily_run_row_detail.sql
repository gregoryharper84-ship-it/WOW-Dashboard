-- WOW V17 Daily run row-detail store.
--
-- Repository source only; do not apply to production until independently
-- reviewed. can_execute remains false.
--
-- Purpose: the V17 Daily response previously inlined the full governed package
-- for every scored row (prediction, evidence ledger, acquisition packet, model
-- artifact metadata, numerical-engine output, objective lanes, backend
-- traversal). A normal client flow failed with ResponseTooLargeError before the
-- requested row count could be reached. This table holds that full evidence so
-- the Daily response can stay compact while the evidence is read back through
-- the paged retrieval route.
--
-- This store is a transport relocation, not a new authority. It never holds a
-- publication decision, never grants execution, and never replaces the governed
-- prediction/evidence ledgers that remain the systems of record.

create table if not exists public.wow_v17_daily_run_row_detail (
    run_id text not null,
    row_index integer not null,
    lane text not null,
    row_status text,
    identity jsonb not null default '{}'::jsonb,
    detail jsonb not null default '{}'::jsonb,
    captured_at timestamptz not null default now(),
    can_execute boolean not null default false,
    constraint wow_v17_daily_run_row_detail_pkey primary key (run_id, row_index),
    constraint wow_v17_daily_run_row_detail_row_index_nonneg check (row_index >= 0),
    constraint wow_v17_daily_run_row_detail_lane check (lane in ('PROPS','MONEYLINE')),
    constraint wow_v17_daily_run_row_detail_identity_object check (jsonb_typeof(identity) = 'object'),
    constraint wow_v17_daily_run_row_detail_detail_object check (jsonb_typeof(detail) = 'object'),
    constraint wow_v17_daily_run_row_detail_never_execute check (can_execute = false)
);

alter table public.wow_v17_daily_run_row_detail enable row level security;

create index if not exists wow_v17_daily_run_row_detail_run_idx
    on public.wow_v17_daily_run_row_detail (run_id, row_index);

create index if not exists wow_v17_daily_run_row_detail_captured_idx
    on public.wow_v17_daily_run_row_detail (captured_at desc);

comment on table public.wow_v17_daily_run_row_detail is
'Full per-row evidence for one V17 Daily run, written server-side so the Daily response can stay compact and the evidence can be read back in bounded pages. Not a publication authority; can_execute is always false.';

-- Least privilege: the governed server-side/service-role path writes and reads.
-- No client role receives mutation authority, and no RLS policy is created for
-- anon/authenticated, so those roles remain fully denied by default.
revoke all on table public.wow_v17_daily_run_row_detail from anon, authenticated;
