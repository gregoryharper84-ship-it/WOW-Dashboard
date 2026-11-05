-- WOW V17 PROPOSAL ONLY — DO NOT APPLY OR MOVE INTO ACTIVE MIGRATIONS
-- Class: guarded schema-intake design; NOT an approved producer, scoring route, or migration.
-- Review scope: #2473 / #1128. Keep MLB wow_event_predictions constraints intact.
-- No grant of INSERT/SELECT to application roles; no backfill; can_execute is always false.

begin;

create table if not exists public.wow_v17_multisport_event_prediction_receipts (
    event_prediction_id uuid primary key default gen_random_uuid(),
    created_at timestamptz not null default clock_timestamp(),
    schema_version text not null default 'WOW_TEAM_EVENT_RECEIPT_V1'
        check (schema_version = 'WOW_TEAM_EVENT_RECEIPT_V1'),

    sport text not null check (sport in ('NFL','NBA','NHL')),
    official_event_id text not null check (length(trim(official_event_id)) > 0),
    event_start_time timestamptz not null,
    home_team text not null check (length(trim(home_team)) > 0),
    away_team text not null check (length(trim(away_team)) > 0),
    selected_participant text not null,
    market_family text not null default 'OUTRIGHT_WINNER'
        check (market_family = 'OUTRIGHT_WINNER'),
    settlement_basis text not null,
    source_snapshot_id text not null check (length(trim(source_snapshot_id)) > 0),
    source_snapshot_timestamp timestamptz not null,

    score_snapshot_id uuid not null,
    model_artifact_id text not null check (length(trim(model_artifact_id)) > 0),
    model_version text not null check (length(trim(model_version)) > 0),
    model_artifact_sha256 text not null
        check (model_artifact_sha256 ~ '^[0-9a-f]{64}$'),
    calibration_artifact_id text not null
        check (length(trim(calibration_artifact_id)) > 0),
    calibration_version text not null check (length(trim(calibration_version)) > 0),
    calibration_artifact_sha256 text not null
        check (calibration_artifact_sha256 ~ '^[0-9a-f]{64}$'),

    model_timestamp timestamptz not null,
    locked_at timestamptz not null,
    terminal_reduced_at timestamptz not null,
    reducer_receipt_id uuid not null unique,
    reducer_receipt_sha256 text not null
        check (reducer_receipt_sha256 ~ '^[0-9a-f]{64}$'),
    terminal_authority text not null default 'V17_TERMINAL_REDUCER'
        check (terminal_authority = 'V17_TERMINAL_REDUCER'),
    terminal_status text not null default 'PASS'
        check (terminal_status = 'PASS'),
    terminal_label text not null default 'FINAL_APPROVED'
        check (terminal_label = 'FINAL_APPROVED'),

    calibrated_home_probability numeric not null,
    calibrated_home_lower_bound numeric not null,
    calibrated_home_upper_bound numeric not null,
    calibrated_away_probability numeric not null,
    calibrated_away_lower_bound numeric not null,
    calibrated_away_upper_bound numeric not null,

    rank_eligible boolean not null default true check (rank_eligible = true),
    probability_publishable boolean not null default true
        check (probability_publishable = true),
    can_execute boolean not null default false check (can_execute = false),

    constraint wow_v17_multisport_event_distinct_teams
        check (home_team <> away_team),
    constraint wow_v17_multisport_event_exact_side
        check (selected_participant in (home_team,away_team)),
    constraint wow_v17_multisport_event_exact_basis
        check (
            (sport in ('NFL','NBA')
             and settlement_basis = 'FULL_GAME_INCLUDING_OVERTIME')
            or
            (sport = 'NHL'
             and settlement_basis = 'FULL_GAME_MONEYLINE_INCLUDING_OVERTIME_SHOOTOUT')
        ),
    constraint wow_v17_multisport_event_valid_chronology
        check (
            source_snapshot_timestamp <= model_timestamp
            and model_timestamp <= locked_at
            and locked_at <= terminal_reduced_at
            and terminal_reduced_at <= created_at
            and created_at < event_start_time
        ),
    constraint wow_v17_multisport_event_valid_home_bounds
        check (
            calibrated_home_lower_bound > 0
            and calibrated_home_lower_bound <= calibrated_home_probability
            and calibrated_home_probability <= calibrated_home_upper_bound
            and calibrated_home_upper_bound < 1
        ),
    constraint wow_v17_multisport_event_valid_away_bounds
        check (
            calibrated_away_lower_bound > 0
            and calibrated_away_lower_bound <= calibrated_away_probability
            and calibrated_away_probability <= calibrated_away_upper_bound
            and calibrated_away_upper_bound < 1
        ),
    constraint wow_v17_multisport_event_probability_pair
        check (
            abs(calibrated_home_probability + calibrated_away_probability - 1.0)
            <= 0.000001
        ),
    constraint wow_v17_multisport_event_unique_score
        unique (sport, official_event_id, score_snapshot_id)
);

comment on table public.wow_v17_multisport_event_prediction_receipts is
    'PROPOSED ONLY: append-only terminal-approved NFL/NBA/NHL full-game prediction receipts. '
    'Not an independently certified producer or live publication route. '
    'Existing MLB wow_event_predictions and NBA shadow research table unchanged.';

-- DB-assigned identity and insertion instant, even if a privileged caller tries
-- to provide client-supplied values. Still NOT reducer-authentic without an
-- independently checked admission service / genuine terminal receipt.
create or replace function public.wow_v17_multisport_receipt_server_identity()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    new.event_prediction_id := pg_catalog.gen_random_uuid();
    new.created_at := pg_catalog.clock_timestamp();
    return new;
end;
$function$;

create trigger trg_wow_v17_multisport_receipt_server_identity
    before insert on public.wow_v17_multisport_event_prediction_receipts
    for each row
    execute function public.wow_v17_multisport_receipt_server_identity();

create or replace function public.wow_v17_multisport_receipt_immutable()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
    raise exception 'WOW_V17_MULTISPORT_RECEIPT_IMMUTABLE'
        using errcode = '55000';
end;
$function$;

create trigger trg_wow_v17_multisport_receipt_immutable
    before update or delete on public.wow_v17_multisport_event_prediction_receipts
    for each row
    execute function public.wow_v17_multisport_receipt_immutable();

alter table public.wow_v17_multisport_event_prediction_receipts
    enable row level security;
alter table public.wow_v17_multisport_event_prediction_receipts
    force row level security;

-- No app/anon/authenticated/service_role read or write authority. A future
-- independently reviewed adapter+role grant is required before integration.
revoke all on table public.wow_v17_multisport_event_prediction_receipts from public;
do $roles$
declare role_name text;
begin
    for role_name in
        select rolname from pg_catalog.pg_roles
        where rolname in ('anon','authenticated','service_role')
    loop
        execute format(
            'revoke all on table public.wow_v17_multisport_event_prediction_receipts from %I',
            role_name
        );
    end loop;
end;
$roles$;

commit;

-- Deliberately absent: INSERT grants, authenticated producer/adapter,
-- independent V17_TERMINAL_REDUCER proof, backfill, readback route, deployment.
-- This proposed SQL alone MUST NOT result in publication or model eligibility.
