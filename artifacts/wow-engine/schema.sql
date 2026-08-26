-- WOW EXTERNAL_GOVERNED_BACKEND — Supabase schema
-- Per WOW-PATCH-2026-08-26-FREE-HOST-PROBABILITY-ENGINE v2, Section 8B.2

create table if not exists wow_predictions (
    prediction_id           uuid primary key default gen_random_uuid(),
    created_at              timestamptz not null default now(),
    event_id                text not null,
    event_start_time        timestamptz not null,

    player                  text,
    team                    text,
    opponent                text,
    sport                   text not null,

    market_type             text not null,
    stat_type               text not null,
    line                    numeric not null,
    direction               text not null check (direction in ('MORE','LESS')),

    -- Failure-path (8B.1)
    regime_model_version    text,
    regime_probabilities_json jsonb,
    regime_probability_sum  numeric,
    primary_failure_path    text,
    failure_cause_tags      text[] default '{}',
    simulation_seed         bigint,
    simulation_draws        integer,

    -- Probability (8B.1 / 8B.2)
    raw_model_probability          numeric,
    independent_model_probability  numeric,
    effective_sample_size          numeric,

    -- Market prior (8B.3)
    market_prior_available     boolean default false,
    market_prior_probability   numeric,
    market_prior_quality       text,
    market_prior_weight        numeric default 0.00,
    market_prior_weight_source text,
    reference_market_probability_raw numeric,
    reference_market_side      text,
    reference_market_price     numeric,
    market_timestamp           timestamptz,

    -- Calibration (8B.4)
    calibration_status       text,   -- PRECALIBRATION_SHRINKAGE | PLATT_TIME_SPLIT_V1 | ISOTONIC_V1
    calibration_method       text,
    calibration_version      text,
    calibration_training_n   integer,
    calibration_parent_cohort text,
    calibration_fit_start    timestamptz,
    calibration_fit_end      timestamptz,

    calibrated_probability            numeric,
    calibrated_probability_lower_bound numeric,
    calibrated_probability_upper_bound numeric,

    -- Governance / publication
    probability_publishable  boolean not null default false,
    probability_ceiling      text,   -- e.g. RESEARCH_INTEREST, MODEL_QUALIFIED_HOLD
    money_lane_status        text,   -- PAYOUT_UNRESOLVED | RESOLVED
    data_gaps                text[] default '{}',
    blockers                 text[] default '{}',

    source_snapshot_id       uuid not null,

    -- Immutability enforcement (constraint 8B.2 + gate #8)
    locked_at                timestamptz
);

-- Hard constraints per 8B.2
alter table wow_predictions
    add constraint chk_regime_sum
    check (regime_probability_sum is null or abs(regime_probability_sum - 1.0) <= 0.000001);

alter table wow_predictions
    add constraint chk_raw_prob_bounds
    check (raw_model_probability is null or (raw_model_probability > 0 and raw_model_probability < 1));

-- NOTE: the original version of this constraint had a NULL-logic gap.
-- Postgres CHECK constraints treat a NULL result as "not violated", so
-- "calibrated_probability is null OR (bounds...)" would silently PASS a
-- row where calibrated_probability was set but lower_bound/upper_bound
-- were NULL, because "lower_bound > 0" evaluates to NULL, not false.
-- This version requires all three fields to be simultaneously present
-- or simultaneously absent, closing that gap.
alter table wow_predictions
    add constraint chk_calibrated_bounds
    check (
        (
            calibrated_probability is null
            and calibrated_probability_lower_bound is null
            and calibrated_probability_upper_bound is null
        )
        or (
            calibrated_probability is not null
            and calibrated_probability_lower_bound is not null
            and calibrated_probability_upper_bound is not null
            and calibrated_probability_lower_bound > 0
            and calibrated_probability_lower_bound <= calibrated_probability
            and calibrated_probability <= calibrated_probability_upper_bound
            and calibrated_probability_upper_bound < 1
        )
    );

alter table wow_predictions
    add constraint chk_sim_draws
    check (simulation_draws is null or simulation_draws >= 50000);

-- Immutability trigger: block any UPDATE once event_start_time has passed
create or replace function wow_block_post_event_edit()
returns trigger as $$
begin
    if OLD.locked_at is not null then
        raise exception 'wow_predictions row % is locked (immutable)', OLD.prediction_id;
    end if;
    if OLD.event_start_time <= now() then
        raise exception 'wow_predictions row % cannot be edited after event start', OLD.prediction_id;
    end if;
    return NEW;
end;
$$ language plpgsql;

drop trigger if exists trg_wow_block_post_event_edit on wow_predictions;
create trigger trg_wow_block_post_event_edit
    before update on wow_predictions
    for each row execute function wow_block_post_event_edit();

-- The original schema only blocked UPDATE. An immutable ledger must also
-- block DELETE once a row is locked or its event has started — otherwise
-- "immutable" only means "can't be edited, but can be erased," which is
-- not the same guarantee.
create or replace function wow_block_post_event_delete()
returns trigger as $$
begin
    if OLD.locked_at is not null then
        raise exception 'wow_predictions row % is locked and cannot be deleted', OLD.prediction_id;
    end if;
    if OLD.event_start_time <= now() then
        raise exception 'wow_predictions row % cannot be deleted after event start', OLD.prediction_id;
    end if;
    return OLD;
end;
$$ language plpgsql;

drop trigger if exists trg_wow_block_post_event_delete on wow_predictions;
create trigger trg_wow_block_post_event_delete
    before delete on wow_predictions
    for each row execute function wow_block_post_event_delete();

create table if not exists wow_outcomes (
    outcome_id              uuid primary key default gen_random_uuid(),
    prediction_id           uuid not null references wow_predictions(prediction_id),

    official_result          text,
    actual_stat               numeric,
    hit                       boolean,
    push                      boolean default false,
    void                      boolean default false,

    settlement_source         text,
    settlement_timestamp      timestamptz,
    closing_market_probability numeric,
    failure_category           text,

    created_at                timestamptz not null default now()
);

-- Separate, structurally isolated manual-estimate tracking (Section 8A.6)
-- NEVER pooled with wow_predictions / wow_outcomes above.
create table if not exists wow_manual_estimates (
    manual_estimate_id       uuid primary key default gen_random_uuid(),
    created_at                timestamptz not null default now(),
    player_team               text,
    sport                     text,
    prop_market                text,
    sample_n                   integer,
    l10_status                 text,
    market_type                text,
    no_vig_market_prob          numeric,
    directional_result          text check (directional_result in
        ('RESEARCH_SUPPORTS_MORE','RESEARCH_SUPPORTS_LESS','MIXED','INSUFFICIENT_EVIDENCE')),
    actual_result                text,
    direction_supported          text check (direction_supported in ('Yes','No','N/A')),
    notes                        text
);

comment on table wow_predictions is 'Governed WOW probability ledger. Immutable after event_start_time. Never pooled with wow_manual_estimates (Section 8A.6).';
comment on table wow_manual_estimates is 'Section 8A Manual Estimate Lane tracking. Structurally isolated from wow_predictions/wow_outcomes calibration data.';

-- Persisted Phase B (Platt) / Phase C (isotonic) calibrator artifacts
-- (8B.4). wow_predictions.calibration_status/method/version/training_n
-- record which calibrator PRODUCED a given row; this table is the
-- calibrator itself -- fitted coefficients / model artifact, fit cohort,
-- training window, and fit metrics -- so it survives a service restart
-- instead of living only in the in-memory PlattFitOutcome that produced
-- it. A fit is never mutated in place: promoting a new candidate inserts
-- a new row and deactivates the previous one for that (cohort, method)
-- pair, preserving fit history for audit.
create table if not exists wow_calibrators (
    calibrator_id        uuid primary key default gen_random_uuid(),
    created_at            timestamptz not null default now(),
    -- Distinct from created_at (row-insert time): the moment the fit
    -- itself completed. In this implementation the two happen back to
    -- back, but the reviewer's persisted-record requirements name both
    -- separately, so both are recorded rather than conflated.
    fitted_at              timestamptz not null default now(),

    -- "phase" alongside calibration_method: the method string can gain a
    -- new version (e.g. PLATT_TIME_SPLIT_V2) without the phase changing,
    -- so callers that only care "is this a Phase B or Phase C row" don't
    -- have to parse the method string.
    phase                  text not null check (phase in ('PHASE_B','PHASE_C')),
    calibration_method    text not null check (calibration_method in ('PLATT_TIME_SPLIT_V1','ISOTONIC_V1')),
    calibration_version   text not null,
    parent_cohort         text not null,

    training_n            integer not null check (training_n > 0),
    fit_start              timestamptz,
    fit_end                 timestamptz,
    fit_metrics_json         jsonb not null,   -- brier / log_loss / ece / calibration_bias

    -- Phase B (Platt): the two fitted scalar coefficients.
    platt_a                numeric,
    platt_b                 numeric,

    -- Phase C (isotonic): serialized fitted model artifact (base64), plus
    -- the walk-forward audit trail proving no future-fold leakage.
    isotonic_artifact_b64    text,
    fold_train_audit_json     jsonb,

    -- Version of the per-candidate predictive-bounds method (8B.4
    -- PREDICTIVE_BOUNDS_V1 amendment) this calibrator is eligible to be
    -- scored under. Recorded per-calibrator so a future bounds-method
    -- revision can be told apart from calibrators fit before it existed.
    bounds_method_version    text,

    promoted                boolean not null default false,
    active                   boolean not null default false,

    check (
        (phase = 'PHASE_B') = (calibration_method = 'PLATT_TIME_SPLIT_V1')
    ),
    check (
        (phase = 'PHASE_C') = (calibration_method = 'ISOTONIC_V1')
    ),
    check (
        (calibration_method = 'PLATT_TIME_SPLIT_V1' and platt_a is not null and platt_b is not null and isotonic_artifact_b64 is null)
        or
        (calibration_method = 'ISOTONIC_V1' and isotonic_artifact_b64 is not null and platt_a is null and platt_b is null)
    )
);

create unique index if not exists uq_wow_calibrators_one_active_per_cohort_method
    on wow_calibrators (parent_cohort, calibration_method)
    where active;

comment on table wow_calibrators is 'Persisted Phase B/C calibrator artifacts (8B.4 + ratified PREDICTIVE_BOUNDS_V1 amendment). At most one active row per (parent_cohort, calibration_method) -- enforced by the partial unique index -- so score_prop_end_to_end always loads an unambiguous calibrator.';
