-- WOW V17 additive migration: make the MLB calibrator evidence row auditable.
--
-- 20261109_mlb_pregame_evidence.sql created wow_mlb_event_calibrator_evidence
-- with the checksum, cohort digest, holdout digest and holdout metrics, but not
-- the fitted numbers, the exact receipt pairs used, or the paired incumbent
-- comparison. A checksum that cannot be recomputed from stored coefficients
-- proves nothing, a digest over receipt ids that are never stored cannot be
-- replayed, and a holdout never scored against the uncalibrated incumbent is a
-- number without a baseline. This migration adds only what the producer
-- persists.
--
-- Required order: 20261109_mlb_pregame_evidence.sql first, then this file.
-- It deliberately does NOT no-op when the base table is absent: a silent no-op
-- lets a migration runner record success without ever adding the columns, and
-- nothing later would notice. An absent base table raises instead, so the
-- runner fails and cannot mark this migration applied. The final block repeats
-- that guarantee by verifying every column and every constraint.
--
-- It certifies, promotes, publishes or grants nothing.

do $$
begin
    if to_regclass('public.wow_mlb_event_calibrator_evidence') is null then
        raise exception
            'wow_mlb_event_calibrator_evidence is absent: apply 20261109_mlb_pregame_evidence.sql before 20261109_mlb_calibrator_provenance.sql';
    end if;

    execute $ddl$
        alter table public.wow_mlb_event_calibrator_evidence
            add column if not exists calibration_coefficients jsonb,
            add column if not exists calibration_coefficients_sha256 text,
            add column if not exists calibration_receipt_manifest jsonb,
            add column if not exists calibration_receipt_manifest_sha256 text,
            add column if not exists holdout_n bigint,
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

    -- Also repair an installation of the earlier placeholder-default version.
    alter table public.wow_mlb_event_calibrator_evidence
        alter column calibration_coefficients drop not null,
        alter column calibration_coefficients drop default,
        alter column calibration_coefficients_sha256 drop not null,
        alter column calibration_coefficients_sha256 drop default,
        alter column calibration_receipt_manifest drop not null,
        alter column calibration_receipt_manifest drop default,
        alter column calibration_receipt_manifest_sha256 drop not null,
        alter column calibration_receipt_manifest_sha256 drop default,
        alter column holdout_n drop not null,
        alter column holdout_n drop default;
    update public.wow_mlb_event_calibrator_evidence set
        calibration_coefficients = nullif(calibration_coefficients, '{}'::jsonb),
        calibration_coefficients_sha256 = nullif(calibration_coefficients_sha256, repeat('0',64)),
        calibration_receipt_manifest = nullif(calibration_receipt_manifest, '{}'::jsonb),
        calibration_receipt_manifest_sha256 = nullif(calibration_receipt_manifest_sha256, repeat('0',64)),
        holdout_n = nullif(holdout_n, 0);
    alter table public.wow_mlb_event_calibrator_evidence
        drop constraint if exists chk_mlb_calibrator_coefficients_present;

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
                add constraint chk_mlb_calibrator_manifest_sha
                check (calibration_receipt_manifest_sha256 ~ '^[0-9a-f]{64}$')
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
                check (jsonb_typeof(calibration_coefficients) = 'object'
                    and calibration_coefficients ? 'a' and calibration_coefficients ? 'b')
        $ddl$;
    exception
        when duplicate_object then null;
    end;

    begin
        execute $ddl$
            alter table public.wow_mlb_event_calibrator_evidence
                add constraint chk_mlb_calibrator_manifest_present
                check (jsonb_typeof(calibration_receipt_manifest) = 'object')
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
        comment on column public.wow_mlb_event_calibrator_evidence.calibration_receipt_manifest is
            'Ordered fit and holdout receipt pairs (event, prediction receipt, settlement receipt) actually used; must be retrievable after the fact.'
    $ddl$;
    execute $ddl$
        comment on column public.wow_mlb_event_calibrator_evidence.calibration_receipt_manifest_sha256 is
            'Digest of the receipt manifest; bound into calibrator_checksum so a different receipt set cannot reuse it.'
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

-- Post-conditions: the runner cannot record success with an un-auditable table.
do $$
declare
    v_missing text;
begin
    select string_agg(required.column_name, ', ' order by required.column_name)
      into v_missing
      from (values
            ('calibration_coefficients'),
            ('calibration_coefficients_sha256'),
            ('calibration_receipt_manifest'),
            ('calibration_receipt_manifest_sha256'),
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

    if (
        select count(*)
          from pg_constraint
         where conrelid = 'public.wow_mlb_event_calibrator_evidence'::regclass
           and conname in (
                'chk_mlb_calibrator_coefficients_sha',
                'chk_mlb_calibrator_manifest_sha',
                'chk_mlb_calibrator_holdout_n',
                'chk_mlb_calibrator_coefficients_present',
                'chk_mlb_calibrator_manifest_present'
               )
    ) <> 5 then
        raise exception 'calibrator provenance migration incomplete: constraints missing';
    end if;
end $$;

create or replace function public.require_mlb_calibrator_provenance()
returns trigger language plpgsql set search_path = pg_catalog, public as $$
begin
    if NEW.calibration_coefficients is null
       or NEW.calibration_coefficients_sha256 is null
       or NEW.calibration_receipt_manifest is null
       or NEW.calibration_receipt_manifest_sha256 is null
       or NEW.holdout_n is null or NEW.holdout_n <= 0
       or NEW.holdout_incumbent_brier is null
       or NEW.holdout_incumbent_log_loss is null
       or NEW.holdout_brier_delta is null
       or NEW.holdout_log_loss_delta is null
       or NEW.holdout_calibrated_worse_n is null
       or NEW.holdout_bounds_lower_mean is null
       or NEW.holdout_bounds_upper_mean is null
       or NEW.holdout_lower_bound_reliability_margin is null then
        raise exception 'Complete calibrator provenance required for new writes';
    end if;
    return NEW;
end $$;
drop trigger if exists require_mlb_calibrator_provenance on public.wow_mlb_event_calibrator_evidence;
create trigger require_mlb_calibrator_provenance before insert
on public.wow_mlb_event_calibrator_evidence for each row
execute function public.require_mlb_calibrator_provenance();
