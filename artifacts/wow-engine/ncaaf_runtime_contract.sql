-- WOW v16 Clean Core — NCAAF live runtime feature + calibration contracts.
-- Additive, service-role only, can_execute=false.

create table if not exists public.wow_ncaaf_event_feature_snapshots (
    feature_snapshot_id uuid primary key default gen_random_uuid(),
    created_at timestamptz not null default now(),
    official_event_id text not null,
    event_start_time timestamptz not null,
    feature_schema_version text not null,
    feature_as_of timestamptz not null,
    home_team text not null,
    away_team text not null,
    venue text not null,
    neutral_site boolean not null default false,
    source_manifest jsonb not null,

    home_power_rating numeric not null,
    away_power_rating numeric not null,
    home_off_epa numeric not null,
    away_off_epa numeric not null,
    home_def_epa numeric not null,
    away_def_epa numeric not null,
    home_success_rate numeric not null,
    away_success_rate numeric not null,
    home_explosiveness numeric not null,
    away_explosiveness numeric not null,
    home_qb_value numeric not null,
    away_qb_value numeric not null,
    home_qb_certainty numeric not null,
    away_qb_certainty numeric not null,
    home_ol_health numeric not null,
    away_ol_health numeric not null,
    home_def_front_health numeric not null,
    away_def_front_health numeric not null,
    home_skill_availability numeric not null,
    away_skill_availability numeric not null,
    home_rest_days numeric not null,
    away_rest_days numeric not null,
    travel_distance_miles numeric not null,
    home_tempo numeric not null,
    away_tempo numeric not null,
    home_turnover_volatility numeric not null,
    away_turnover_volatility numeric not null,
    home_special_teams_rating numeric not null,
    away_special_teams_rating numeric not null,
    weather_wind_mph numeric not null,
    weather_precip_probability numeric not null,

    starting_qb_status_home text not null,
    starting_qb_status_away text not null,
    depth_chart_status text not null,
    injury_evidence_timestamp timestamptz not null,
    lineup_status text not null,
    market_timestamp timestamptz,
    market_home_no_vig numeric,
    market_away_no_vig numeric,
    can_execute boolean not null default false,

    constraint wow_ncaaf_live_teams_distinct check (home_team <> away_team),
    constraint wow_ncaaf_live_manifest_object check (jsonb_typeof(source_manifest) = 'object'),
    constraint wow_ncaaf_live_qb_home check (home_qb_certainty between 0 and 1),
    constraint wow_ncaaf_live_qb_away check (away_qb_certainty between 0 and 1),
    constraint wow_ncaaf_live_market_home check (market_home_no_vig is null or market_home_no_vig > 0 and market_home_no_vig < 1),
    constraint wow_ncaaf_live_market_away check (market_away_no_vig is null or market_away_no_vig > 0 and market_away_no_vig < 1),
    constraint wow_ncaaf_live_pregame check (feature_as_of < event_start_time and injury_evidence_timestamp < event_start_time and (market_timestamp is null or market_timestamp < event_start_time)),
    constraint wow_ncaaf_live_never_execute check (can_execute = false),
    unique (official_event_id, feature_schema_version, feature_as_of)
);

create index if not exists idx_wow_ncaaf_event_feature_lookup
    on public.wow_ncaaf_event_feature_snapshots (official_event_id, feature_schema_version, feature_as_of desc);

create table if not exists public.wow_ncaaf_calibrator_artifacts (
    calibrator_id uuid primary key default gen_random_uuid(),
    created_at timestamptz not null default now(),
    calibrator_version text not null unique,
    model_artifact_version text not null,
    calibration_method text not null,
    training_n integer not null,
    calibration_start_date date not null,
    calibration_end_date date not null,
    payload jsonb not null,
    metrics jsonb not null default '{}'::jsonb,
    calibration_health_status text not null default 'BLOCKED',
    active boolean not null default false,
    probability_publishable boolean not null default false,
    can_execute boolean not null default false,
    constraint wow_ncaaf_calibrator_method check (calibration_method in ('EMPIRICAL_WILSON_BINS_V1')),
    constraint wow_ncaaf_calibrator_training_n check (training_n > 0),
    constraint wow_ncaaf_calibrator_dates check (calibration_end_date >= calibration_start_date),
    constraint wow_ncaaf_calibrator_payload_object check (jsonb_typeof(payload) = 'object'),
    constraint wow_ncaaf_calibrator_metrics_object check (jsonb_typeof(metrics) = 'object'),
    constraint wow_ncaaf_calibrator_health check (calibration_health_status in ('BLOCKED','WATCH','PASS')),
    constraint wow_ncaaf_calibrator_publish_gate check (probability_publishable = false or (active = true and calibration_health_status = 'PASS' and training_n >= 50)),
    constraint wow_ncaaf_calibrator_never_execute check (can_execute = false)
);

create unique index if not exists uq_wow_ncaaf_active_calibrator_for_model
    on public.wow_ncaaf_calibrator_artifacts (model_artifact_version)
    where active;

alter table public.wow_ncaaf_event_feature_snapshots enable row level security;
alter table public.wow_ncaaf_calibrator_artifacts enable row level security;
revoke all on table public.wow_ncaaf_event_feature_snapshots, public.wow_ncaaf_calibrator_artifacts from anon, authenticated;
grant all on table public.wow_ncaaf_event_feature_snapshots, public.wow_ncaaf_calibrator_artifacts to service_role;

create or replace function public.wow_ncaaf_latest_event_features(
    p_official_event_id text,
    p_feature_schema_version text,
    p_home_team text,
    p_away_team text
) returns jsonb
language plpgsql
stable
security invoker
set search_path = public
as $$
declare
    v_row public.wow_ncaaf_event_feature_snapshots%rowtype;
begin
    select * into v_row
      from public.wow_ncaaf_event_feature_snapshots
     where official_event_id = p_official_event_id
       and feature_schema_version = p_feature_schema_version
       and home_team = p_home_team
       and away_team = p_away_team
       and feature_as_of < event_start_time
     order by feature_as_of desc
     limit 1;
    if not found then
        return jsonb_build_object('ok', false, 'code', 'NCAAF_EVENT_FEATURE_SNAPSHOT_NOT_FOUND', 'probability_publishable', false, 'can_execute', false);
    end if;
    return to_jsonb(v_row) || jsonb_build_object('ok', true, 'code', 'NCAAF_EVENT_FEATURE_SNAPSHOT_READY', 'probability_publishable', false, 'can_execute', false);
end;
$$;

create or replace function public.wow_ncaaf_active_calibrator(
    p_model_artifact_version text
) returns jsonb
language plpgsql
stable
security invoker
set search_path = public
as $$
declare
    v_row public.wow_ncaaf_calibrator_artifacts%rowtype;
begin
    select * into v_row
      from public.wow_ncaaf_calibrator_artifacts
     where model_artifact_version = p_model_artifact_version
       and active = true
       and probability_publishable = true
       and calibration_health_status = 'PASS'
     order by created_at desc
     limit 1;
    if not found then
        return jsonb_build_object('ok', false, 'code', 'NCAAF_CERTIFIED_CALIBRATOR_NOT_FOUND', 'probability_publishable', false, 'can_execute', false);
    end if;
    return to_jsonb(v_row) || jsonb_build_object('ok', true, 'code', 'NCAAF_CERTIFIED_CALIBRATOR_READY', 'can_execute', false);
end;
$$;

revoke all on function public.wow_ncaaf_latest_event_features(text,text,text,text) from public, anon, authenticated;
revoke all on function public.wow_ncaaf_active_calibrator(text) from public, anon, authenticated;
grant execute on function public.wow_ncaaf_latest_event_features(text,text,text,text) to service_role;
grant execute on function public.wow_ncaaf_active_calibrator(text) to service_role;

comment on table public.wow_ncaaf_event_feature_snapshots is 'Live pregame NCAAF fitted-model feature snapshots. Exact event/team identity, explicit as-of timestamps, service-role only, can_execute=false.';
comment on table public.wow_ncaaf_calibrator_artifacts is 'Dedicated NCAAF calibration artifacts. Publication requires active PASS state and >=50 calibration rows; can_execute=false.';
