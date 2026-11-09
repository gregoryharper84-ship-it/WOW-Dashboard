-- WOW V17 additive migration: make the MLB calibrator evidence row auditable.
--
-- 20261109_mlb_pregame_evidence.sql created wow_mlb_event_calibrator_evidence
-- with the checksum, cohort digest, holdout digest and holdout metrics, but not
-- the fitted numbers themselves or the paired incumbent comparison. A checksum
-- that cannot be recomputed from stored coefficients proves nothing, and a
-- holdout that is never scored against the uncalibrated incumbent is a number
-- without a baseline. This migration adds only what the producer now persists.
--
-- Additive and idempotent: it may be applied before or after the base table is
-- deployed, and it certifies, promotes or publishes nothing.
--
-- Every added column is either provenance the producer derives from its own
-- fitted rows or a metric computed on the holdout. None of them can be supplied
-- by a caller, and none is a verification flag: chronological_replay_verified
-- and calibrated_lower_bound_verified still come only from a CERTIFIED receipt.

alter table public.wow_mlb_event_calibrator_evidence
    add column if not exists calibration_coefficients jsonb not null default '{}'::jsonb,
    add column if not exists calibration_coefficients_sha256 text not null
        default repeat('0', 64),
    add column if not exists holdout_n bigint not null default 0,
    add column if not exists holdout_incumbent_brier double precision,
    add column if not exists holdout_incumbent_log_loss double precision,
    add column if not exists holdout_brier_delta double precision,
    add column if not exists holdout_log_loss_delta double precision,
    add column if not exists holdout_calibrated_worse_n bigint,
    add column if not exists holdout_bounds_lower_mean double precision,
    add column if not exists holdout_bounds_upper_mean double precision,
    add column if not exists holdout_lower_bound_reliability_margin double precision;

do $$
begin
    alter table public.wow_mlb_event_calibrator_evidence
        add constraint chk_mlb_calibrator_coefficients_sha
        check (calibration_coefficients_sha256 ~ '^[0-9a-f]{64}$');
exception
    when duplicate_object then null;
end $$;

do $$
begin
    alter table public.wow_mlb_event_calibrator_evidence
        add constraint chk_mlb_calibrator_holdout_n
        check (holdout_n >= 0);
exception
    when duplicate_object then null;
end $$;

do $$
begin
    alter table public.wow_mlb_event_calibrator_evidence
        add constraint chk_mlb_calibrator_coefficients_present
        check (jsonb_typeof(calibration_coefficients) = 'object');
exception
    when duplicate_object then null;
end $$;

comment on column public.wow_mlb_event_calibrator_evidence.calibration_coefficients is
    'The exact fitted Platt coefficients (a, b) this calibrator was built from; auditable after the fact.';
comment on column public.wow_mlb_event_calibrator_evidence.calibration_coefficients_sha256 is
    'Digest of the stored coefficients; bound into calibrator_checksum so refitted weights cannot reuse it.';
comment on column public.wow_mlb_event_calibrator_evidence.holdout_brier_delta is
    'Calibrated Brier minus uncalibrated (incumbent) Brier on the identical holdout rows. Negative is better.';
comment on column public.wow_mlb_event_calibrator_evidence.holdout_lower_bound_reliability_margin is
    'Realized holdout rate minus the mean declared 10th-percentile lower bound. Negative means the declared floor was optimistic.';
