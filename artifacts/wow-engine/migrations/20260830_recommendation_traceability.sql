-- Immutable cross-sport recommendation/display traceability.
-- This ledger records what WOW displayed without promoting research-only output
-- into governed probability calibration tables.

create table if not exists public.wow_recommendation_records (
    recommendation_record_id uuid primary key,
    created_at timestamptz not null default now(),
    recorded_at timestamptz not null default now(),
    idempotency_key text not null unique,
    research_run_id text not null,
    request_id text,
    host_identity text not null,
    model_identity text not null,
    source_type text not null,
    source_conversation_ref text,
    row_key text not null,
    sport text not null,
    league text,
    event_id text not null,
    event_start_time timestamptz not null,
    participant text not null,
    opponent text,
    market_family text not null,
    selection text not null,
    terminal_label text not null,
    probability_publishable boolean not null default false,
    model_probability numeric check (model_probability is null or (model_probability > 0 and model_probability < 1)),
    calibrated_probability numeric check (calibrated_probability is null or (calibrated_probability > 0 and calibrated_probability < 1)),
    calibrated_probability_lower_bound numeric check (calibrated_probability_lower_bound is null or (calibrated_probability_lower_bound > 0 and calibrated_probability_lower_bound < 1)),
    governed_prediction_table text,
    governed_prediction_id uuid,
    source_snapshot_id uuid,
    evidence_fingerprint text,
    blockers text[] not null default '{}',
    display_payload jsonb not null default '{}'::jsonb,
    capture_timing text not null default 'PREGAME'
        check (capture_timing in ('PREGAME','POST_EVENT_RETROACTIVE')),
    calibration_eligible boolean not null default false,
    can_execute boolean not null default false check (can_execute = false),
    constraint recommendation_calibration_provenance check (
        calibration_eligible = false or (
            capture_timing = 'PREGAME'
            and probability_publishable = true
            and governed_prediction_id is not null
        )
    )
);

create index if not exists idx_wow_recommendation_records_run
    on public.wow_recommendation_records (research_run_id, row_key);
create index if not exists idx_wow_recommendation_records_event
    on public.wow_recommendation_records (event_id, event_start_time);

create table if not exists public.wow_recommendation_outcomes (
    recommendation_outcome_id uuid primary key default gen_random_uuid(),
    recommendation_record_id uuid not null unique
        references public.wow_recommendation_records(recommendation_record_id),
    created_at timestamptz not null default now(),
    settled_at timestamptz not null,
    settled_result text not null check (settled_result in ('WIN','LOSS','PUSH','VOID')),
    official_result text,
    settlement_source text not null,
    settlement_evidence_ref text,
    position_reference text,
    position_structure text,
    underlying_market_count integer check (underlying_market_count is null or underlying_market_count > 0),
    entry_cost numeric check (entry_cost is null or entry_cost >= 0),
    payout numeric check (payout is null or payout >= 0),
    profit_loss numeric,
    displayed_roi numeric,
    attribution_status text not null default 'MATCHED_PREGAME_RECORD'
        check (attribution_status in ('MATCHED_PREGAME_RECORD','RETROSPECTIVE_UNVERIFIED')),
    excluded_from_calibration boolean not null default false,
    can_execute boolean not null default false check (can_execute = false),
    constraint retrospective_outcome_excluded check (
        attribution_status <> 'RETROSPECTIVE_UNVERIFIED' or excluded_from_calibration = true
    )
);

create index if not exists idx_wow_recommendation_outcomes_position
    on public.wow_recommendation_outcomes (position_reference);

create or replace function public.wow_block_recommendation_record_mutation()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    raise exception 'WOW recommendation records are immutable';
end;
$$;

drop trigger if exists trg_wow_recommendation_records_no_update on public.wow_recommendation_records;
create trigger trg_wow_recommendation_records_no_update
before update on public.wow_recommendation_records
for each row execute function public.wow_block_recommendation_record_mutation();

drop trigger if exists trg_wow_recommendation_records_no_delete on public.wow_recommendation_records;
create trigger trg_wow_recommendation_records_no_delete
before delete on public.wow_recommendation_records
for each row execute function public.wow_block_recommendation_record_mutation();

create or replace function public.wow_block_recommendation_outcome_mutation()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    raise exception 'WOW recommendation outcomes are immutable';
end;
$$;

drop trigger if exists trg_wow_recommendation_outcomes_no_update on public.wow_recommendation_outcomes;
create trigger trg_wow_recommendation_outcomes_no_update
before update on public.wow_recommendation_outcomes
for each row execute function public.wow_block_recommendation_outcome_mutation();

drop trigger if exists trg_wow_recommendation_outcomes_no_delete on public.wow_recommendation_outcomes;
create trigger trg_wow_recommendation_outcomes_no_delete
before delete on public.wow_recommendation_outcomes
for each row execute function public.wow_block_recommendation_outcome_mutation();

alter table public.wow_recommendation_records enable row level security;
alter table public.wow_recommendation_outcomes enable row level security;
revoke all on public.wow_recommendation_records from anon, authenticated;
revoke all on public.wow_recommendation_outcomes from anon, authenticated;

comment on table public.wow_recommendation_records is
'Immutable record of every WOW recommendation displayed across sports and terminal ceilings. Separate from governed calibration ledgers.';
comment on table public.wow_recommendation_outcomes is
'Immutable settlement evidence linked to the exact displayed WOW recommendation record.';
