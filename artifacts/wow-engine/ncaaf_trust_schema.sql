-- WOW v16 Clean Core — dedicated NCAAF trust/calibration ledger
-- Additive only. Does not alter MLB event tables or live-execution governance.
-- can_execute=false is database-enforced.

create table if not exists wow_ncaaf_predictions (
    ncaaf_prediction_id uuid primary key default gen_random_uuid(),
    created_at timestamptz not null default now(),
    research_run_id text not null,

    event_date date not null,
    sport text not null default 'NCAAF' check (sport = 'NCAAF'),
    league text not null default 'NCAAF' check (league = 'NCAAF'),
    official_event_id text not null,
    event_start_time timestamptz not null,
    venue text not null,
    neutral_site boolean not null default false,
    home_away text not null check (home_away in ('HOME','AWAY','NEUTRAL')),

    team text not null,
    opponent text not null,
    market_role text not null check (market_role in ('FAVORITE','UNDERDOG','EVEN','CONFLICT')),
    market text not null default 'MONEYLINE',
    selection text not null,
    source text not null,
    source_snapshot_id uuid not null,

    starting_qb_status text not null,
    backup_qb_downgrade_value numeric,
    offensive_line_injury_status text not null,
    defensive_front_pass_rush_health text not null,
    top_wr_rb_availability text not null,
    travel_rest_spot text not null,
    weather_summary text not null,
    wind_mph numeric,

    selection_price_american integer,
    opposing_price_american integer,
    market_timestamp timestamptz,
    no_vig_probability numeric,

    independent_probability numeric,
    market_prior_weight numeric not null default 0.0,
    unconditional_probability numeric,
    calibrated_probability numeric,
    lower_bound numeric,
    upper_bound numeric,

    failure_regimes_json jsonb,
    failure_path_score numeric,
    largest_failure_path text,

    conference_tier text not null,
    fbs_vs_fcs text not null,
    qb_certainty numeric not null,
    depth_chart_certainty numeric not null,
    injury_reporting_quality numeric not null,
    market_liquidity numeric not null,
    weather_variance numeric not null,
    team_tempo numeric not null,
    turnover_volatility numeric not null,
    special_teams_volatility numeric not null,
    model_disagreement numeric not null,

    model_family text,
    model_version text,
    model_artifact_id text,
    model_timestamp timestamptz,
    calibration_status text,
    calibration_method text,
    calibration_version text,
    calibration_training_n integer,
    calibration_health_status text,

    ncaaf_trust_state text not null default 'NCAAF_TEST_ONLY'
        check (ncaaf_trust_state in (
            'NCAAF_TEST_ONLY','NCAAF_WATCH','NCAAF_PRIMARY_CANDIDATE',
            'NCAAF_TRUSTED','NCAAF_SCALE_ELIGIBLE'
        )),
    terminal_label text,
    blockers text[] not null default '{}',
    failure_tags text[] not null default '{}',
    probability_publishable boolean not null default false,
    locked_at timestamptz,
    can_execute boolean not null default false,

    constraint chk_ncaaf_distinct_teams check (team <> opponent),
    constraint chk_ncaaf_can_execute_false check (can_execute = false),
    constraint chk_ncaaf_neutral_consistency check (neutral_site = false or home_away = 'NEUTRAL'),
    constraint chk_ncaaf_market_prior_weight check (market_prior_weight >= 0 and market_prior_weight <= 1),
    constraint chk_ncaaf_no_vig check (no_vig_probability is null or (no_vig_probability > 0 and no_vig_probability < 1)),
    constraint chk_ncaaf_independent_probability check (independent_probability is null or (independent_probability > 0 and independent_probability < 1)),
    constraint chk_ncaaf_unconditional_probability check (unconditional_probability is null or (unconditional_probability > 0 and unconditional_probability < 1)),
    constraint chk_ncaaf_failure_path_score check (failure_path_score is null or (failure_path_score >= 0 and failure_path_score < 1)),
    constraint chk_ncaaf_calibrated_bounds check (
        (
            calibrated_probability is null and lower_bound is null and upper_bound is null
        ) or (
            calibrated_probability is not null and lower_bound is not null and upper_bound is not null
            and lower_bound > 0 and lower_bound <= calibrated_probability
            and calibrated_probability <= upper_bound and upper_bound < 1
        )
    ),
    constraint chk_ncaaf_qb_certainty check (qb_certainty between 0 and 1),
    constraint chk_ncaaf_depth_chart_certainty check (depth_chart_certainty between 0 and 1),
    constraint chk_ncaaf_injury_reporting_quality check (injury_reporting_quality between 0 and 1),
    constraint chk_ncaaf_market_liquidity check (market_liquidity between 0 and 1),
    constraint chk_ncaaf_weather_variance check (weather_variance between 0 and 1),
    constraint chk_ncaaf_team_tempo check (team_tempo between 0 and 1),
    constraint chk_ncaaf_turnover_volatility check (turnover_volatility between 0 and 1),
    constraint chk_ncaaf_special_teams_volatility check (special_teams_volatility between 0 and 1),
    constraint chk_ncaaf_model_disagreement check (model_disagreement between 0 and 1),
    constraint chk_ncaaf_qb_watch_gate check (
        starting_qb_status in ('CONFIRMED','CONFIRMED_STARTER','BACKUP_CONFIRMED')
        or probability_publishable = false
    ),
    constraint chk_ncaaf_trust_publication check (
        ncaaf_trust_state not in ('NCAAF_TEST_ONLY','NCAAF_WATCH')
        or probability_publishable = false
    )
);

create unique index if not exists uq_wow_ncaaf_prediction_run_event_team_model_time
    on wow_ncaaf_predictions (research_run_id, official_event_id, team, model_timestamp)
    where model_timestamp is not null;

create or replace function wow_block_ncaaf_prediction_post_start_edit()
returns trigger as $$
begin
    if OLD.locked_at is not null then
        raise exception 'wow_ncaaf_predictions row % is locked (immutable)', OLD.ncaaf_prediction_id;
    end if;
    if OLD.event_start_time <= now() then
        raise exception 'wow_ncaaf_predictions row % cannot be edited after event start', OLD.ncaaf_prediction_id;
    end if;
    return NEW;
end;
$$ language plpgsql
set search_path = '';

drop trigger if exists trg_wow_block_ncaaf_prediction_post_start_edit on wow_ncaaf_predictions;
create trigger trg_wow_block_ncaaf_prediction_post_start_edit
    before update on wow_ncaaf_predictions
    for each row execute function wow_block_ncaaf_prediction_post_start_edit();

create or replace function wow_block_ncaaf_prediction_post_start_delete()
returns trigger as $$
begin
    if OLD.locked_at is not null then
        raise exception 'wow_ncaaf_predictions row % is locked and cannot be deleted', OLD.ncaaf_prediction_id;
    end if;
    if OLD.event_start_time <= now() then
        raise exception 'wow_ncaaf_predictions row % cannot be deleted after event start', OLD.ncaaf_prediction_id;
    end if;
    return OLD;
end;
$$ language plpgsql
set search_path = '';

drop trigger if exists trg_wow_block_ncaaf_prediction_post_start_delete on wow_ncaaf_predictions;
create trigger trg_wow_block_ncaaf_prediction_post_start_delete
    before delete on wow_ncaaf_predictions
    for each row execute function wow_block_ncaaf_prediction_post_start_delete();

create table if not exists wow_ncaaf_outcomes (
    ncaaf_outcome_id uuid primary key default gen_random_uuid(),
    ncaaf_prediction_id uuid not null references wow_ncaaf_predictions(ncaaf_prediction_id),
    created_at timestamptz not null default now(),

    closing_price_american integer,
    closing_opposing_price_american integer,
    closing_no_vig numeric,
    closing_snapshot_timestamp timestamptz,
    clv numeric,
    clv_grade text check (clv_grade is null or clv_grade in (
        'BEAT_CLOSE','CLOSED_SAME','LOST_TO_CLOSE','NO_CLOSE_AVAILABLE'
    )),

    result text,
    won boolean,
    void boolean not null default false,
    settlement_source text,
    settlement_timestamp timestamptz,
    brier_score numeric,
    log_loss numeric,
    postmortem_note text,
    observed_failure_path text,
    process_classification text,

    constraint chk_ncaaf_closing_no_vig check (closing_no_vig is null or (closing_no_vig > 0 and closing_no_vig < 1)),
    constraint chk_ncaaf_brier check (brier_score is null or (brier_score >= 0 and brier_score <= 1)),
    constraint chk_ncaaf_log_loss check (log_loss is null or log_loss >= 0)
);

create unique index if not exists uq_wow_ncaaf_outcome_prediction
    on wow_ncaaf_outcomes (ncaaf_prediction_id);

-- One joined calibration ledger with the exact pregame + close + settlement
-- fields needed for forward trust reviews. This view is evaluation-only;
-- it does not authorize betting or execution.
create or replace view wow_ncaaf_calibration_ledger as
select
    p.event_date as date,
    p.sport,
    p.league,
    p.official_event_id as event_id,
    p.team,
    p.opponent,
    p.market_role,
    p.market,
    p.selection,
    p.source,
    p.selection_price_american as price_if_available,
    p.opposing_price_american as opposing_price_if_available,
    p.no_vig_probability as no_vig_if_available,
    p.independent_probability,
    p.market_prior_weight,
    p.unconditional_probability,
    p.calibrated_probability,
    p.lower_bound,
    p.upper_bound,
    p.terminal_label,
    p.blockers,
    p.failure_tags,
    p.model_timestamp,
    o.closing_price_american as closing_price,
    o.closing_no_vig,
    o.result,
    o.brier_score,
    o.log_loss,
    o.clv,
    o.clv_grade,
    o.postmortem_note,
    p.ncaaf_trust_state,
    p.can_execute
from wow_ncaaf_predictions p
left join wow_ncaaf_outcomes o
    on o.ncaaf_prediction_id = p.ncaaf_prediction_id;

alter table wow_ncaaf_predictions enable row level security;
alter table wow_ncaaf_outcomes enable row level security;

comment on table wow_ncaaf_predictions is 'NCAAF governed pregame research ledger. can_execute is permanently false.';
comment on table wow_ncaaf_outcomes is 'NCAAF close/settlement/calibration evidence, including CLV/Brier/log-loss.';
comment on view wow_ncaaf_calibration_ledger is 'Joined NCAAF forward trust ledger. Evaluation only; no execution authority.';
