-- WOW V17 governed exact-line spread receipt store.
--
-- Additive/reversible source migration. Apply only through governed Supabase
-- release review. This relation is the only place a certified spread model may
-- land an admitted pregame receipt: the Daily SPREAD lane reads it, and
-- v17.spread_card_admission re-verifies every row it receives against the
-- ledger-bound exact line at read time.
--
-- A row here is not a wager, an order, or an execution grant. can_execute is
-- pinned false, every probability is strictly interior, quote provenance is
-- mandatory (a synthetic or reconstructed line cannot satisfy it), and the row
-- is immutable after insert.
--
-- Read-time binding, not write-time: this table stores the quote a model was
-- scored against. Whether that quote still matches the append-only market
-- ledger for the event is decided by the reader, which fails closed.

create table if not exists public.wow_v17_spread_card_receipts (
    receipt_key text primary key,
    content_sha256 text not null,
    schema_version text not null default 'WOW_V17_SPREAD_CARD_RECEIPT_V1',
    sport text not null,
    official_event_id text not null,
    event_start_time_utc timestamptz not null,
    home_team text not null,
    away_team text not null,
    market_family text not null default 'POINT_SPREAD',
    exact_line_side text not null,
    exact_signed_spread numeric not null,
    settlement_basis text not null,
    -- Quote provenance: no observed book quote, no receipt.
    line_source text not null,
    quote_source_id text not null,
    quote_sportsbook text not null,
    quote_observed_at_utc timestamptz not null,
    provider_event_id text not null,
    -- Model provenance.
    controlling_spread_specialist text not null,
    model_artifact_version text not null,
    model_class text not null,
    model_version text not null,
    calibration_version text not null,
    -- Three-way exact-line distribution and its calibrated bounds.
    p_cover numeric not null,
    p_push numeric not null,
    p_not_cover numeric not null,
    calibrated_lower_bound numeric not null,
    calibrated_upper_bound numeric not null,
    -- Pregame timing and terminal provenance.
    model_timestamp timestamptz not null,
    source_snapshot_timestamp timestamptz not null,
    event_state text not null default 'SCHEDULED',
    score_snapshot_status text not null default 'PASS',
    source_receipt_id text not null,
    terminal_authority text not null default 'V17_TERMINAL_REDUCER',
    controlling_engine_identity text not null default 'LLP_TEAM_BETTING_ENGINE',
    admission_status text not null default 'PASS',
    probability_publishable boolean not null default true,
    rank_eligible boolean not null default true,
    created_at timestamptz not null default now(),
    can_execute boolean not null default false,
    constraint wow_v17_spread_card_receipts_identity unique (
        sport, official_event_id, exact_line_side, exact_signed_spread, model_timestamp
    ),
    constraint wow_v17_spread_card_receipts_schema check (
        schema_version = 'WOW_V17_SPREAD_CARD_RECEIPT_V1'
    ),
    constraint wow_v17_spread_card_receipts_sport check (
        sport in ('NFL', 'NBA', 'WNBA', 'NCAAF', 'NCAAB')
    ),
    constraint wow_v17_spread_card_receipts_market check (
        market_family = 'POINT_SPREAD'
    ),
    constraint wow_v17_spread_card_receipts_side check (
        exact_line_side in ('HOME', 'AWAY')
    ),
    constraint wow_v17_spread_card_receipts_line_bounds check (
        exact_signed_spread > -100 and exact_signed_spread < 100
    ),
    constraint wow_v17_spread_card_receipts_teams_distinct check (
        home_team <> away_team
    ),
    constraint wow_v17_spread_card_receipts_model_class check (
        model_class = 'CERTIFIED_FITTED'
    ),
    constraint wow_v17_spread_card_receipts_triplet_range check (
        p_cover between 0 and 1
        and p_push between 0 and 1
        and p_not_cover between 0 and 1
    ),
    constraint wow_v17_spread_card_receipts_triplet_normalized check (
        abs(p_cover + p_push + p_not_cover - 1) < 1e-9
    ),
    constraint wow_v17_spread_card_receipts_bounds_interior check (
        calibrated_lower_bound > 0
        and calibrated_lower_bound <= p_cover
        and p_cover <= calibrated_upper_bound
        and calibrated_upper_bound < 1
    ),
    constraint wow_v17_spread_card_receipts_pregame check (
        model_timestamp < event_start_time_utc
        and quote_observed_at_utc < event_start_time_utc
        and source_snapshot_timestamp <= model_timestamp
    ),
    constraint wow_v17_spread_card_receipts_event_state check (
        event_state in ('SCHEDULED', 'PREGAME')
    ),
    constraint wow_v17_spread_card_receipts_score_snapshot check (
        score_snapshot_status = 'PASS'
    ),
    constraint wow_v17_spread_card_receipts_terminal check (
        terminal_authority = 'V17_TERMINAL_REDUCER'
    ),
    constraint wow_v17_spread_card_receipts_engine check (
        controlling_engine_identity = 'LLP_TEAM_BETTING_ENGINE'
    ),
    constraint wow_v17_spread_card_receipts_admitted check (
        admission_status = 'PASS'
    ),
    constraint wow_v17_spread_card_receipts_publishable check (
        probability_publishable and rank_eligible
    ),
    constraint wow_v17_spread_card_receipts_never_execute check (
        can_execute = false
    )
);

alter table public.wow_v17_spread_card_receipts enable row level security;

-- One receipt per event/line is the newest by model_timestamp; lines move, so
-- historical receipts are retained rather than replaced.
create index if not exists wow_v17_spread_card_receipts_event_idx
    on public.wow_v17_spread_card_receipts (sport, official_event_id, model_timestamp desc);

create index if not exists wow_v17_spread_card_receipts_start_idx
    on public.wow_v17_spread_card_receipts (event_start_time_utc);

create index if not exists wow_v17_spread_card_receipts_created_idx
    on public.wow_v17_spread_card_receipts (created_at desc);

comment on table public.wow_v17_spread_card_receipts is
'Append-only, admitted exact-line spread receipts from certified fitted models. Read-time line binding and admission are re-verified by V17; this table never grants publication, ranking, or execution authority.';

-- Append-only: an admitted receipt may not be edited or deleted.
create or replace function public.wow_v17_block_spread_receipt_change()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
begin
    raise exception '% row is immutable', tg_table_name;
end;
$$;

drop trigger if exists trg_wow_v17_spread_receipts_no_update on public.wow_v17_spread_card_receipts;
create trigger trg_wow_v17_spread_receipts_no_update
    before update on public.wow_v17_spread_card_receipts
    for each row execute function public.wow_v17_block_spread_receipt_change();

drop trigger if exists trg_wow_v17_spread_receipts_no_delete on public.wow_v17_spread_card_receipts;
create trigger trg_wow_v17_spread_receipts_no_delete
    before delete on public.wow_v17_spread_card_receipts
    for each row execute function public.wow_v17_block_spread_receipt_change();

-- RLS is fail-closed: no anon/authenticated policy is created. Governed
-- service-role server paths retain access; client roles receive none.
revoke all on table public.wow_v17_spread_card_receipts from anon, authenticated;

-- Deterministic rollback (only after release verification confirms no retained
-- pregame receipt is still required for grading or postmortem):
-- drop trigger if exists trg_wow_v17_spread_receipts_no_delete on public.wow_v17_spread_card_receipts;
-- drop trigger if exists trg_wow_v17_spread_receipts_no_update on public.wow_v17_spread_card_receipts;
-- drop function if exists public.wow_v17_block_spread_receipt_change();
-- drop table if exists public.wow_v17_spread_card_receipts;
