-- WOW NCAAF fitted-model historical training contract.
-- Stores only pregame-eligible features with explicit as-of timestamps so
-- retrospective leakage can be audited. No prediction/publication authority.

create table if not exists public.wow_ncaaf_training_games (
    training_game_id uuid primary key default gen_random_uuid(),
    created_at timestamptz not null default now(),
    official_event_id text not null,
    season integer not null,
    week integer not null,
    season_type text not null,
    event_start_time timestamptz not null,
    venue text,
    neutral_site boolean not null default false,
    home_team text not null,
    away_team text not null,
    home_points integer,
    away_points integer,
    home_won boolean,
    result_source text not null,
    result_source_timestamp timestamptz not null,
    can_execute boolean not null default false,
    constraint wow_ncaaf_training_game_teams_distinct check (home_team <> away_team),
    constraint wow_ncaaf_training_never_execute check (can_execute = false),
    unique (official_event_id)
);

create table if not exists public.wow_ncaaf_training_features (
    feature_row_id uuid primary key default gen_random_uuid(),
    created_at timestamptz not null default now(),
    training_game_id uuid not null references public.wow_ncaaf_training_games(training_game_id),
    feature_schema_version text not null,
    feature_as_of timestamptz not null,
    feature_source_manifest jsonb not null,

    -- Team quality / opponent-adjusted form
    home_power_rating numeric,
    away_power_rating numeric,
    home_off_epa numeric,
    away_off_epa numeric,
    home_def_epa numeric,
    away_def_epa numeric,
    home_success_rate numeric,
    away_success_rate numeric,
    home_explosiveness numeric,
    away_explosiveness numeric,

    -- Situational and roster certainty
    home_qb_value numeric,
    away_qb_value numeric,
    home_qb_certainty numeric,
    away_qb_certainty numeric,
    home_ol_health numeric,
    away_ol_health numeric,
    home_def_front_health numeric,
    away_def_front_health numeric,
    home_skill_availability numeric,
    away_skill_availability numeric,
    home_rest_days numeric,
    away_rest_days numeric,
    travel_distance_miles numeric,

    -- Pace / variance / special teams / weather
    home_tempo numeric,
    away_tempo numeric,
    home_turnover_volatility numeric,
    away_turnover_volatility numeric,
    home_special_teams_rating numeric,
    away_special_teams_rating numeric,
    weather_temperature_f numeric,
    weather_wind_mph numeric,
    weather_precip_probability numeric,

    -- Market prior is stored separately and may be used only at an explicitly
    -- governed blend weight; it must never substitute for independent features.
    market_home_no_vig numeric,
    market_away_no_vig numeric,
    market_timestamp timestamptz,

    can_execute boolean not null default false,

    constraint wow_ncaaf_feature_manifest_object check (jsonb_typeof(feature_source_manifest) = 'object'),
    constraint wow_ncaaf_feature_qb_certainty_home check (home_qb_certainty is null or home_qb_certainty between 0 and 1),
    constraint wow_ncaaf_feature_qb_certainty_away check (away_qb_certainty is null or away_qb_certainty between 0 and 1),
    constraint wow_ncaaf_feature_market_home check (market_home_no_vig is null or (market_home_no_vig > 0 and market_home_no_vig < 1)),
    constraint wow_ncaaf_feature_market_away check (market_away_no_vig is null or (market_away_no_vig > 0 and market_away_no_vig < 1)),
    constraint wow_ncaaf_training_features_never_execute check (can_execute = false),
    unique (training_game_id, feature_schema_version, feature_as_of)
);

create or replace function public.wow_ncaaf_assert_feature_pregame()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
declare
    v_start timestamptz;
begin
    select event_start_time into v_start
      from public.wow_ncaaf_training_games
     where training_game_id = NEW.training_game_id;

    if v_start is null then
        raise exception 'NCAAF training game % not found', NEW.training_game_id;
    end if;
    if NEW.feature_as_of >= v_start then
        raise exception 'NCAAF feature snapshot must be strictly pregame: feature_as_of %, kickoff %', NEW.feature_as_of, v_start;
    end if;
    if NEW.market_timestamp is not null and NEW.market_timestamp >= v_start then
        raise exception 'NCAAF market snapshot used for training must be strictly pregame: market_timestamp %, kickoff %', NEW.market_timestamp, v_start;
    end if;
    return NEW;
end;
$$;

revoke all on function public.wow_ncaaf_assert_feature_pregame() from public, anon, authenticated;
grant execute on function public.wow_ncaaf_assert_feature_pregame() to service_role;

drop trigger if exists trg_wow_ncaaf_assert_feature_pregame on public.wow_ncaaf_training_features;
create trigger trg_wow_ncaaf_assert_feature_pregame
    before insert or update on public.wow_ncaaf_training_features
    for each row execute function public.wow_ncaaf_assert_feature_pregame();

alter table public.wow_ncaaf_training_games enable row level security;
alter table public.wow_ncaaf_training_features enable row level security;
revoke all on table public.wow_ncaaf_training_games from anon, authenticated;
revoke all on table public.wow_ncaaf_training_features from anon, authenticated;
grant all on table public.wow_ncaaf_training_games to service_role;
grant all on table public.wow_ncaaf_training_features to service_role;

comment on table public.wow_ncaaf_training_features is
  'Pregame-only NCAAF feature snapshots for fitted-model training. Trigger-enforced chronology; no execution authority.';
