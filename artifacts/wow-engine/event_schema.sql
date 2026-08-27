-- WOW /score-event additive persistence — MLB full-game outright winner v1
-- Framework: WOW v16 Clean Core
-- This schema is separate from wow_predictions/wow_outcomes because those
-- tables are prop-specific (including MORE/LESS direction semantics).
-- can_execute remains false; these tables store research/audit evidence only.

create table if not exists wow_event_predictions (
    event_prediction_id uuid primary key default gen_random_uuid(),
    created_at timestamptz not null default now(),

    research_run_id text not null,
    event_key text not null,
    official_event_id text not null,
    requested_slate_date date not null,
    requested_timezone text not null,
    scan_stage text not null check (scan_stage = 'PREGAME'),
    event_start_time timestamptz not null,

    sport text not null check (sport = 'MLB'),
    league text not null check (league = 'MLB'),
    market_family text not null check (market_family = 'OUTRIGHT_WINNER'),
    settlement_basis text not null check (settlement_basis = 'FULL_GAME_INCLUDING_EXTRA_INNINGS'),

    home_team text not null,
    away_team text not null,
    venue text,

    home_starting_pitcher text,
    away_starting_pitcher text,
    home_starter_status text,
    away_starter_status text,
    home_lineup_status text,
    away_lineup_status text,

    source_snapshot_id uuid not null,
    latest_material_update_timestamp timestamptz,
    model_timestamp timestamptz,
    model_valid_after_latest_update boolean not null default false,

    controlling_specialist text not null default 'wow.mlb-game-win-probability-expert',
    model_version text,
    model_artifact_id text,
    model_inputs_hash text,
    simulation_seed bigint,
    simulation_count integer,

    projected_runs_home numeric,
    projected_runs_away numeric,
    tie_after_9_probability numeric,
    home_wins_extras_given_tie numeric,
    away_wins_extras_given_tie numeric,

    raw_home_probability numeric,
    raw_away_probability numeric,
    independent_home_probability numeric,
    independent_away_probability numeric,

    market_prior_available boolean not null default false,
    market_prior_home_probability numeric,
    market_prior_away_probability numeric,
    market_prior_weight numeric not null default 0.0,
    market_prior_quality text,
    market_timestamp timestamptz,

    calibration_status text,
    calibration_method text,
    calibration_version text,
    calibration_training_n integer,
    calibration_parent_cohort text,
    bounds_method_version text,

    calibrated_home_probability numeric,
    calibrated_home_lower_bound numeric,
    calibrated_home_upper_bound numeric,
    calibrated_away_probability numeric,
    calibrated_away_lower_bound numeric,
    calibrated_away_upper_bound numeric,

    favorite_side text check (favorite_side is null or favorite_side in ('HOME','AWAY')),
    favorite_failure_paths_json jsonb,
    largest_favorite_loss_path text,
    favorite_failure_path_probability numeric,
    underdog_upset_path_json jsonb,

    probability_audit_result text,
    event_decision text,
    selected_participant text,
    selected_market_role text,
    lower_bound_gap numeric,
    event_mutex_status text,

    rank_eligible boolean not null default false,
    probability_publishable boolean not null default false,
    probability_ceiling text,
    data_gaps text[] not null default '{}',
    blockers text[] not null default '{}',
    locked_at timestamptz,

    can_execute boolean not null default false,

    constraint chk_event_distinct_teams
        check (home_team <> away_team),

    constraint chk_event_can_execute_false
        check (can_execute = false),

    constraint chk_event_simulation_count
        check (simulation_count is null or simulation_count >= 50000),

    constraint chk_event_raw_home_bounds
        check (raw_home_probability is null or (raw_home_probability > 0 and raw_home_probability < 1)),

    constraint chk_event_raw_away_bounds
        check (raw_away_probability is null or (raw_away_probability > 0 and raw_away_probability < 1)),

    constraint chk_event_raw_pair
        check (
            (raw_home_probability is null and raw_away_probability is null)
            or (
                raw_home_probability is not null
                and raw_away_probability is not null
                and abs(raw_home_probability + raw_away_probability - 1.0) <= 0.000001
            )
        ),

    constraint chk_event_independent_home_bounds
        check (independent_home_probability is null or (independent_home_probability > 0 and independent_home_probability < 1)),

    constraint chk_event_independent_away_bounds
        check (independent_away_probability is null or (independent_away_probability > 0 and independent_away_probability < 1)),

    constraint chk_event_independent_pair
        check (
            (independent_home_probability is null and independent_away_probability is null)
            or (
                independent_home_probability is not null
                and independent_away_probability is not null
                and abs(independent_home_probability + independent_away_probability - 1.0) <= 0.000001
            )
        ),

    constraint chk_event_market_prior_weight
        check (market_prior_weight >= 0 and market_prior_weight <= 1),

    constraint chk_event_market_prior_pair
        check (
            (
                market_prior_available = false
                and market_prior_home_probability is null
                and market_prior_away_probability is null
            )
            or (
                market_prior_available = true
                and market_prior_home_probability is not null
                and market_prior_away_probability is not null
                and market_prior_home_probability > 0
                and market_prior_home_probability < 1
                and market_prior_away_probability > 0
                and market_prior_away_probability < 1
                and abs(market_prior_home_probability + market_prior_away_probability - 1.0) <= 0.000001
            )
        ),

    constraint chk_event_tie_after_9_bounds
        check (tie_after_9_probability is null or (tie_after_9_probability > 0 and tie_after_9_probability < 1)),

    constraint chk_event_extras_home_bounds
        check (home_wins_extras_given_tie is null or (home_wins_extras_given_tie > 0 and home_wins_extras_given_tie < 1)),

    constraint chk_event_extras_away_bounds
        check (away_wins_extras_given_tie is null or (away_wins_extras_given_tie > 0 and away_wins_extras_given_tie < 1)),

    constraint chk_event_extras_pair
        check (
            (home_wins_extras_given_tie is null and away_wins_extras_given_tie is null)
            or (
                home_wins_extras_given_tie is not null
                and away_wins_extras_given_tie is not null
                and abs(home_wins_extras_given_tie + away_wins_extras_given_tie - 1.0) <= 0.000001
            )
        ),

    constraint chk_event_failure_path_probability
        check (
            favorite_failure_path_probability is null
            or (favorite_failure_path_probability > 0 and favorite_failure_path_probability < 1)
        ),

    constraint chk_event_calibrated_block
        check (
            (
                calibrated_home_probability is null
                and calibrated_home_lower_bound is null
                and calibrated_home_upper_bound is null
                and calibrated_away_probability is null
                and calibrated_away_lower_bound is null
                and calibrated_away_upper_bound is null
            )
            or (
                calibrated_home_probability is not null
                and calibrated_home_lower_bound is not null
                and calibrated_home_upper_bound is not null
                and calibrated_away_probability is not null
                and calibrated_away_lower_bound is not null
                and calibrated_away_upper_bound is not null

                and calibrated_home_lower_bound > 0
                and calibrated_home_lower_bound <= calibrated_home_probability
                and calibrated_home_probability <= calibrated_home_upper_bound
                and calibrated_home_upper_bound < 1

                and calibrated_away_lower_bound > 0
                and calibrated_away_lower_bound <= calibrated_away_probability
                and calibrated_away_probability <= calibrated_away_upper_bound
                and calibrated_away_upper_bound < 1

                and abs(calibrated_home_probability + calibrated_away_probability - 1.0) <= 0.000001
            )
        ),

    constraint chk_event_selected_participant
        check (
            selected_participant is null
            or selected_participant = home_team
            or selected_participant = away_team
        ),

    constraint chk_event_publishable_requires_governance
        check (
            probability_publishable = false
            or (
                model_valid_after_latest_update = true
                and probability_audit_result = 'PASS_PROBABILITY_AUDIT'
                and rank_eligible = true
                and calibrated_home_probability is not null
                and calibrated_away_probability is not null
                and calibrated_home_lower_bound is not null
                and calibrated_away_lower_bound is not null
                and cardinality(blockers) = 0
            )
        )
);

create unique index if not exists uq_wow_event_prediction_run_event_model_time
    on wow_event_predictions (research_run_id, event_key, model_timestamp)
    where model_timestamp is not null;

create or replace function wow_block_event_prediction_post_start_edit()
returns trigger as $$
begin
    if OLD.locked_at is not null then
        raise exception 'wow_event_predictions row % is locked (immutable)', OLD.event_prediction_id;
    end if;
    if OLD.event_start_time <= now() then
        raise exception 'wow_event_predictions row % cannot be edited after event start', OLD.event_prediction_id;
    end if;
    return NEW;
end;
$$ language plpgsql;

drop trigger if exists trg_wow_block_event_prediction_post_start_edit on wow_event_predictions;
create trigger trg_wow_block_event_prediction_post_start_edit
    before update on wow_event_predictions
    for each row execute function wow_block_event_prediction_post_start_edit();

create or replace function wow_block_event_prediction_post_start_delete()
returns trigger as $$
begin
    if OLD.locked_at is not null then
        raise exception 'wow_event_predictions row % is locked and cannot be deleted', OLD.event_prediction_id;
    end if;
    if OLD.event_start_time <= now() then
        raise exception 'wow_event_predictions row % cannot be deleted after event start', OLD.event_prediction_id;
    end if;
    return OLD;
end;
$$ language plpgsql;

drop trigger if exists trg_wow_block_event_prediction_post_start_delete on wow_event_predictions;
create trigger trg_wow_block_event_prediction_post_start_delete
    before delete on wow_event_predictions
    for each row execute function wow_block_event_prediction_post_start_delete();

create table if not exists wow_event_outcomes (
    event_outcome_id uuid primary key default gen_random_uuid(),
    event_prediction_id uuid not null references wow_event_predictions(event_prediction_id),
    created_at timestamptz not null default now(),

    official_winner text,
    home_score integer,
    away_score integer,
    void boolean not null default false,

    settlement_source text,
    settlement_timestamp timestamptz,
    closing_market_home_probability numeric,
    failure_category text,

    constraint uq_wow_event_outcome_prediction unique (event_prediction_id),

    constraint chk_event_outcome_scores_nonnegative
        check (
            (home_score is null or home_score >= 0)
            and (away_score is null or away_score >= 0)
        ),

    constraint chk_event_closing_market_home_probability
        check (
            closing_market_home_probability is null
            or (closing_market_home_probability > 0 and closing_market_home_probability < 1)
        )
);

alter table wow_event_predictions enable row level security;
alter table wow_event_outcomes enable row level security;

comment on table wow_event_predictions is
'Governed WOW team/event probability ledger. MLB full-game OUTRIGHT_WINNER v1. Separate from prop ledger; can_execute is always false.';

comment on table wow_event_outcomes is
'Official settlement rows for wow_event_predictions. One grade per event prediction.';
