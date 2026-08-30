-- WOW v16 Clean Core — governed LIVE_UPSET probability lane, replay-safe v2.
-- This migration intentionally seeds no model, calibrator, champion, prediction,
-- or executable capability. can_execute=false remains invariant.

alter table public.wow_calibrators
    add column if not exists live_bounds_json jsonb;

create table if not exists public.wow_live_state_snapshots (
    source_snapshot_id uuid primary key default gen_random_uuid(),
    official_event_id text not null,
    sport text not null,
    league text not null,
    event_status text not null,
    home_team text,
    away_team text,
    snapshot_timestamp timestamptz not null,
    latest_material_update_at timestamptz not null,
    source_provider text not null,
    source_uri text,
    source_retrieved_at timestamptz not null,
    state_schema_version text not null,
    state_hash text not null,
    state_json jsonb not null,
    feature_model_family text not null,
    feature_model_artifact_version text not null,
    feature_schema_version text not null,
    feature_artifact_checksum text not null,
    created_at timestamptz not null default clock_timestamp(),
    can_execute boolean not null default false,
    check (event_status in ('IN_PROGRESS','SCHEDULED','FINAL','CANCELLED','POSTPONED','SUSPENDED')),
    check (can_execute = false),
    unique (official_event_id, sport, snapshot_timestamp, state_hash)
);

-- Upgrade a partially-created earlier schema without inventing model binding.
alter table public.wow_live_state_snapshots
    add column if not exists feature_model_family text,
    add column if not exists feature_model_artifact_version text,
    add column if not exists feature_schema_version text,
    add column if not exists feature_artifact_checksum text;

do $$
begin
    if exists (
        select 1
        from public.wow_live_state_snapshots
        where feature_model_family is null
           or feature_model_artifact_version is null
           or feature_schema_version is null
           or feature_artifact_checksum is null
    ) then
        raise exception 'LIVE_STATE_ARTIFACT_BINDING_REQUIRED_BEFORE_MIGRATION';
    end if;
end;
$$;

alter table public.wow_live_state_snapshots
    alter column feature_model_family set not null,
    alter column feature_model_artifact_version set not null,
    alter column feature_schema_version set not null,
    alter column feature_artifact_checksum set not null;

create index if not exists wow_live_state_event_lookup
    on public.wow_live_state_snapshots (sport, official_event_id, snapshot_timestamp desc);

create table if not exists public.wow_live_probability_predictions (
    prediction_id uuid primary key default gen_random_uuid(),
    research_run_id text not null,
    official_event_id text not null,
    sport text not null,
    league text not null,
    exact_selection text not null,
    mode text not null default 'LIVE_UPSET',
    event_status text not null,
    settlement_rule text not null,
    source_snapshot_id uuid not null references public.wow_live_state_snapshots(source_snapshot_id),
    live_snapshot_timestamp timestamptz not null,
    state_schema_version text not null,
    state_hash text not null,
    market_role text not null,
    market_role_source text not null,
    market_role_timestamp timestamptz not null,
    market_role_confidence numeric not null,
    model_family text not null,
    model_version text not null,
    calibration_method text not null,
    calibration_version text not null,
    raw_probability numeric not null,
    unconditional_probability numeric not null,
    calibrated_probability numeric not null,
    lower_bound numeric not null,
    upper_bound numeric not null,
    failure_path_score numeric not null,
    main_failure_path text not null,
    regime_probabilities_json jsonb not null,
    simulation_seed bigint not null,
    simulation_draws integer not null,
    terminal_label text not null,
    upset_tier text not null,
    probability_publishable boolean not null default false,
    model_timestamp timestamptz not null,
    created_at timestamptz not null default clock_timestamp(),
    can_execute boolean not null default false,
    check (mode = 'LIVE_UPSET'),
    check (event_status = 'IN_PROGRESS'),
    check (settlement_rule = 'FULL_GAME_OUTRIGHT'),
    check (market_role = 'UNDERDOG'),
    check (market_role_confidence >= 0 and market_role_confidence <= 1),
    check (raw_probability > 0 and raw_probability < 1),
    check (unconditional_probability > 0 and unconditional_probability < 1),
    check (calibrated_probability > 0 and calibrated_probability < 1),
    check (lower_bound > 0 and lower_bound <= calibrated_probability),
    check (upper_bound >= calibrated_probability and upper_bound < 1),
    check (failure_path_score >= 0 and failure_path_score <= 1),
    check (simulation_draws >= 50000),
    check (probability_publishable = true),
    check (can_execute = false)
);

create unique index if not exists uq_wow_live_prediction_run_event_mode
    on public.wow_live_probability_predictions (research_run_id, official_event_id, mode);
create index if not exists wow_live_prediction_event_time
    on public.wow_live_probability_predictions (sport, official_event_id, model_timestamp desc);
create index if not exists wow_live_prediction_source_snapshot_idx
    on public.wow_live_probability_predictions (source_snapshot_id);

create or replace function public.wow_block_live_immutable_change()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
begin
    raise exception '% row is immutable', tg_table_name;
end;
$$;

drop trigger if exists trg_wow_live_state_immutable_update on public.wow_live_state_snapshots;
create trigger trg_wow_live_state_immutable_update
    before update on public.wow_live_state_snapshots
    for each row execute function public.wow_block_live_immutable_change();
drop trigger if exists trg_wow_live_state_immutable_delete on public.wow_live_state_snapshots;
create trigger trg_wow_live_state_immutable_delete
    before delete on public.wow_live_state_snapshots
    for each row execute function public.wow_block_live_immutable_change();
drop trigger if exists trg_wow_live_prediction_immutable_update on public.wow_live_probability_predictions;
create trigger trg_wow_live_prediction_immutable_update
    before update on public.wow_live_probability_predictions
    for each row execute function public.wow_block_live_immutable_change();
drop trigger if exists trg_wow_live_prediction_immutable_delete on public.wow_live_probability_predictions;
create trigger trg_wow_live_prediction_immutable_delete
    before delete on public.wow_live_probability_predictions
    for each row execute function public.wow_block_live_immutable_change();

alter table public.wow_live_state_snapshots enable row level security;
alter table public.wow_live_probability_predictions enable row level security;
revoke all on table public.wow_live_state_snapshots from anon, authenticated;
revoke all on table public.wow_live_probability_predictions from anon, authenticated;
revoke all on function public.wow_block_live_immutable_change() from anon, authenticated;

comment on table public.wow_live_state_snapshots is
'Immutable server-side snapshots for governed live event scoring. Caller input is not authoritative.';
comment on table public.wow_live_probability_predictions is
'Immutable WOW LIVE_UPSET probability research ledger. can_execute=false.';
