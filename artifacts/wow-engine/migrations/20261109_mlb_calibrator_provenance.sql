-- WOW V17 additive migration: make the MLB calibrator evidence row auditable.
--
-- 20261109_mlb_pregame_evidence.sql created wow_mlb_event_calibrator_evidence
-- with the checksum, cohort digest, holdout digest and holdout metrics, but not
-- the fitted numbers themselves or the paired incumbent comparison. A checksum
-- that cannot be recomputed from stored coefficients proves nothing, and a
-- holdout that is never scored against the uncalibrated incumbent is a number
-- without a baseline. This migration adds only what the producer now persists.
--
-- Order-safe: every statement is guarded on the base table existing, so this
-- file may be applied before or after 20261109_mlb_pregame_evidence.sql without
-- raising. If it runs first it is a documented no-op and MUST be re-applied
-- after the base table exists; the final block fails loudly if the table exists
-- but the columns are still missing, so a skipped re-apply cannot go unnoticed.
--
-- It certifies, promotes, publishes or grants nothing.

do $$
begin
    if to_regclass('public.wow_mlb_event_calibrator_evidence') is null then
        raise notice 'wow_mlb_event_calibrator_evidence not present yet; skipping calibrator provenance columns. Re-apply this migration after the base table exists.';
        return;
    end if;

    execute $ddl$
        alter table public.wow_mlb_event_calibrator_evidence
            add column if not exists calibration_coefficients jsonb not null
                default '{}'::jsonb,
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
            add column if not exists holdout_lower_bound_reliability_margin
                double precision
    $ddl$;
end $$;

do $$
begin
    if to_regclass('public.wow_mlb_event_calibrator_evidence') is null then
        return;
    end if;

    begin
        execute $ddl$
            alter table public.wow_mlb_event_calibrator_evidence
                add constraint chk_mlb_calibrator_coefficients_sha
                check (calibration_coefficients_sha256 ~ '^[0-9a-f]{64}$')
        $ddl$;
    exception
        when duplicate_object then null;
    end;

    begin
        execute $ddl$
            alter table public.wow_mlb_event_calibrator_evidence
                add constraint chk_mlb_calibrator_holdout_n
                check (holdout_n >= 0)
        $ddl$;
    exception
        when duplicate_object then null;
    end;

    begin
        execute $ddl$
            alter table public.wow_mlb_event_calibrator_evidence
                add constraint chk_mlb_calibrator_coefficients_present
                check (jsonb_typeof(calibration_coefficients) = 'object')
        $ddl$;
    exception
        when duplicate_object then null;
    end;

    execute $ddl$
        comment on column public.wow_mlb_event_calibrator_evidence.calibration_coefficients is
            'The exact fitted Platt coefficients (a, b) this calibrator was built from; auditable after the fact.'
    $ddl$;
    execute $ddl$
        comment on column public.wow_mlb_event_calibrator_evidence.calibration_coefficients_sha256 is
            'Digest of the stored coefficients; bound into calibrator_checksum so refitted weights cannot reuse it.'
    $ddl$;
    execute $ddl$
        comment on column public.wow_mlb_event_calibrator_evidence.holdout_brier_delta is
            'Calibrated Brier minus uncalibrated (incumbent) Brier on the identical holdout rows. Negative is better.'
    $ddl$;
    execute $ddl$
        comment on column public.wow_mlb_event_calibrator_evidence.holdout_lower_bound_reliability_margin is
            'Realized holdout rate minus the mean declared 10th-percentile diagnostic floor. Negative means the declared floor was optimistic.'
    $ddl$;
end $$;

-- Fail loudly if the table exists but this migration has not actually been
-- applied, instead of leaving a silently un-auditable table behind.
do $$
declare
    v_missing text;
begin
    if to_regclass('public.wow_mlb_event_calibrator_evidence') is null then
        raise notice 'calibrator provenance verification skipped: base table absent.';
        return;
    end if;

    select string_agg(required.column_name, ', ' order by required.column_name)
      into v_missing
      from (values
            ('calibration_coefficients'),
            ('calibration_coefficients_sha256'),
            ('holdout_n'),
            ('holdout_incumbent_brier'),
            ('holdout_incumbent_log_loss'),
            ('holdout_brier_delta'),
            ('holdout_log_loss_delta'),
            ('holdout_calibrated_worse_n'),
            ('holdout_bounds_lower_mean'),
            ('holdout_bounds_upper_mean'),
            ('holdout_lower_bound_reliability_margin')
           ) as required(column_name)
     where not exists (
            select 1
              from information_schema.columns c
             where c.table_schema = 'public'
               and c.table_name = 'wow_mlb_event_calibrator_evidence'
               and c.column_name = required.column_name
           );

    if v_missing is not null then
        raise exception 'calibrator provenance migration incomplete: missing columns %', v_missing;
    end if;
end $$;
